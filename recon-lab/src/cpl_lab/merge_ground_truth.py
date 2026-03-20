"""Merge per-repo ground truth into Parquet tables.

Two phases:
  1. **Post-process**: For each repo with per-task JSONs but no JSONL
     tables, run ``collector.collect_ground_truth()`` to resolve defs
     against the coderecon index and produce ``runs.jsonl``,
     ``touched_objects.jsonl``, ``queries.jsonl``.
  2. **Merge**: Concatenate across repos into ``data/merged/*.parquet``,
     adding a ``repo_set`` column from ``REPO_MANIFEST``.

Reads: ``data/{repo_id}/ground_truth/{N1..W11}.json``
       ``clones/{set}/{clone}/.recon/index.db``
Writes: ``data/merged/runs.parquet``
        ``data/merged/touched_objects.parquet``
        ``data/merged/queries.parquet``
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import click
import pandas as pd

from cpl_lab.clone import REPO_MANIFEST, clone_dir_for


def _postprocess_repos(
    data_dir: Path,
    clones_dir: Path,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run collector on repos that have per-task JSONs but no JSONL tables.

    Returns summary with counts of processed / skipped / failed repos.
    """
    from cpl_lab.collector import collect_ground_truth, iter_task_json_files

    processed = skipped = failed = 0
    details: list[dict[str, Any]] = []

    for repo_dir in sorted(data_dir.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name in ("merged", "logs", "index_logs"):
            continue
        repo_id = repo_dir.name
        gt_dir = repo_dir / "ground_truth"

        # Skip if no per-task JSONs
        task_jsons = iter_task_json_files(gt_dir)
        if not task_jsons:
            continue

        # Skip if JSONL tables already exist
        if all((gt_dir / f).exists() for f in ("runs.jsonl", "touched_objects.jsonl", "queries.jsonl")):
            skipped += 1
            continue

        # Find the index.db
        clone = clone_dir_for(repo_id, clones_dir)
        if clone is None or not clone.is_dir():
            click.echo(f"  {repo_id}: clone not found, skipping postprocess", err=True)
            failed += 1
            continue

        index_db = clone / ".recon" / "index.db"
        if not index_db.exists():
            click.echo(f"  {repo_id}: index.db missing at {index_db}", err=True)
            failed += 1
            continue

        if verbose:
            click.echo(f"  {repo_id}: postprocessing {len(task_jsons)} task JSONs...")
        try:
            summary = collect_ground_truth(repo_id, repo_dir, index_db)
            details.append(summary)
            processed += 1
            if verbose:
                click.echo(f"    {summary['queries']} queries, "
                           f"{summary['relevant_defs_total']} defs "
                           f"({summary['unmatched']} unmatched)")
        except Exception as e:
            click.echo(f"  {repo_id}: postprocess failed: {e}", err=True)
            failed += 1

    return {"processed": processed, "skipped": skipped, "failed": failed, "details": details}


def merge_ground_truth(
    data_dir: Path,
    clones_dir: Path | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Post-process and merge all per-repo ground truth into Parquet.

    Args:
        data_dir: Root data directory containing ``{repo_id}/`` subdirs.
        clones_dir: Workspace clones directory. If provided, runs
            postprocessing for repos that need it before merging.
        verbose: Print progress.

    Returns:
        Summary dict with row counts.
    """
    # Phase 1: postprocess per-task JSONs → JSONL tables
    if clones_dir is not None:
        pp = _postprocess_repos(data_dir, clones_dir, verbose=verbose)
        if pp["processed"] > 0:
            click.echo(f"  Postprocessed {pp['processed']} repos "
                       f"({pp['skipped']} skipped, {pp['failed']} failed)")
        elif pp["failed"] > 0:
            click.echo(f"  {pp['failed']} repos failed postprocessing")

    # Phase 2: merge JSONL → Parquet
    merged_dir = data_dir / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)

    tables = {
        "runs": "ground_truth/runs.jsonl",
        "touched_objects": "ground_truth/touched_objects.jsonl",
        "queries": "ground_truth/queries.jsonl",
    }

    counts: dict[str, int] = {}

    for table_name, rel_path in tables.items():
        rows: list[dict] = []
        for repo_dir in sorted(data_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name in ("merged", "logs", "index_logs"):
                continue
            repo_id = repo_dir.name
            src = repo_dir / rel_path
            if not src.exists():
                continue
            repo_set = REPO_MANIFEST.get(repo_id, {}).get("set", "unknown")
            for ln in src.read_text().splitlines():
                if ln.strip():
                    row = json.loads(ln)
                    row["repo_id"] = repo_id
                    row["repo_set"] = repo_set
                    rows.append(row)

        df = pd.DataFrame(rows)
        out_path = merged_dir / f"{table_name}.parquet"
        df.to_parquet(out_path, index=False)
        counts[table_name] = len(df)

    # Phase 3: collect per-repo features from index.db
    if clones_dir is not None:
        repo_features = _collect_repo_features(data_dir, clones_dir)
        if repo_features:
            rf_df = pd.DataFrame(repo_features)
            rf_df.to_parquet(merged_dir / "repo_features.parquet", index=False)
            counts["repo_features"] = len(rf_df)

    summary = {"merged_dir": str(merged_dir), "counts": counts}
    (merged_dir / "ground_truth_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _collect_repo_features(
    data_dir: Path,
    clones_dir: Path,
) -> list[dict[str, Any]]:
    """Query each repo's index.db for object_count and file_count."""
    rows: list[dict[str, Any]] = []
    for repo_dir in sorted(data_dir.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name in ("merged", "logs", "index_logs"):
            continue
        repo_id = repo_dir.name
        clone = clone_dir_for(repo_id, clones_dir)
        if clone is None:
            continue
        index_db = clone / ".recon" / "index.db"
        if not index_db.exists():
            continue
        con = sqlite3.connect(str(index_db))
        try:
            obj_count = con.execute("SELECT COUNT(*) FROM def_facts").fetchone()[0]
            file_count = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        except Exception:
            obj_count = file_count = 0
        finally:
            con.close()
        rows.append({
            "repo_id": repo_id,
            "object_count": obj_count,
            "file_count": file_count,
        })
    return rows
