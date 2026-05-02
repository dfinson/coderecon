"""Batch-index all worktrees via the CodeRecon SDK.

Spawns the daemon, registers each repo + worktree, then calls
``sdk.reindex(repo, worktree=name)`` which does the right thing:
main worktrees get a full reindex, non-main worktrees get an
incremental diff-based reindex automatically.
"""

from __future__ import annotations

import asyncio
import json
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


async def main() -> None:
    from coderecon.sdk import CodeRecon, CodeReconError

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from recon_lab.pipeline.clone import clone_dir_for

    # Discover all repos with .recon/index.db (already indexed base repos)
    repo_roots: list[Path] = []
    for set_dir in sorted(CLONES_DIR.iterdir()):
        if set_dir.name == "instances" or not set_dir.is_dir():
            continue
        for repo_dir in sorted(set_dir.iterdir()):
            if (repo_dir / ".recon" / "index.db").exists():
                repo_roots.append(repo_dir)

    print(f"Found {len(repo_roots)} repos with index.db", flush=True)

    # Build worktree map: repo_root → list of (wt_name, wt_path)
    worktree_map: dict[Path, list[tuple[str, Path]]] = {r: [] for r in repo_roots}

    pr_file = DATA_DIR / "pr_instances.jsonl"
    if pr_file.exists():
        for line in pr_file.read_text().splitlines():
            if not line.strip():
                continue
            inst = json.loads(line)
            iid = inst["instance_id"]
            rid = inst["repo_id"]
            mc = clone_dir_for(rid, CLONES_DIR)
            if mc is None or mc not in worktree_map:
                continue
            wt_path = INSTANCES_DIR / iid
            if wt_path.is_dir():
                worktree_map[mc].append((iid, wt_path))

    total_wts = sum(len(wts) for wts in worktree_map.values())
    print(f"Total worktrees to index: {total_wts}", flush=True)

    async with CodeRecon() as sdk:
        # Register all repos (ensures they're in the daemon catalog)
        for repo_root in repo_roots:
            await sdk.register(repo_root)

        # Register + reindex each worktree
        total_ok = total_skip = total_fail = 0
        t0 = time.monotonic()

        for i, repo_root in enumerate(repo_roots, 1):
            wts = worktree_map[repo_root]
            if not wts:
                continue
            repo_name = repo_root.name
            print(f"\n[{i}/{len(repo_roots)}] {repo_name} ({len(wts)} worktrees)",
                  flush=True)

            for wt_name, wt_path in wts:
                try:
                    # Register worktree path so daemon knows about it
                    await sdk.register(wt_path)
                    await sdk.reindex(repo_name, worktree=wt_name)
                    total_ok += 1
                    print(f"    {wt_name}: OK", flush=True)
                except CodeReconError as exc:
                    if "no diff" in str(exc).lower() or "0 files" in str(exc).lower():
                        total_skip += 1
                        print(f"    {wt_name}: SKIP (no diff)", flush=True)
                    else:
                        total_fail += 1
                        print(f"    {wt_name}: FAILED {exc}", flush=True)
                except Exception as exc:
                    total_fail += 1
                    print(f"    {wt_name}: ERROR {exc}", flush=True)

        elapsed = time.monotonic() - t0
        print(f"\n{'='*60}", flush=True)
        print(f"DONE in {elapsed:.0f}s", flush=True)
        print(f"  Indexed:  {total_ok}", flush=True)
        print(f"  Skipped:  {total_skip}", flush=True)
        print(f"  Failed:   {total_fail}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
