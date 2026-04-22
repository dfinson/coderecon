"""Batch-index all registered-but-empty worktrees.

For each main repo's index.db, finds worktrees with 0 indexed files,
runs ``git diff main...HEAD`` to discover changed files, and calls
``reindex_incremental()`` to populate files + def_facts.

This is a one-shot backfill for worktrees that were registered before
the prod fix that queues diff-reindex at registration time.
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
    # Two cursors: one for iteration, one for the inner COUNT query
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


def _git_diff_vs_main(wt_path: Path) -> list[str]:
    """Return repo-relative paths that differ from main."""
    try:
        result = subprocess.run(
            ["git", "-C", str(wt_path), "diff", "--name-only", "main...HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            return []
        return [l for l in result.stdout.splitlines() if l]
    except Exception:
        return []


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

    ok = skip = fail = 0
    for wt_id, wt_name, wt_root_path in empty_wts:
        wt_path = Path(wt_root_path)
        if not wt_path.is_dir():
            skip += 1
            continue

        diff_files = _git_diff_vs_main(wt_path)
        if not diff_files:
            skip += 1
            continue

        # Ensure worktree row + root cache are set
        coordinator._get_or_create_worktree_id(wt_name, root_path=wt_root_path)
        coordinator._worktree_root_cache[wt_name] = wt_path

        abs_paths = [wt_path / p for p in diff_files]
        try:
            stats = await coordinator.reindex_incremental(abs_paths, worktree=wt_name)
            ok += 1
            if stats.files_added > 0:
                print(
                    f"    {wt_name}: {stats.files_added} files, "
                    f"{stats.symbols_indexed} symbols"
                )
        except Exception as exc:
            fail += 1
            print(f"    {wt_name}: ERROR {exc}")

    # Flush any pending tantivy writes
    try:
        coordinator.tantivy_writer_commit()
    except Exception:
        pass

    return ok, skip, fail


async def main() -> None:
    # Discover all repos with index.db
    repo_roots: list[Path] = []
    for set_dir in sorted(CLONES_DIR.iterdir()):
        if set_dir.name == "instances" or not set_dir.is_dir():
            continue
        for repo_dir in sorted(set_dir.iterdir()):
            idx = repo_dir / ".recon" / "index.db"
            if idx.exists():
                repo_roots.append(repo_dir)

    print(f"Found {len(repo_roots)} repos with index.db")

    # Also check for unregistered worktrees from pr_instances.jsonl
    pr_file = DATA_DIR / "pr_instances.jsonl"
    if pr_file.exists():
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from cpl_lab.pipeline.clone import clone_dir_for

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
                    # Pre-register this worktree
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
            print(f"Pre-registered {unregistered} previously unregistered worktrees")

    total_ok = total_skip = total_fail = 0
    t0 = time.monotonic()

    for i, repo_root in enumerate(repo_roots, 1):
        print(f"\n[{i}/{len(repo_roots)}] {repo_root.name}")
        ok, skip, fail = await _process_repo(repo_root)
        total_ok += ok
        total_skip += skip
        total_fail += fail
        if ok + skip + fail > 0:
            print(f"  => {ok} indexed, {skip} skipped, {fail} failed")

    elapsed = time.monotonic() - t0
    print(f"\n{'='*60}")
    print(f"DONE in {elapsed:.0f}s")
    print(f"  Indexed:  {total_ok}")
    print(f"  Skipped:  {total_skip}")
    print(f"  Failed:   {total_fail}")


if __name__ == "__main__":
    asyncio.run(main())
