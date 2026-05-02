"""Overnight full pipeline: reindex → worktrees → pr-import → collect → merge → train.

Phase 1: Full reindex of all 96 base repos (eval, ranker-gate, cutoff)
Phase 2: Incremental worktree indexing for all instances.
Phase 3: PR import (GT defs + LLM query generation)
Phase 4: Non-OK query generation
Phase 5: Signal collection
Phase 6: Merge + Train

Usage:
    python scripts/overnight_reindex_all.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

LAB_SRC = Path(__file__).resolve().parents[1] / "src"
if str(LAB_SRC) not in sys.path:
    sys.path.insert(0, str(LAB_SRC))

from recon_lab.pipeline.index import _iter_clones, _recon_init_cmd

CLONES_DIR = Path.home() / ".recon/recon-lab/clones"
REPO_SETS = ["eval", "ranker-gate", "cutoff"]
TIMEOUT_PER_REPO = 2400  # 40 min
PARALLEL = 1  # sequential: single repo saturates GPU + all CPU cores


async def _index_one(repo_dir: Path, sem: asyncio.Semaphore) -> str:
    """Index a single repo, returning a status line."""
    rel = f"{repo_dir.parent.name}/{repo_dir.name}"
    async with sem:
        cmd, env = _recon_init_cmd(repo_dir, reindex=True)
        t0 = time.monotonic()
        print(f"  START {rel}", flush=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=TIMEOUT_PER_REPO
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                elapsed = time.monotonic() - t0
                msg = f"  TIMEOUT {rel} ({elapsed:.0f}s)"
                print(msg, flush=True)
                return msg

            elapsed = time.monotonic() - t0
            if proc.returncode == 0:
                msg = f"  OK {rel} ({elapsed:.0f}s)"
            else:
                err_snippet = (stderr or b"").decode(errors="replace")[:200]
                msg = f"  FAILED {rel} exit={proc.returncode} ({elapsed:.0f}s) {err_snippet}"
            print(msg, flush=True)
            return msg
        except Exception as exc:
            elapsed = time.monotonic() - t0
            msg = f"  ERROR {rel} ({elapsed:.0f}s): {exc}"
            print(msg, flush=True)
            return msg


async def phase1_base_repos() -> None:
    """Full reindex all base repos with parallelism."""
    print("=" * 60, flush=True)
    print(f"PHASE 1: Full reindex of base repos (parallel={PARALLEL})", flush=True)
    print("=" * 60, flush=True)

    # Collect all repo dirs across sets (skip instances/)
    all_repos: list[Path] = []
    for repo_set in REPO_SETS:
        all_repos.extend(_iter_clones(CLONES_DIR, repo_set))

    print(f"Total repos to index: {len(all_repos)}", flush=True)

    sem = asyncio.Semaphore(PARALLEL)
    t0 = time.monotonic()

    results = await asyncio.gather(
        *[_index_one(r, sem) for r in all_repos]
    )

    elapsed = time.monotonic() - t0
    ok = sum(1 for r in results if "  OK " in r)
    failed = len(results) - ok
    print(f"\nPhase 1 complete in {elapsed:.0f}s ({elapsed/3600:.1f}h)", flush=True)
    print(f"  OK: {ok}  Failed/Timeout: {failed}", flush=True)


async def phase2_worktrees() -> None:
    """Incremental index all worktrees."""
    print("\n" + "=" * 60, flush=True)
    print("PHASE 2: Incremental worktree indexing", flush=True)
    print("=" * 60, flush=True)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from batch_index_worktrees import main as batch_main
    await batch_main()


def _run_cli(*args: str, timeout: int = 7200) -> None:
    """Run a recon-lab CLI command as a subprocess."""
    cmd = [sys.executable, "-m", "recon_lab.cli", *args]
    print(f"  $ {' '.join(args)}", flush=True)
    t0 = time.monotonic()
    result = subprocess.run(
        cmd,
        timeout=timeout,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    elapsed = time.monotonic() - t0
    if result.returncode != 0:
        print(f"  FAILED exit={result.returncode} ({elapsed:.0f}s)", flush=True)
    else:
        print(f"  OK ({elapsed:.0f}s)", flush=True)


def phase3_pr_import() -> None:
    """Import PR instances: GT defs + LLM query generation."""
    print("\n" + "=" * 60, flush=True)
    print("PHASE 3: PR import (GT + queries)", flush=True)
    print("=" * 60, flush=True)
    _run_cli("data", "pr-import", "--set", "all", "--workers", "4")


def phase4_non_ok_queries() -> None:
    """Generate non-OK queries."""
    print("\n" + "=" * 60, flush=True)
    print("PHASE 4: Non-OK query generation", flush=True)
    print("=" * 60, flush=True)
    _run_cli("data", "non-ok-queries", "--set", "all", "--workers", "2")


def phase5_collect() -> None:
    """Collect retrieval signals."""
    print("\n" + "=" * 60, flush=True)
    print("PHASE 5: Signal collection", flush=True)
    print("=" * 60, flush=True)
    _run_cli("pipeline", "collect", "--set", "all", "--workers", "4")


def phase6_merge_train() -> None:
    """Merge data and train models."""
    print("\n" + "=" * 60, flush=True)
    print("PHASE 6: Merge + Train", flush=True)
    print("=" * 60, flush=True)
    _run_cli("pipeline", "merge", "--what", "all")
    _run_cli("pipeline", "train", "--skip-merge")


async def run() -> None:
    overall_t0 = time.monotonic()
    print(f"Starting overnight pipeline at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"Clones dir: {CLONES_DIR}", flush=True)
    print(f"PID: {os.getpid()}", flush=True)

    await phase1_base_repos()
    await phase2_worktrees()
    phase3_pr_import()
    phase4_non_ok_queries()
    phase5_collect()
    phase6_merge_train()

    elapsed = time.monotonic() - overall_t0
    print(f"\n{'=' * 60}", flush=True)
    print(f"ALL DONE in {elapsed:.0f}s ({elapsed/3600:.1f}h)", flush=True)
    print(f"Finished at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
