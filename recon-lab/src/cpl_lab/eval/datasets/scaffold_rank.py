"""Scaffold reranking dataset — loads static JSONL fixture.

Returns a ``Dataset`` of ``Sample`` objects for use with ``@task``.

Each sample carries candidates, GT keys, and problem statement in
``Sample.metadata``.  The ``input`` field holds the problem statement.

Build the fixture first (no recon index required at eval time):
    python -m cpl_lab.build_scaffold_rerank_data
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inspect_ai.dataset import Dataset, MemoryDataset, Sample


def scaffold_rank_dataset(
    data_file: str = "~/.recon/recon-lab/data/scaffold_rerank_data.jsonl",
    max_records: int = 0,
    min_gt_edited: int = 1,
) -> Dataset:
    """Load scaffold reranking fixture.

    Args:
        data_file:  Path to the JSONL fixture produced by
            ``build_scaffold_rerank_data.py``.
        max_records: Cap on records loaded (0 = all).
        min_gt_edited: Skip tasks with fewer edited GT defs than this.
    """
    data_path = Path(data_file).expanduser()
    if not data_path.exists():
        raise FileNotFoundError(
            f"Scaffold reranking fixture not found: {data_path}\n"
            "Build it first: python -m cpl_lab.build_scaffold_rerank_data"
        )

    records: list[dict[str, Any]] = []
    for line in data_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if len(rec.get("gt_edited_keys", [])) < min_gt_edited:
            continue
        if not rec.get("candidates"):
            continue
        records.append(rec)
        if max_records > 0 and len(records) >= max_records:
            break

    if not records:
        raise RuntimeError(
            f"No valid records loaded from {data_path}. "
            "Check min_gt_edited or regenerate the fixture."
        )

    samples = [
        Sample(
            id=rec.get("task_id", str(i)),
            input=rec.get("problem_statement", ""),
            metadata=rec,
        )
        for i, rec in enumerate(records)
    ]
    return MemoryDataset(samples=samples, name="scaffold-rank")
