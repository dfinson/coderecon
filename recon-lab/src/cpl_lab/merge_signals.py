"""Merge per-repo signal data into a single denormalized Parquet file.

Run after each signal collection pass (when harvesters change).
Joins with pre-merged ground truth for graded relevance labels,
query metadata (query_type, label_gate), repo_set, and repo features
(object_count, file_count).  The resulting ``candidates_rank.parquet``
is the single input for all three trainers.

Reads: ``data/{repo_id}/signals/candidates_rank.jsonl``
       ``data/merged/touched_objects.parquet``
       ``data/merged/queries.parquet``
       ``data/merged/repo_features.parquet``  (optional)
Writes: ``data/merged/candidates_rank.parquet``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from cpl_lab.clone import REPO_MANIFEST


def merge_signals(data_dir: Path) -> dict[str, Any]:
    """Merge all per-repo signal JSONL into one denormalized Parquet.

    Every row gets: ``repo_id``, ``repo_set``, ``query_type``,
    ``label_gate``, ``object_count``, ``file_count``, and graded
    ``label_relevant`` (2 = minimum, 1 = thrash_preventing, 0 = irrelevant).

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

    # Graded relevance: (run_id, def_uid) → 2 or 1
    touched_df = pd.read_parquet(touched_path)
    tier_map: dict[tuple[str, str], int] = {}
    for _, row in touched_df.iterrows():
        grade = 2 if row.get("tier", "minimum") == "minimum" else 1
        tier_map[(row["run_id"], row["def_uid"])] = grade

    # Query metadata: query_id → {query_type, label_gate, repo_id}
    queries_df = pd.read_parquet(queries_path)
    query_meta: dict[str, dict[str, str]] = {}
    for _, qr in queries_df.iterrows():
        query_meta[qr["query_id"]] = {
            "query_type": qr["query_type"],
            "label_gate": qr.get("label_gate", "OK"),
            "repo_id": qr.get("repo_id", ""),
        }

    # Repo features: repo_id → {object_count, file_count}
    rf_path = merged_dir / "repo_features.parquet"
    repo_feat_map: dict[str, dict[str, int]] = {}
    if rf_path.exists():
        for _, rf in pd.read_parquet(rf_path).iterrows():
            repo_feat_map[rf["repo_id"]] = {
                "object_count": int(rf["object_count"]),
                "file_count": int(rf["file_count"]),
            }

    # ── collect per-repo signal JSONL ────────────────────────────

    rows: list[dict] = []
    for repo_dir in sorted(data_dir.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name in ("merged", "logs", "index_logs"):
            continue
        repo_id = repo_dir.name
        src = repo_dir / "signals" / "candidates_rank.jsonl"
        if not src.exists():
            continue
        repo_set = REPO_MANIFEST.get(repo_id, {}).get("set", "unknown")
        rf = repo_feat_map.get(repo_id, {"object_count": 0, "file_count": 0})
        for ln in src.read_text().splitlines():
            if ln.strip():
                row = json.loads(ln)
                row["repo_id"] = repo_id
                row["repo_set"] = repo_set
                row["object_count"] = rf["object_count"]
                row["file_count"] = rf["file_count"]
                # Denormalize query metadata
                qm = query_meta.get(row.get("query_id", ""), {})
                row["query_type"] = qm.get("query_type", "")
                row["label_gate"] = qm.get("label_gate", "OK")
                rows.append(row)

    if not rows:
        raise ValueError("No signal data found")

    df = pd.DataFrame(rows)

    # Re-derive graded relevance from source of truth
    df["label_relevant"] = [
        tier_map.get((row["run_id"], row["def_uid"]), 0)
        for _, row in df.iterrows()
    ]

    out_path = merged_dir / "candidates_rank.parquet"
    df.to_parquet(out_path, index=False)

    summary = {
        "merged_dir": str(merged_dir),
        "total_candidates": len(df),
        "positive_rate": float((df["label_relevant"] > 0).mean()),
    }
    (merged_dir / "signals_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
