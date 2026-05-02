"""Sharded indexing for AML multi-node execution.

Splits the repo list into N shards and indexes only the assigned shard.
Each shard runs on a separate GPU node (NC4as_T4_v3) for parallel indexing.

Usage (from AML component or direct):
    python -m recon_lab.pipeline.index_shard \
        --workspace /mnt/ws \
        --set all \
        --shard-index 0 \
        --shard-count 4
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from .index import _iter_clones, _recon_init_cmd


def _shard_repos(repos: list[Path], shard_index: int, shard_count: int) -> list[Path]:
    """Select the repos belonging to this shard (round-robin by index)."""
    return [r for i, r in enumerate(repos) if i % shard_count == shard_index]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sharded GPU indexing for AML.")
    parser.add_argument("--workspace", type=Path, required=True, help="Pipeline workspace root.")
    parser.add_argument("--set", dest="repo_set", default="all", help="Repo set filter.")
    parser.add_argument("--timeout", type=int, default=2400, help="Timeout per repo (seconds).")
    parser.add_argument("--shard-index", type=int, default=0, help="This node's shard index.")
    parser.add_argument("--shard-count", type=int, default=1, help="Total shard count.")
    parser.add_argument("--reindex", action="store_true", default=True, help="Force full reindex.")
    args = parser.parse_args(argv)

    clones_dir = args.workspace / "clones"
    if not clones_dir.is_dir():
        print(f"ERROR: clones directory not found: {clones_dir}", file=sys.stderr)
        raise SystemExit(1)

    all_repos = _iter_clones(clones_dir, args.repo_set)
    my_repos = _shard_repos(all_repos, args.shard_index, args.shard_count)

    print(f"Shard {args.shard_index}/{args.shard_count}: {len(my_repos)} repos "
          f"(of {len(all_repos)} total)", flush=True)

    ok = failed = timed_out = 0

    for repo_dir in my_repos:
        rel = f"{repo_dir.parent.name}/{repo_dir.name}"
        print(f"  START {rel}", flush=True)
        t0 = time.monotonic()

        cmd, env = _recon_init_cmd(repo_dir, reindex=args.reindex)

        try:
            subprocess.run(
                cmd, env=env, timeout=args.timeout, check=True,
                capture_output=True, text=True,
            )
            elapsed = time.monotonic() - t0
            print(f"  OK {rel} ({elapsed:.0f}s)", flush=True)
            ok += 1
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - t0
            print(f"  TIMEOUT {rel} ({elapsed:.0f}s)", flush=True)
            timed_out += 1
        except subprocess.CalledProcessError as e:
            elapsed = time.monotonic() - t0
            snippet = (e.stderr or "")[:200]
            print(f"  FAILED {rel} exit={e.returncode} ({elapsed:.0f}s) {snippet}", flush=True)
            failed += 1

    print(f"\nShard {args.shard_index} complete: OK={ok} Failed={failed} Timeout={timed_out}",
          flush=True)

    if failed > 0 or timed_out > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
