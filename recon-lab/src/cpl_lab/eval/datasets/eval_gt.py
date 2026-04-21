"""Ground truth dataset for Inspect AI evaluation.

Returns a ``Dataset`` of ``Sample`` objects for use with ``@task``.

Each sample carries ground-truth touched defs, gate label, and query
metadata in ``Sample.metadata``.  The ``input`` field holds the query
text (used only as an identifier — the solver runs the pipeline
in-process).
"""

from __future__ import annotations

import json
from pathlib import Path

from inspect_ai.dataset import Dataset, MemoryDataset, Sample

from cpl_lab.data_manifest import iter_repo_data_dirs


def _def_key(d: dict) -> str:
    """Canonical candidate key: ``path:kind:name:start_line``."""
    return f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"


def eval_gt_dataset(
    data_dir: str = "~/.recon/recon-lab/data",
) -> Dataset:
    """Load ground truth from all available indexed instances.

    Args:
        data_dir: Root data directory (default ``~/.recon/recon-lab/data``).
    """
    data_root = Path(data_dir).expanduser()

    records: list[dict] = []
    for repo_dir in iter_repo_data_dirs(data_root):
        repo_id = repo_dir.name
        gt_dir = repo_dir / "ground_truth"
        queries_file = gt_dir / "queries.jsonl"
        touched_file = gt_dir / "touched_objects.jsonl"
        legacy_file = data_root / repo_id / "ground_truth.jsonl"

        if queries_file.exists() and touched_file.exists():
            _load_from_tables(records, repo_id, queries_file, touched_file)
        elif legacy_file.exists():
            _load_from_legacy_jsonl(records, repo_id, legacy_file)

    if not records:
        msg = (
            f"No ground truth found for eval repos in {data_root}. "
            "Run ground-truth annotation first."
        )
        raise FileNotFoundError(msg)

    samples = [
        Sample(
            id=rec["query_id"],
            input=rec["query_text"],
            metadata=rec,
        )
        for rec in records
    ]
    return MemoryDataset(samples=samples, name="cpl-eval-gt")


def _load_from_tables(
    records: list[dict], repo_id: str, queries_file: Path, touched_file: Path
) -> None:
    # Build set of unmatched (phantom) GT objects from summary.json.
    unmatched: set[tuple[str, str, str, str, str]] = set()
    summary_file = touched_file.parent / "summary.json"
    if summary_file.exists():
        import json as _json

        summary = _json.loads(summary_file.read_text())
        for u in summary.get("unmatched_details", []):
            unmatched.add((
                u.get("task_id", ""),
                u.get("tier", ""),
                u.get("path", ""),
                u.get("name", ""),
                u.get("kind", ""),
            ))

    relevant: dict[str, dict[str, list[str]]] = {}

    for line in touched_file.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        run_id = row.get("run_id", "")
        if run_id == f"{repo_id}__non_ok":
            continue
        task_id = run_id.removeprefix(f"{repo_id}_")
        tier = row.get("tier", "minimum")

        match_key = (task_id, tier, row.get("path", ""),
                     row.get("name", ""), row.get("kind", ""))
        if match_key in unmatched:
            continue

        bucket = relevant.setdefault(task_id, {"minimum": []})
        bucket.setdefault(tier, []).append(_def_key(row))

    for line in queries_file.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        run_id = row.get("run_id", "")
        if run_id == f"{repo_id}__non_ok":
            task_id = "__non_ok"
        else:
            task_id = run_id.removeprefix(f"{repo_id}_")
        tiers = relevant.get(task_id, {"minimum": []})
        gt_edited = tiers.get("minimum", [])

        # Skip OK queries where ALL GT objects were phantoms
        if row.get("label_gate", "OK") == "OK" and not gt_edited:
            continue

        records.append({
            "repo_id": repo_id,
            "task_id": task_id,
            "query_id": row["query_id"],
            "query_text": row["query_text"],
            "query_type": row.get("query_type", "UNKNOWN"),
            "seeds": row.get("seeds", []),
            "pins": row.get("pins", []),
            "gt_edited": gt_edited,
            "gt_read_necessary": [],
            "label_gate": row.get("label_gate", "OK"),
        })


def _load_from_legacy_jsonl(records: list[dict], repo_id: str, legacy_file: Path) -> None:
    for line in legacy_file.read_text().splitlines():
        if not line.strip():
            continue
        task = json.loads(line)
        tid = task.get("task_id", "")
        gt_edited = [_def_key(d) for d in task.get("minimum_sufficient_defs", [])]
        label_gate = task.get("gate_label", "OK" if gt_edited else "UNSAT")

        for qi, q in enumerate(task.get("queries", [])):
            records.append({
                "repo_id": repo_id,
                "task_id": tid,
                "query_id": f"{tid}/Q{qi}",
                "query_text": q["query_text"],
                "query_type": q.get("query_type", "UNKNOWN"),
                "seeds": q.get("seeds", []),
                "pins": q.get("pins", []),
                "gt_edited": gt_edited,
                "gt_read_necessary": [],
                "label_gate": label_gate,
            })
