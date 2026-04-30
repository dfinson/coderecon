"""Stratified dataset for GT discovery experiment.

Builds a balanced sample across creation intensity buckets,
loading one query per instance with the full PR context.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from inspect_ai.dataset import Dataset, MemoryDataset, Sample

from recon_lab.data_manifest import iter_repo_data_dirs


def _def_key(d: dict) -> str:
    return f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"


def _classify_instance(inst: dict) -> str:
    """Classify instance into creation bucket."""
    files = inst.get("files", [])
    if not files:
        return "modification"
    new_count = sum(1 for f in files if f.get("is_new"))
    ratio = new_count / len(files)
    if ratio > 0.5:
        return "creation_heavy"
    elif new_count > 0:
        return "mixed"
    return "modification"


def _pick_query(inst: dict) -> dict[str, Any] | None:
    """Pick the best query: prefer Q_FULL, then Q_SEM_IDENT, else first."""
    queries = inst.get("queries", [])
    if not queries:
        return None
    preference = ["Q_FULL", "Q_SEM_IDENT", "Q_IDENTIFIER", "Q_SEMANTIC"]
    by_type = {q.get("query_type"): q for q in queries}
    for pref in preference:
        if pref in by_type:
            return by_type[pref]
    return queries[0]


def gt_discovery_dataset(
    data_dir: str = "~/.recon/recon-lab/data",
    *,
    n_modification: int = 40,
    n_mixed: int = 40,
    n_creation_heavy: int = 40,
    seed: int = 42,
) -> Dataset:
    """Build stratified sample across creation intensity buckets.

    For each instance, loads one query (Q_FULL preferred) plus the full
    PR context (title, body, diff excerpt) as the task prompt.

    Returns:
        Inspect AI MemoryDataset.
    """
    data_root = Path(data_dir).expanduser()
    rng = random.Random(seed)

    buckets: dict[str, list[dict]] = {
        "modification": [],
        "mixed": [],
        "creation_heavy": [],
    }

    for repo_dir in iter_repo_data_dirs(data_root):
        gt_dir = repo_dir / "ground_truth"
        gt_file = gt_dir / f"{repo_dir.name}.json"
        if not gt_file.exists():
            continue

        try:
            inst = json.loads(gt_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        bucket = _classify_instance(inst)
        query = _pick_query(inst)
        if query is None:
            continue

        defs = inst.get("minimum_sufficient_defs", [])
        files = inst.get("files", [])
        new_paths = [f["path"] for f in files if f.get("is_new")]
        mod_paths = [f["path"] for f in files if not f.get("is_new")]

        record = {
            "instance_id": inst.get("instance_id") or repo_dir.name,
            "repo_id": inst.get("repo_id", ""),
            "task_id": inst.get("task_id") or repo_dir.name,
            "query_text": query["query_text"],
            "query_type": query.get("query_type", "UNKNOWN"),
            "seeds": query.get("seeds", []),
            "pins": query.get("pins", []),
            "title": inst.get("title", ""),
            "body": (inst.get("body") or "")[:3000],
            "gt_edited": [_def_key(d) for d in defs],
            "gt_def_details": defs,
            "creation_bucket": bucket,
            "new_file_paths": new_paths,
            "modified_file_paths": mod_paths,
            "total_files": len(files),
            "new_file_count": len(new_paths),
        }
        buckets[bucket].append(record)

    # Stratified sampling
    selected: list[dict] = []
    limits = {
        "modification": n_modification,
        "mixed": n_mixed,
        "creation_heavy": n_creation_heavy,
    }

    for bucket_name, limit in limits.items():
        pool = buckets[bucket_name]
        rng.shuffle(pool)
        selected.extend(pool[:limit])

    if not selected:
        msg = f"No instances found in {data_root} for GT discovery."
        raise FileNotFoundError(msg)

    samples = [
        Sample(
            id=rec["instance_id"],
            input=_build_task_prompt(rec),
            metadata=rec,
        )
        for rec in selected
    ]
    return MemoryDataset(samples=samples, name="gt-discovery")


def _build_task_prompt(rec: dict) -> str:
    """Build the task prompt combining PR context + query."""
    parts = [
        f"# Task: {rec['title']}",
        "",
        rec["body"][:2000] if rec["body"] else "(no description)",
        "",
        f"## Search Query",
        f"{rec['query_text']}",
    ]
    if rec.get("seeds"):
        parts.append(f"Seeds: {', '.join(rec['seeds'])}")
    if rec.get("pins"):
        parts.append(f"Pins: {', '.join(rec['pins'])}")
    return "\n".join(parts)
