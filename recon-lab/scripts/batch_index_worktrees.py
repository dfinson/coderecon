"""Batch-index all registered-but-empty worktrees.

Mirrors the prod daemon's worktree indexing flow:

1. For each repo's index.db, find worktrees with 0 indexed files.
2. For each empty worktree, diff its content against main
   (``git diff --name-only main HEAD`` — two-arg content diff, NOT
   the three-dot merge-base diff that prod uses for active branches).
3. Call the full ``reindex_incremental()`` for every differing file so
   that ALL index data is populated: files, def_facts, tantivy, SPLADE
   vectors, cross-file resolution, semantic passes.

Lab worktrees are checked out at ``base_commit`` (already-merged,
ancestor of main).  Three-dot diff doesn't work for these because the
merge-base IS the checkout commit.  Two-arg diff compares tree content
directly, which correctly finds all files that differ.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Ensure coderecon is importable (editable install in .venv)
SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


CLONES_DIR = Path.home() / ".recon/recon-lab/clones"
DATA_DIR = Path.home() / ".recon/recon-lab/data"
INSTANCES_DIR = CLONES_DIR / "instances"


def _find_empty_worktrees(idx_path: Path) -> list[tuple[int, str, str]]:
    """Return (wt_id, name, root_path) for worktrees with 0 indexed files."""
    con = sqlite3.connect(str(idx_path))
    cur_outer = con.cursor()
    cur_inner = con.cursor()
    empty: list[tuple[int, str, str]] = []
    for wt_id, name, root_path, is_main in cur_outer.execute(
        "SELECT id, name, root_path, is_main FROM worktrees"
    ):
        if is_main:
            continue
        fc = cur_inner.execute(
            "SELECT COUNT(*) FROM files WHERE worktree_id = ?", (wt_id,)
        ).fetchone()[0]
        if fc == 0:
            empty.append((wt_id, name, root_path))
    con.close()
    return empty


def _git_diff_vs_default(wt_path: Path, default_branch: str) -> list[str]:
    """Return repo-relative paths whose content differs between *default_branch* and HEAD.

    Uses two-arg ``git diff --name-only <branch> HEAD`` (content diff).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(wt_path), "diff", "--name-only", default_branch, "HEAD"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            return []
        return [l for l in result.stdout.splitlines() if l]
    except Exception:
        return []


def _detect_default_branch(repo_root: Path) -> str:
    """Resolve the default branch name for a repo clone."""
    # 1. Current branch of the main worktree
    r = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()

    # 2. origin/HEAD
    r = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if r.returncode == 0:
        return r.stdout.strip().rsplit("/", 1)[-1]

    # 3. Probe common names
    for candidate in ("main", "master"):
        r = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", candidate],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if r.returncode == 0:
            return candidate

    return "main"


async def _process_repo(repo_root: Path) -> tuple[int, int, int]:
    """Index all empty worktrees for one repo.  Returns (ok, skip, fail)."""
    from coderecon.index.ops import IndexCoordinatorEngine

    idx_path = repo_root / ".recon" / "index.db"
    tantivy_path = repo_root / ".recon" / "tantivy"

    empty_wts = _find_empty_worktrees(idx_path)
    if not empty_wts:
        return 0, 0, 0

    coordinator = IndexCoordinatorEngine(
        repo_root=repo_root,
        db_path=idx_path,
        tantivy_path=tantivy_path,
    )
    await coordinator.load_existing()

    default_branch = _detect_default_branch(repo_root)

    ok = skip = fail = 0
    for wt_id, wt_name, wt_root_path in empty_wts:
        wt_path = Path(wt_root_path)
        if not wt_path.is_dir():
            skip += 1
            continue

        diff_files = _git_diff_vs_default(wt_path, default_branch)
        if not diff_files:
            skip += 1
            continue

        # Ensure worktree row + root cache are set so reindex_incremental
        # reads files from the correct checkout directory.
        coordinator._get_or_create_worktree_id(wt_name, root_path=wt_root_path)
        coordinator._worktree_root_cache[wt_name] = wt_path

        abs_paths = [wt_path / p for p in diff_files]
        try:
            stats = await coordinator.reindex_incremental(abs_paths, worktree=wt_name)
            ok += 1
            print(
                f"    {wt_name}: {stats.files_added}+{stats.files_updated} files, "
                f"{stats.symbols_indexed} symbols, {stats.duration_seconds:.1f}s",
                flush=True,
            )
        except Exception as exc:
            fail += 1
            print(f"    {wt_name}: ERROR {exc}", flush=True)

    return ok, skip, fail


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cpl_lab.pipeline.clone import clone_dir_for

    # Discover all repos with index.db
    repo_roots: list[Path] = []
    for set_dir in sorted(CLONES_DIR.iterdir()):
        if set_dir.name == "instances" or not set_dir.is_dir():
            continue
        for repo_dir in sorted(set_dir.iterdir()):
            idx = repo_dir / ".recon" / "index.db"
            if idx.exists():
                repo_roots.append(repo_dir)

    print(f"Found {len(repo_roots)} repos with index.db", flush=True)

    # Pre-register any unregistered worktrees from pr_instances.jsonl
    pr_file = DATA_DIR / "pr_instances.jsonl"
    if pr_file.exists():
        registered_names: dict[Path, set[str]] = {}
        for rr in repo_roots:
            idx = rr / ".recon" / "index.db"
            con = sqlite3.connect(str(idx))
            names = {r[0] for r in con.execute("SELECT name FROM worktrees").fetchall()}
            con.close()
            registered_names[rr] = names

        unregistered = 0
        for line in pr_file.read_text().splitlines():
            if not line.strip():
                continue
            inst = json.loads(line)
            iid = inst["instance_id"]
            rid = inst["repo_id"]
            mc = clone_dir_for(rid, CLONES_DIR)
            if mc is None:
                continue
            if iid not in registered_names.get(mc, set()):
                wt_path = INSTANCES_DIR / iid
                if wt_path.is_dir():
                    idx = mc / ".recon" / "index.db"
                    con = sqlite3.connect(str(idx))
                    con.execute(
                        "INSERT OR IGNORE INTO worktrees (name, root_path, is_main) "
                        "VALUES (?, ?, 0)",
                        (iid, str(wt_path)),
                    )
                    con.commit()
                    con.close()
                    registered_names.setdefault(mc, set()).add(iid)
                    unregistered += 1

        if unregistered:
            print(f"Pre-registered {unregistered} previously unregistered worktrees",
                  flush=True)

    total_ok = total_skip = total_fail = 0
    t0 = time.monotonic()

    for i, repo_root in enumerate(repo_roots, 1):
        print(f"\n[{i}/{len(repo_roots)}] {repo_root.name}", flush=True)
        ok, skip, fail = await _process_repo(repo_root)
        total_ok += ok
        total_skip += skip
        total_fail += fail
        if ok + skip + fail > 0:
            print(f"  => {ok} indexed, {skip} skipped, {fail} failed", flush=True)

    elapsed = time.monotonic() - t0
    print(f"\n{'='*60}", flush=True)
    print(f"DONE in {elapsed:.0f}s", flush=True)
    print(f"  Indexed:  {total_ok}", flush=True)
    print(f"  Skipped:  {total_skip}", flush=True)
    print(f"  Failed:   {total_fail}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
