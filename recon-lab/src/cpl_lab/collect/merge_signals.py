"""Merge per-repo signal data into a single denormalized Parquet file.

Run after each signal collection pass (when harvesters change).
Joins with pre-merged ground truth for graded relevance labels,
query metadata (query_type, label_gate), repo_set, and repo features
(object_count, file_count).  The resulting ``candidates_rank.parquet``
is the single input for all three trainers.

Streams in two dimensions: one repo at a time, and within each repo
reads Parquet row groups individually.  Peak RAM ≈ one row group
(one query's candidates, typically <100K rows) rather than the full
repo or dataset.

Reads: ``data/{repo_id}/signals/candidates_rank.parquet``
       ``data/merged/touched_objects.parquet``
       ``data/merged/queries.parquet``
       ``data/merged/repo_features.parquet``  (optional)
Writes: ``data/merged/candidates_rank.parquet``
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from cpl_lab.data_manifest import iter_repo_data_dirs, load_repo_manifest

console = Console()

# Canonical schema for the merged output.  Uses large_string because
# pandas round-trips through PyArrow produce large_string by default.
_MERGED_SCHEMA = pa.schema([
    pa.field("task_id", pa.large_string()),
    pa.field("query_id", pa.large_string()),
    pa.field("query_type", pa.large_string()),
    pa.field("candidate_key", pa.large_string()),
    pa.field("path", pa.large_string()),
    pa.field("kind", pa.large_string()),
    pa.field("name", pa.large_string()),
    pa.field("lexical_path", pa.large_string()),
    pa.field("qualified_name", pa.large_string()),
    pa.field("start_line", pa.int64()),
    pa.field("end_line", pa.int64()),
    pa.field("object_size_lines", pa.int64()),
    pa.field("file_ext", pa.large_string()),
    pa.field("parent_dir", pa.large_string()),
    pa.field("path_depth", pa.int64()),
    pa.field("has_docstring", pa.bool_()),
    pa.field("has_decorators", pa.bool_()),
    pa.field("has_return_type", pa.bool_()),
    pa.field("signature_text", pa.large_string()),
    pa.field("namespace", pa.large_string()),
    pa.field("nesting_depth", pa.int64()),
    pa.field("has_parent_scope", pa.bool_()),
    pa.field("hub_score", pa.int64()),
    pa.field("is_test", pa.bool_()),
    pa.field("is_endpoint", pa.bool_()),
    pa.field("test_coverage_count", pa.int64()),
    pa.field("term_match_count", pa.float64()),
    pa.field("term_total_matches", pa.float64()),
    pa.field("lex_hit_count", pa.int64()),
    pa.field("bm25_file_score", pa.float64()),
    pa.field("graph_edge_type", pa.large_string()),
    pa.field("graph_seed_rank", pa.float64()),
    pa.field("graph_caller_max_tier", pa.large_string()),
    pa.field("symbol_source", pa.large_string()),
    pa.field("import_direction", pa.large_string()),
    pa.field("from_coverage", pa.bool_()),
    pa.field("retriever_hits", pa.int64()),
    pa.field("rrf_score", pa.float64()),
    pa.field("seed_path_distance", pa.int64()),
    pa.field("same_package", pa.bool_()),
    pa.field("package_distance", pa.int64()),
    pa.field("query_len", pa.int64()),
    pa.field("has_identifier", pa.bool_()),
    pa.field("has_path", pa.bool_()),
    pa.field("identifier_density", pa.float64()),
    pa.field("has_numbers", pa.bool_()),
    pa.field("has_quoted_strings", pa.bool_()),
    pa.field("term_count", pa.int64()),
    pa.field("label_relevant", pa.int64()),
    pa.field("repo_id", pa.large_string()),
    pa.field("repo_set", pa.large_string()),
    pa.field("logical_repo_id", pa.large_string()),
    pa.field("object_count", pa.int64()),
    pa.field("file_count", pa.int64()),
    pa.field("label_gate", pa.large_string()),
    pa.field("run_id", pa.large_string()),
])


def merge_signals(data_dir: Path) -> dict[str, Any]:
    """Merge all per-repo signal Parquet into one denormalized Parquet.

    Every row gets: ``repo_id``, ``repo_set``, ``query_type``,
    ``label_gate``, ``object_count``, ``file_count``, and graded
    ``label_relevant`` (1 = relevant, 0 = irrelevant).

    Streams row groups from each repo's Parquet, enriches, and writes
    immediately.  Peak memory ≈ one row group (~50-100K rows) rather
    than the full repo.

    Args:
        data_dir: Root data directory containing ``{repo_id}/`` subdirs
            and ``merged/`` with GT Parquet tables.

    Returns:
        Summary dict with row counts.
    """
    merged_dir = data_dir / "merged"

    # ── load lookups from merged GT ──────────────────────────────

    touched_path = merged_dir / "touched_objects.parquet"
    queries_path = merged_dir / "queries.parquet"
    for p in (touched_path, queries_path):
        if not p.exists():
            raise FileNotFoundError(f"No {p} — run merge_ground_truth first")

    # Graded relevance: (run_id, candidate_key) → 2 or 1
    # GT uses underscore separator (repo_query), signals use slash (repo/query).
    # Store both forms so lookups work regardless of format.
    touched_df = pd.read_parquet(touched_path)
    tier_map: dict[tuple[str, str], int] = {}
    for _, row in touched_df.iterrows():
        grade = 2 if row.get("tier", "minimum") == "minimum" else 1
        rid = row["run_id"]
        ckey = row["candidate_key"]
        tier_map[(rid, ckey)] = grade
        # Also store the slash-separated form for signal lookups
        # "cpp-abseil_M1" → "cpp-abseil/M1" (split on last _ before query id)
        parts = rid.rsplit("_", 1)
        if len(parts) == 2:
            tier_map[(f"{parts[0]}/{parts[1]}", ckey)] = grade

    # Query metadata: query_id → {query_type, label_gate, repo_id}
    # GT uses underscore form: "cpp-abseil_M1_q0"
    # Signals use slash form:  "cpp-abseil/M1/Q0"
    # Store both so lookups work regardless of source format.
    queries_df = pd.read_parquet(queries_path)
    query_meta: dict[str, dict[str, str]] = {}
    for _, qr in queries_df.iterrows():
        meta = {
            "query_type": qr["query_type"],
            "label_gate": qr.get("label_gate", "OK"),
            "repo_id": qr.get("repo_id", ""),
        }
        gt_qid = qr["query_id"]  # e.g. "cpp-abseil_M1_q0"
        query_meta[gt_qid] = meta
        # Convert to signal form: "cpp-abseil_M1_q0" → "cpp-abseil/M1/Q0"
        parts = gt_qid.rsplit("_", 2)
        if len(parts) == 3:
            sig_qid = f"{parts[0]}/{parts[1]}/{parts[2].upper()}"
            query_meta[sig_qid] = meta

    # Repo features: repo_id → {object_count, file_count}
    rf_path = merged_dir / "repo_features.parquet"
    repo_feat_map: dict[str, dict[str, Any]] = {}
    if rf_path.exists():
        for _, rf in pd.read_parquet(rf_path).iterrows():
            repo_feat_map[rf["repo_id"]] = {
                "object_count": int(rf["object_count"]),
                "file_count": int(rf["file_count"]),
                "logical_repo_id": rf.get("logical_repo_id", rf["repo_id"]),
            }

    # ── stream per-repo signals row-group by row-group ───────────

    out_path = merged_dir / "candidates_rank.parquet"
    writer: pq.ParquetWriter | None = None
    total_candidates = 0
    total_positive = 0

    # Pre-scan repos for progress sizing
    repo_jobs: list[tuple[str, Path, str, dict[str, int], int, int]] = []
    total_row_groups = 0
    total_rows_est = 0
    for repo_dir in iter_repo_data_dirs(data_dir):
        repo_id = repo_dir.name
        pq_src = repo_dir / "signals" / "candidates_rank.parquet"
        if not pq_src.exists():
            continue
        manifest = load_repo_manifest(repo_dir)
        repo_set = manifest.get("repo_set", "unknown")
        rf = repo_feat_map.get(repo_id, {
            "object_count": 0,
            "file_count": 0,
            "logical_repo_id": manifest.get("logical_repo_id", repo_id),
        })
        pf = pq.ParquetFile(pq_src)
        n_rg = pf.metadata.num_row_groups
        n_rows = pf.metadata.num_rows
        total_row_groups += n_rg
        total_rows_est += n_rows
        repo_jobs.append((repo_id, pq_src, repo_set, rf, n_rg, n_rows))

    # ── Rich progress ────────────────────────────────────────────
    overall = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30, complete_style="green"),
        TextColumn("{task.completed}/{task.total} repos"),
        TimeElapsedColumn(),
        TextColumn("·"),
        TimeRemainingColumn(),
        console=console, expand=False,
    )
    overall_bar = overall.add_task("Merge", total=len(repo_jobs))

    row_progress = Progress(
        TextColumn("  {task.description}", style="dim"),
        BarColumn(bar_width=25, style="cyan", complete_style="green"),
        TaskProgressColumn(),
        TextColumn("{task.fields[rows]}", style="dim"),
        console=console, expand=False,
    )
    row_tid = None

    tbl = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    tbl.add_column("Repo", style="cyan", min_width=28)
    tbl.add_column("Rows", justify="right", min_width=12)
    tbl.add_column("Pos", justify="right", style="green", min_width=6)
    tbl.add_column("Time", justify="right", min_width=7)
    tbl.add_column("", width=4)

    repos_done = 0
    t_start = time.monotonic()

    def _render() -> Panel:
        header = Text.from_markup(
            f"  [bold]{len(repo_jobs)}[/bold] repos  ·  "
            f"[bold]{total_row_groups}[/bold] row groups  ·  "
            f"{total_rows_est:,} rows\n"
            f"  Merged: {total_candidates:,} rows  ·  "
            f"[green]{total_positive:,}[/green] positive  ·  "
            f"{repos_done} repos done"
        )
        parts: list = [header, Text(""), overall]
        if row_progress.tasks:
            parts.append(row_progress)
        if tbl.rows:
            parts.extend([Text(""), tbl])
        return Panel(
            Group(*parts),
            title="Signal Merge", title_align="left",
            border_style="dim", width=72, padding=(0, 1),
        )

    with Live(_render(), console=console, refresh_per_second=2) as live:
        for repo_id, pq_src, repo_set, rf, n_rg, n_rows in repo_jobs:
            # Add per-repo progress bar
            row_tid = row_progress.add_task(
                repo_id, total=n_rg, rows=f"{n_rows:,} rows"
            )
            live.update(_render())

            repo_candidates = 0
            repo_positive = 0
            t_repo = time.monotonic()
            pf = pq.ParquetFile(pq_src)

            for rg_idx in range(n_rg):
                table = pf.read_row_group(rg_idx)
                df = table.to_pandas()
                del table

                df["repo_id"] = repo_id
                df["repo_set"] = repo_set
                df["logical_repo_id"] = rf.get("logical_repo_id", repo_id)
                df["object_count"] = rf["object_count"]
                df["file_count"] = rf["file_count"]
                df["query_type"] = df["query_id"].map(
                    lambda qid: query_meta.get(qid, {}).get("query_type", "")
                )
                df["label_gate"] = df["query_id"].map(
                    lambda qid: query_meta.get(qid, {}).get("label_gate", "OK")
                )

                # Re-derive graded relevance from source of truth
                # Signal task_id is just the mutation part (e.g. "M1"),
                # but GT run_id is "{repo_id}_{task_id}" (e.g. "cpp-cli11_M1").
                task_col = df["task_id"] if "task_id" in df.columns else df.get("run_id", "")
                full_run_col = [f"{repo_id}_{t}" for t in task_col]
                cand_col = df.get("candidate_key", "")
                df["label_relevant"] = [
                    tier_map.get((t, c), 0) for t, c in zip(full_run_col, cand_col)
                ]

                # Alias task_id → run_id (training expects run_id for group keys)
                df["run_id"] = full_run_col

                n = len(df)
                pos = int((df["label_relevant"] > 0).sum())
                repo_candidates += n
                repo_positive += pos
                total_candidates += n
                total_positive += pos

                out_table = pa.Table.from_pandas(df, preserve_index=False)
                del df

                out_table = _align_to_merged(out_table)
                if writer is None:
                    writer = pq.ParquetWriter(out_path, _MERGED_SCHEMA)

                writer.write_table(out_table)
                del out_table

                row_progress.update(row_tid, advance=1)
                live.update(_render())

            # Repo finished
            repo_sec = round(time.monotonic() - t_repo, 1)
            row_progress.remove_task(row_tid)
            repos_done += 1
            overall.update(overall_bar, advance=1)
            tbl.add_row(
                repo_id,
                f"{repo_candidates:,}",
                f"{repo_positive:,}" if repo_positive else "—",
                f"{repo_sec}s",
                "[green]✓[/green]",
            )
            live.update(_render())

    if writer is not None:
        writer.close()

    if total_candidates == 0:
        raise ValueError("No signal data found")

    positive_rate = total_positive / total_candidates
    summary = {
        "merged_dir": str(merged_dir),
        "total_candidates": total_candidates,
        "positive_rate": float(positive_rate),
    }
    (merged_dir / "signals_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _align_to_merged(table: pa.Table) -> pa.Table:
    """Align *table* columns to ``_MERGED_SCHEMA``.

    Adds missing columns as null arrays, drops extras, reorders, and
    casts each column to the canonical type.
    """
    columns = []
    for field in _MERGED_SCHEMA:
        if field.name in table.column_names:
            col = table.column(field.name)
            if col.type != field.type:
                # null → concrete: replace with typed nulls
                if pa.types.is_null(col.type):
                    col = pa.nulls(len(table), type=field.type)
                else:
                    col = col.cast(field.type, safe=False)
        else:
            col = pa.nulls(len(table), type=field.type)
        columns.append(col)
    return pa.table(
        {f.name: c for f, c in zip(_MERGED_SCHEMA, columns)},
        schema=_MERGED_SCHEMA,
    )
