"""Merge per-repo ground truth into Parquet tables.

Two phases:
  1. **Post-process**: For each repo with per-task JSONs but no JSONL
     tables, run ``collect_ground_truth()`` to resolve defs against the
     coderecon index and produce ``runs.jsonl``, ``touched_objects.jsonl``,
     ``queries.jsonl``.
  2. **Merge**: Concatenate across repos into ``data/merged/*.parquet``,
     adding a ``repo_set`` column from ``REPO_MANIFEST``.

Reads: ``data/{repo_id}/ground_truth/{N1..W11}.json``
       ``clones/{set}/{clone}/.recon/index.db``
Writes: ``data/merged/runs.parquet``
        ``data/merged/touched_objects.parquet``
        ``data/merged/queries.parquet``
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import click
import pandas as pd

from cpl_lab.data_manifest import (
    clone_dir_for_dir,
    iter_repo_data_dirs,
    iter_task_json_files,
    load_repo_manifest,
    main_clone_dir_for_dir,
)


# ── Ground truth post-processing (from collector.py) ────────────


def _resolve_end_line(
    defs_by_coords: dict[tuple[str, str, str, int], int],
    path: str,
    name: str,
    kind: str,
    start_line: int,
) -> int | None:
    """Look up end_line for a def from the pre-fetched defs map.

    Tries exact start_line match first, then fuzzy ±5 lines.
    """
    # Exact match
    key = (path, name, kind, start_line)
    if key in defs_by_coords:
        return defs_by_coords[key]

    # Fuzzy: within ±5 lines
    for delta in range(1, 6):
        for sl in (start_line + delta, start_line - delta):
            key = (path, name, kind, sl)
            if key in defs_by_coords:
                return defs_by_coords[key]

    return None


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows as newline-delimited JSON (atomic)."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    os.replace(tmp, path)


def collect_ground_truth(
    repo_id: str,
    data_dir: Path,
    index_db: Path,
) -> dict[str, Any]:
    """Post-process agent JSON output into JSONL tables.

    Reads per-task JSON files from ``data/{repo_id}/ground_truth/``,
    resolves defs against the coderecon index, and assembles
    ``runs.jsonl``, ``touched_objects.jsonl``, and ``queries.jsonl``.
    """
    gt_dir = data_dir / "ground_truth"
    task_files = iter_task_json_files(gt_dir)
    if not task_files:
        raise FileNotFoundError(f"No ground truth JSON files in {gt_dir}")

    # Pre-fetch all defs from the index via SDK
    main_clone_dir = index_db.parent.parent  # .recon/index.db → repo_root
    defs_by_coords = _fetch_defs_map(main_clone_dir)

    runs: list[dict[str, Any]] = []
    touched: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []

    for tf in task_files:
        task = json.loads(tf.read_text())
        raw_task_id = task["task_id"]
        task_id = raw_task_id.split("/")[-1] if "/" in raw_task_id else raw_task_id
        run_id = f"{repo_id}_{task_id}"

        runs.append({
            "run_id": run_id,
            "repo_id": repo_id,
            "task_id": task_id,
            "task_text": task.get("task_text") or task.get("title", ""),
        })

        for tier_key, tier_label in [
            ("minimum_sufficient_defs", "minimum"),
            ("coverage_linked_defs", "coverage"),
        ]:
            for rd in task.get(tier_key, []):
                end_line = _resolve_end_line(
                    defs_by_coords, rd["path"], rd["name"], rd["kind"],
                    start_line=rd["start_line"],
                )
                if end_line is None:
                    unmatched.append({
                        "task_id": task_id,
                        "tier": tier_label,
                        "path": rd["path"],
                        "name": rd["name"],
                        "kind": rd["kind"],
                    })
                touched.append({
                    "run_id": run_id,
                    "candidate_key": f"{rd['path']}:{rd['kind']}:{rd['name']}:{rd['start_line']}",
                    "path": rd["path"],
                    "kind": rd["kind"],
                    "name": rd["name"],
                    "start_line": rd["start_line"],
                    "end_line": end_line if end_line is not None else rd["start_line"],
                    "tier": tier_label,
                })

        audit_records.append({
            "run_id": run_id,
            "task_id": task_id,
            "diff": task.get("diff", ""),
            "solve_notes": task.get("solve_notes", ""),
            "confidence": task.get("confidence", "unknown"),
            "excluded_defs": task.get("excluded_defs", []),
            "justifications": {
                tier_key: [
                    {"path": d["path"], "name": d["name"], "reason": d.get("reason", "")}
                    for d in task.get(tier_key, [])
                ]
                for tier_key in ("minimum_sufficient_defs", "coverage_linked_defs")
            },
        })

        for qi, q in enumerate(task.get("queries", [])):
            query_type = q["query_type"]
            label = "OK" if query_type.startswith("Q_") else query_type
            queries.append({
                "run_id": run_id,
                "query_id": f"{run_id}_q{qi}",
                "query_text": q["query_text"],
                "query_type": query_type,
                "seeds": q.get("seeds", []),
                "pins": q.get("pins", []),
                "label_gate": label,
            })

    # Non-OK queries (UNSAT/BROAD/AMBIG) are per-repo, stored in the main
    # repo's data dir.  For PR worktrees, resolve via manifest.json.
    manifest_path = data_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        logical_repo_id = manifest.get("logical_repo_id") or manifest.get("repo_id")
    else:
        logical_repo_id = data_dir.name
    non_ok_path = data_dir.parent / logical_repo_id / "ground_truth" / "non_ok_queries.json"
    if non_ok_path.exists():
        non_ok = json.loads(non_ok_path.read_text())
        non_ok_run_id = f"{repo_id}__non_ok"
        for qi, q in enumerate(non_ok.get("non_ok_queries", [])):
            query_type = q["query_type"]
            queries.append({
                "run_id": non_ok_run_id,
                "query_id": f"{non_ok_run_id}_q{qi}",
                "query_text": q["query_text"],
                "query_type": query_type,
                "seeds": q.get("seeds", []),
                "pins": q.get("pins", []),
                "label_gate": query_type,
            })

    out_dir = data_dir / "ground_truth"
    _write_jsonl(out_dir / "runs.jsonl", runs)
    _write_jsonl(out_dir / "touched_objects.jsonl", touched)
    _write_jsonl(out_dir / "queries.jsonl", queries)

    audit_dir = data_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(audit_dir / "audit_records.jsonl", audit_records)

    n_minimum = sum(1 for t in touched if t["tier"] == "minimum")
    n_coverage = sum(1 for t in touched if t["tier"] == "coverage")

    summary = {
        "repo_id": repo_id,
        "tasks": len(runs),
        "relevant_defs_total": len(touched),
        "minimum_sufficient": n_minimum,
        "coverage_linked": n_coverage,
        "queries": len(queries),
        "unmatched": len(unmatched),
        "unmatched_rate": len(unmatched) / max(len(touched) + len(unmatched), 1),
        "unmatched_details": unmatched,
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


# ── Post-process + merge pipeline ───────────────────────────────


def _postprocess_repos(
    data_dir: Path,
    clones_dir: Path,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run collector on repos that have per-task JSONs but no JSONL tables."""
    processed = skipped = failed = 0
    details: list[dict[str, Any]] = []

    for repo_dir in iter_repo_data_dirs(data_dir):
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

        # Find the index.db (use main repo clone, not instance worktree)
        clone = main_clone_dir_for_dir(repo_dir, clones_dir)
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
        for repo_dir in iter_repo_data_dirs(data_dir):
            repo_id = repo_dir.name
            src = repo_dir / rel_path
            if not src.exists():
                continue
            manifest = load_repo_manifest(repo_dir)
            repo_set = manifest.get("repo_set", "unknown")
            logical_repo_id = manifest.get("logical_repo_id", repo_id)
            for ln in src.read_text().splitlines():
                if ln.strip():
                    row = json.loads(ln)
                    row["repo_id"] = repo_id
                    row["repo_set"] = repo_set
                    row["logical_repo_id"] = logical_repo_id
                    rows.append(row)

        df = pd.DataFrame(rows)
        out_path = merged_dir / f"{table_name}.parquet"
        tmp_path = out_path.with_suffix(".tmp.parquet")
        df.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, out_path)
        counts[table_name] = len(df)

    # Phase 3: collect per-repo features from index.db
    if clones_dir is not None:
        repo_features = _collect_repo_features(data_dir, clones_dir)
        if repo_features:
            rf_df = pd.DataFrame(repo_features)
            tmp_rf = merged_dir / "repo_features.tmp.parquet"
            rf_df.to_parquet(tmp_rf, index=False)
            os.replace(tmp_rf, merged_dir / "repo_features.parquet")
            counts["repo_features"] = len(rf_df)

    summary = {"merged_dir": str(merged_dir), "counts": counts}
    (merged_dir / "ground_truth_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _collect_repo_features(
    data_dir: Path,
    clones_dir: Path,
) -> list[dict[str, Any]]:
    """Query each repo's index for object_count and file_count via SDK."""
    from coderecon.sdk.dev import CodeReconDev

    rows: list[dict[str, Any]] = []

    async def _gather() -> None:
        from cpl_lab.config import recon_binary
        async with CodeReconDev(binary=recon_binary()) as sdk:
            for repo_dir in iter_repo_data_dirs(data_dir):
                repo_id = repo_dir.name
                manifest = load_repo_manifest(repo_dir)
                clone = main_clone_dir_for_dir(repo_dir, clones_dir)
                if clone is None:
                    continue
                index_db = clone / ".recon" / "index.db"
                if not index_db.exists():
                    continue
                try:
                    reg = await sdk.register(str(clone))
                    status = await sdk.index_status(reg.repo)
                    rows.append({
                        "repo_id": repo_id,
                        "logical_repo_id": manifest.get("logical_repo_id", repo_id),
                        "object_count": status.def_count,
                        "file_count": status.file_count,
                    })
                except Exception:
                    rows.append({
                        "repo_id": repo_id,
                        "logical_repo_id": manifest.get("logical_repo_id", repo_id),
                        "object_count": 0,
                        "file_count": 0,
                    })

    asyncio.run(_gather())
    return rows


def _fetch_defs_map(
    main_clone_dir: Path,
) -> dict[tuple[str, str, str, int], int]:
    """Fetch all defs from a repo's index via SDK and return lookup map.

    Returns: {(path, name, kind, start_line): end_line}
    """
    from coderecon.sdk.dev import CodeReconDev

    result: dict[tuple[str, str, str, int], int] = {}

    async def _fetch() -> None:
        from cpl_lab.config import recon_binary
        async with CodeReconDev(binary=recon_binary()) as sdk:
            reg = await sdk.register(str(main_clone_dir))
            defs = await sdk.lookup_defs(reg.repo)
            for d in defs:
                key = (d.path, d.name, d.kind, d.start_line)
                result[key] = d.end_line

    asyncio.run(_fetch())
    return result
