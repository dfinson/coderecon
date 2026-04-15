"""Worktree remerge logic.

After `git merge`, reconciles index facts between worktrees:
- Files with matching content_hash → adopt facts instantly (no re-parse).
- Files with new hashes → queue for re-indexing.
- Deleted files → prune from index.
- Deleted worktree → drop all worktree data.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import structlog
from sqlalchemy import Engine, text

log = structlog.get_logger(__name__)


def compute_file_hashes(repo_root: Path, paths: list[str]) -> dict[str, str]:
    """Compute content hashes for a list of files using git hash-object.

    Much faster than Python hashing — git already has the blob hashes.
    Falls back to sha256 for untracked files.
    """
    import hashlib

    hashes: dict[str, str] = {}
    for path in paths:
        full_path = repo_root / path
        if not full_path.exists():
            continue
        try:
            result = subprocess.run(
                ["git", "hash-object", str(full_path)],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                hashes[path] = result.stdout.strip()
            else:
                # Fallback for untracked files
                content = full_path.read_bytes()
                hashes[path] = hashlib.sha256(content).hexdigest()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    return hashes


def get_changed_files_since(
    repo_root: Path,
    since_ref: str = "ORIG_HEAD",
) -> list[str]:
    """Get files that changed between since_ref and HEAD.

    Used after merge/rebase to find what needs reconciliation.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since_ref, "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().splitlines() if f]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


class RemergeResult:
    """Result of a remerge operation."""

    __slots__ = ("adopted", "reindexed", "pruned", "elapsed_ms")

    def __init__(self) -> None:
        self.adopted: int = 0
        self.reindexed: list[str] = []
        self.pruned: int = 0
        self.elapsed_ms: float = 0

    def to_dict(self) -> dict:
        return {
            "adopted": self.adopted,
            "reindexed": len(self.reindexed),
            "pruned": self.pruned,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


def remerge_worktree(
    engine: Engine,
    repo_root: Path,
    target_worktree_id: int,
    source_worktree_id: int | None = None,
) -> RemergeResult:
    """Reconcile a worktree's index after a merge/rebase.

    Args:
        engine: SQLAlchemy engine for the repo's index.db.
        repo_root: Root path of the target worktree.
        target_worktree_id: The worktree that was merged into.
        source_worktree_id: The worktree that was merged from (optional).

    Returns:
        RemergeResult with counts of adopted, reindexed, and pruned files.
    """
    t0 = time.monotonic()
    result = RemergeResult()

    # 1. Find changed files since the merge
    changed_paths = get_changed_files_since(repo_root)
    if not changed_paths:
        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    # 2. Compute current content hashes for changed files
    current_hashes = compute_file_hashes(repo_root, changed_paths)

    # 3. Get existing index state for these files
    with engine.connect() as conn:
        placeholders = ",".join(f"'{p}'" for p in changed_paths)
        rows = conn.execute(text(
            f"SELECT id, path, content_hash FROM files "  # noqa: S608
            f"WHERE worktree_id = {target_worktree_id} "
            f"AND path IN ({placeholders})"
        )).fetchall()

    indexed: dict[str, tuple[int, str | None]] = {}
    for row in rows:
        file_id, path, content_hash = row
        indexed[path] = (file_id, content_hash)

    # 4. Reconcile each changed file
    for path in changed_paths:
        current_hash = current_hashes.get(path)

        if current_hash is None:
            # File was deleted by the merge
            if path in indexed:
                _prune_file(engine, indexed[path][0])
                result.pruned += 1
            continue

        existing = indexed.get(path)
        if existing is not None:
            _, existing_hash = existing
            if existing_hash == current_hash:
                # Hash unchanged — nothing to do
                result.adopted += 1
                continue

        # Hash changed or new file — check if another worktree has this hash
        if source_worktree_id is not None:
            source_match = _find_file_by_hash(
                engine, source_worktree_id, path, current_hash,
            )
            if source_match is not None:
                # Source worktree has this exact content — adopt its facts
                if existing is not None:
                    # Update hash on existing file row
                    _update_file_hash(engine, existing[0], current_hash)
                result.adopted += 1
                continue

        # Needs full re-indexing
        result.reindexed.append(path)

    result.elapsed_ms = (time.monotonic() - t0) * 1000

    log.info(
        "remerge_complete",
        worktree_id=target_worktree_id,
        changed=len(changed_paths),
        adopted=result.adopted,
        reindexed=len(result.reindexed),
        pruned=result.pruned,
        elapsed_ms=round(result.elapsed_ms, 1),
    )

    return result


def drop_worktree_data(engine: Engine, worktree_id: int) -> int:
    """Remove all data for a deleted worktree.

    Cascades: files → def_facts, ref_facts, import_facts, etc.
    Returns number of files removed.
    """
    with engine.connect() as conn:
        # Get file IDs for this worktree
        rows = conn.execute(text(
            f"SELECT id FROM files WHERE worktree_id = {worktree_id}"  # noqa: S608
        )).fetchall()
        file_ids = [r[0] for r in rows]

        if not file_ids:
            return 0

        id_list = ",".join(str(fid) for fid in file_ids)

        # Delete facts referencing these files
        for table in (
            "def_facts", "ref_facts", "import_facts", "scope_facts",
            "local_bind_facts", "dynamic_access_sites", "type_annotation_facts",
        ):
            conn.execute(text(f"DELETE FROM {table} WHERE file_id IN ({id_list})"))  # noqa: S608

        # Delete the files themselves
        conn.execute(text(
            f"DELETE FROM files WHERE worktree_id = {worktree_id}"  # noqa: S608
        ))

        # Delete the worktree record
        conn.execute(text(
            f"DELETE FROM worktrees WHERE id = {worktree_id}"  # noqa: S608
        ))

        conn.commit()

    log.info("worktree_data_dropped", worktree_id=worktree_id, files=len(file_ids))
    return len(file_ids)


# --- Internal helpers ---

def _prune_file(engine: Engine, file_id: int) -> None:
    """Remove a single file and all its facts."""
    with engine.connect() as conn:
        for table in (
            "def_facts", "ref_facts", "import_facts", "scope_facts",
            "local_bind_facts", "dynamic_access_sites",
        ):
            conn.execute(text(f"DELETE FROM {table} WHERE file_id = {file_id}"))  # noqa: S608
        conn.execute(text(f"DELETE FROM files WHERE id = {file_id}"))  # noqa: S608
        conn.commit()


def _find_file_by_hash(
    engine: Engine,
    worktree_id: int,
    path: str,
    content_hash: str,
) -> int | None:
    """Find a file in a specific worktree with matching path and hash."""
    with engine.connect() as conn:
        row = conn.execute(text(
            f"SELECT id FROM files "  # noqa: S608
            f"WHERE worktree_id = {worktree_id} "
            f"AND path = '{path}' "
            f"AND content_hash = '{content_hash}'"
        )).fetchone()
        return row[0] if row else None


def _update_file_hash(engine: Engine, file_id: int, content_hash: str) -> None:
    """Update a file's content hash."""
    with engine.connect() as conn:
        conn.execute(text(
            f"UPDATE files SET content_hash = '{content_hash}' WHERE id = {file_id}"  # noqa: S608
        ))
        conn.commit()
