"""Eval-set ground truth dataset — loads GT from held-out eval repos.

Registered as ``@dataset("cpl-eval-gt")`` for EVEE evaluation.

Each record is a (repo_id, task_id, query) triple with ground-truth
touched defs and gate label. Loaded from per-repo ground truth artifacts
produced by the mining pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from evee import dataset

from cpl_lab.data_manifest import iter_repo_data_dirs, repo_set_for_dir


def _def_key(d: dict) -> str:
    """Canonical candidate key: ``path:kind:name:start_line``."""
    return f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"


@dataset("cpl-eval-gt")
class EvalGroundTruthDataset:
    """Loads ground truth for the held-out eval repo set.

    Args:
        data_dir: Root data directory (default ``~/.recon/recon-lab/data``).
    """

    def __init__(self, data_dir: str = "~/.recon/recon-lab/data", **kwargs: object) -> None:
        data_root = Path(data_dir).expanduser()

        self.records: list[dict] = []
        for repo_dir in iter_repo_data_dirs(data_root):
            if repo_set_for_dir(repo_dir) != "eval":
                continue
            repo_id = repo_dir.name
            gt_dir = repo_dir / "ground_truth"
            queries_file = gt_dir / "queries.jsonl"
            touched_file = gt_dir / "touched_objects.jsonl"
            legacy_file = data_root / repo_id / "ground_truth.jsonl"

            if queries_file.exists() and touched_file.exists():
                self._load_from_tables(repo_id, queries_file, touched_file)
            elif legacy_file.exists():
                self._load_from_legacy_jsonl(repo_id, legacy_file)

        if not self.records:
            msg = (
                f"No ground truth found for eval repos in {data_root}. "
                "Run ground-truth annotation first."
            )
            raise FileNotFoundError(msg)

    def _load_from_tables(self, repo_id: str, queries_file: Path, touched_file: Path) -> None:
        relevant: dict[str, dict[str, list[str]]] = {}

        for line in touched_file.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            run_id = row.get("run_id", "")
            if run_id == f"{repo_id}__non_ok":
                continue
            task_id = run_id.removeprefix(f"{repo_id}_")
            bucket = relevant.setdefault(task_id, {"minimum": [], "thrash_preventing": []})
            bucket.setdefault(row.get("tier", "thrash_preventing"), []).append(_def_key(row))

        for line in queries_file.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            run_id = row.get("run_id", "")
            if run_id == f"{repo_id}__non_ok":
                task_id = "__non_ok"
            else:
                task_id = run_id.removeprefix(f"{repo_id}_")
            tiers = relevant.get(task_id, {"minimum": [], "thrash_preventing": []})
            self.records.append({
                "repo_id": repo_id,
                "task_id": task_id,
                "query_id": row["query_id"],
                "query_text": row["query_text"],
                "query_type": row.get("query_type", "UNKNOWN"),
                "seeds": row.get("seeds", []),
                "pins": row.get("pins", []),
                "gt_edited": tiers.get("minimum", []),
                "gt_read_necessary": tiers.get("thrash_preventing", []),
                "label_gate": row.get("label_gate", "OK"),
            })

    def _load_from_legacy_jsonl(self, repo_id: str, legacy_file: Path) -> None:
        for line in legacy_file.read_text().splitlines():
            if not line.strip():
                continue
            task = json.loads(line)
            tid = task.get("task_id", "")
            gt_edited = [_def_key(d) for d in task.get("minimum_sufficient_defs", [])]
            gt_read = [_def_key(d) for d in task.get("thrash_preventing_defs", [])]
            label_gate = task.get("gate_label", "OK" if gt_edited else "UNSAT")

            for qi, q in enumerate(task.get("queries", [])):
                self.records.append({
                    "repo_id": repo_id,
                    "task_id": tid,
                    "query_id": f"{tid}/Q{qi}",
                    "query_text": q["query_text"],
                    "query_type": q.get("query_type", "UNKNOWN"),
                    "seeds": q.get("seeds", []),
                    "pins": q.get("pins", []),
                    "gt_edited": gt_edited,
                    "gt_read_necessary": gt_read,
                    "label_gate": label_gate,
                })

    def __iter__(self):
        yield from self.records

    def __len__(self) -> int:
        return len(self.records)
