#!/usr/bin/env python3
"""Parallel indexer for SWE-bench instance clones."""

import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

CLONES_DIR = Path.home() / ".recon/recon-lab/clones/instances"
VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venv/bin/python"
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def needs_indexing(clone_dir: Path) -> bool:
    recon_dir = clone_dir / ".recon"
    if not recon_dir.exists():
        return True
    db = recon_dir / "index.db"
    if not db.exists():
        return True
    # Check epochs > 0 (completed indexing)
    import sqlite3
    try:
        c = sqlite3.connect(str(db))
        epochs = c.execute("SELECT count(*) FROM epochs").fetchone()[0]
        c.close()
        return epochs == 0
    except Exception:
        return True


def index_repo(clone_dir: Path) -> tuple[str, float, bool, str]:
    name = clone_dir.name
    start = time.time()
    try:
        env = {**__import__("os").environ, "TERM": "dumb"}
        # Cap internal tree-sitter workers to avoid oversubscription
        # with outer parallelism (WORKERS outer × inner workers ≤ nproc)
        env["CODERECON_INDEX_WORKERS"] = str(max(1, (__import__("os").cpu_count() or 4) // WORKERS))
        result = subprocess.run(
            [str(VENV_PYTHON), "-m", "coderecon.cli.main", "init", "-r", str(clone_dir)],
            capture_output=True, text=True, timeout=600,
            env=env,
        )
        elapsed = time.time() - start
        ok = result.returncode == 0
        msg = "" if ok else result.stderr[-200:]
        return name, elapsed, ok, msg
    except subprocess.TimeoutExpired:
        return name, time.time() - start, False, "TIMEOUT"
    except Exception as e:
        return name, time.time() - start, False, str(e)


def main():
    all_clones = sorted(
        d for d in CLONES_DIR.iterdir()
        if d.is_dir() and (d / ".git").exists()
    )
    to_index = [d for d in all_clones if needs_indexing(d)]
    total = len(all_clones)
    done_already = total - len(to_index)

    print(f"Total clones: {total}, already indexed: {done_already}, to index: {len(to_index)}")
    print(f"Workers: {WORKERS}")
    print(f"Estimated time: ~{len(to_index) * 90 / WORKERS / 60:.0f} minutes")
    print()

    completed = 0
    failed = 0
    start_all = time.time()

    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(index_repo, d): d for d in to_index}
        for future in as_completed(futures):
            name, elapsed, ok, msg = future.result()
            completed += 1
            if ok:
                status = f"OK ({elapsed:.0f}s)"
            else:
                failed += 1
                status = f"FAIL ({elapsed:.0f}s): {msg}"
            
            wall = time.time() - start_all
            rate = completed / wall * 3600 if wall > 0 else 0
            remaining = len(to_index) - completed
            eta_min = remaining / (completed / wall) / 60 if completed > 0 else 0
            
            print(f"[{done_already + completed}/{total}] {name}: {status}  "
                  f"({rate:.0f}/hr, ETA {eta_min:.0f}m)")

    total_time = time.time() - start_all
    print(f"\nDone: {completed} indexed, {failed} failed, {total_time/60:.1f} minutes total")


if __name__ == "__main__":
    main()
