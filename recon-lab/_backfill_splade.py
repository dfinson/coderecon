#!/usr/bin/env python3
"""One-shot backfill: detect and fill ALL missing signals across all GT repos.

Handles: SPLADE vectors, semantic resolution, semantic neighbors,
doc chunk vectors, and doc-code edges.

Run from the main coderecon project root (not recon-lab), so that
coderecon + coderecon-models-splade + coderecon-models-ce are on sys.path:

    cd /home/dave01/wsl-repos/coderecon
    uv run --frozen python recon-lab/_backfill_splade.py
"""

import sys
import time
from pathlib import Path

# Add recon-lab src to path so we can import cpl_lab
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cpl_lab.pipeline.clone import REPO_MANIFEST, clone_dir_for

CLONES_DIR = Path.home() / ".recon" / "recon-lab" / "clones"


def backfill_repo(repo_id: str, clone_dir: Path) -> dict:
    """Run consistency check + backfill for one repo."""
    recon_dir = clone_dir / ".recon"
    db_path = recon_dir / "index.db"

    if not db_path.exists():
        return {"repo_id": repo_id, "status": "no_index"}

    from coderecon.index._internal.db import Database, check_consistency, backfill_gaps

    db = Database(db_path)

    # Ensure new tables (e.g. splade_vecs) exist in old DBs
    db.create_all()
    report = check_consistency(db)

    if report.consistent:
        return {"repo_id": repo_id, "status": "ok", "gaps": 0}

    t0 = time.monotonic()
    results = backfill_gaps(db, report)
    elapsed = time.monotonic() - t0

    return {
        "repo_id": repo_id,
        "status": "backfilled",
        "gaps": report.total_gaps,
        "stored": results,
        "elapsed_s": round(elapsed, 1),
    }


def main():
    total = len(REPO_MANIFEST)
    print(f"Scanning {total} repos for signal gaps...\n")

    ok = backfilled = skipped = errors = 0
    total_stored = 0

    for i, repo_id in enumerate(sorted(REPO_MANIFEST), 1):
        clone_dir = clone_dir_for(repo_id, CLONES_DIR)
        if clone_dir is None:
            print(f"  [{i}/{total}] {repo_id}: SKIP (no clone dir)")
            skipped += 1
            continue

        try:
            result = backfill_repo(repo_id, clone_dir)
        except Exception as e:
            print(f"  [{i}/{total}] {repo_id}: ERROR {e}")
            errors += 1
            continue

        status = result["status"]
        if status == "no_index":
            print(f"  [{i}/{total}] {repo_id}: SKIP (no index.db)")
            skipped += 1
        elif status == "ok":
            print(f"  [{i}/{total}] {repo_id}: OK")
            ok += 1
        elif status == "backfilled":
            stored = result["stored"]
            elapsed = result["elapsed_s"]
            total_stored += sum(stored.values())
            print(f"  [{i}/{total}] {repo_id}: BACKFILLED {result['gaps']} gaps -> {stored} ({elapsed}s)")
            backfilled += 1

    print(f"\nDone: {ok} ok, {backfilled} backfilled ({total_stored} total), {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
