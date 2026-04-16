"""Helpers for per-instance workspace metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SPECIAL_DATA_DIRS = frozenset({"merged", "logs", "index_logs"})
MANIFEST_FILE = "manifest.json"

_SPECIAL_GT_JSON_FILES = frozenset({
    "non_ok_queries.json",
    "summary.json",
})


def iter_task_json_files(gt_dir: Path) -> list[Path]:
    """Return per-task ground-truth JSON files, excluding derived artifacts."""
    if not gt_dir.is_dir():
        return []
    return sorted(
        path
        for path in gt_dir.glob("*.json")
        if path.name not in _SPECIAL_GT_JSON_FILES
    )


def iter_repo_data_dirs(data_dir: Path) -> list[Path]:
    """Return all concrete repo-instance directories under *data_dir*."""
    if not data_dir.is_dir():
        return []
    return sorted(
        path for path in data_dir.iterdir()
        if path.is_dir() and path.name not in SPECIAL_DATA_DIRS
    )


def load_repo_manifest(repo_dir: Path) -> dict[str, Any]:
    """Load repo-instance manifest JSON if present."""
    path = repo_dir / MANIFEST_FILE
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def write_repo_manifest(repo_dir: Path, payload: dict[str, Any]) -> None:
    """Persist repo-instance manifest JSON."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / MANIFEST_FILE).write_text(json.dumps(payload, indent=2, sort_keys=True))


def repo_set_for_dir(repo_dir: Path) -> str | None:
    """Return the repo set recorded for *repo_dir*, if any."""
    repo_set = load_repo_manifest(repo_dir).get("repo_set")
    return repo_set if isinstance(repo_set, str) and repo_set else None


def logical_repo_id_for_dir(repo_dir: Path) -> str:
    """Return the logical repo identifier for *repo_dir*."""
    manifest = load_repo_manifest(repo_dir)
    logical_repo_id = manifest.get("logical_repo_id")
    if isinstance(logical_repo_id, str) and logical_repo_id:
        return logical_repo_id
    return repo_dir.name


def clone_dir_for_dir(repo_dir: Path, clones_dir: Path) -> Path | None:
    """Resolve the clone directory for a repo-instance directory."""
    manifest = load_repo_manifest(repo_dir)
    clone_dir = manifest.get("clone_dir")
    if isinstance(clone_dir, str) and clone_dir:
        return Path(clone_dir)

    from cpl_lab.pipeline.clone import clone_dir_for as resolve_manifest_clone_dir

    return resolve_manifest_clone_dir(repo_dir.name, clones_dir)