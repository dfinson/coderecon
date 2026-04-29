"""Ranking model version and manifest access.

Provides version constants and runtime access to the model manifest
(training metadata, metrics, dataset provenance).

Version scheme:
    Dataset: ds-YYYY.N  (ground truth generation)
    Model:   m-YYYY.N   (trained model artifacts)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from coderecon.adapters.files.ops import atomic_write_text

log = structlog.get_logger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_MANIFEST_PATH = _DATA_DIR / "manifest.json"

@dataclass(frozen=True)
class RankingManifest:
    """Metadata about the shipped ranking models."""

    model_version: str | None
    dataset_version: str | None
    trained_at: str | None
    git_sha: str | None
    metrics: dict[str, float] = field(default_factory=dict)
    repos_trained: int = 0
    queries_trained: int = 0
    notes: str = ""

    @property
    def is_trained(self) -> bool:
        """Return True if models have been trained (not placeholder)."""
        return self.model_version is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for status endpoint / logging."""
        return {
            "model_version": self.model_version,
            "dataset_version": self.dataset_version,
            "trained_at": self.trained_at,
            "git_sha": self.git_sha,
            "metrics": self.metrics,
            "repos_trained": self.repos_trained,
            "queries_trained": self.queries_trained,
        }

def load_manifest() -> RankingManifest:
    """Load the ranking manifest from package data.

    Returns a placeholder manifest if the file doesn't exist or is
    malformed (models not yet trained).
    """
    if not _MANIFEST_PATH.exists():
        log.debug("ranking.manifest.not_found", path=str(_MANIFEST_PATH))
        return RankingManifest(model_version=None, dataset_version=None,
                               trained_at=None, git_sha=None)

    try:
        data = json.loads(_MANIFEST_PATH.read_text())
        manifest = RankingManifest(
            model_version=data.get("model_version"),
            dataset_version=data.get("dataset_version"),
            trained_at=data.get("trained_at"),
            git_sha=data.get("git_sha"),
            metrics=data.get("metrics", {}),
            repos_trained=data.get("repos_trained", 0),
            queries_trained=data.get("queries_trained", 0),
            notes=data.get("notes", ""),
        )
        if manifest.is_trained:
            log.debug(
                "ranking.manifest.loaded",
                model=manifest.model_version,
                dataset=manifest.dataset_version,
            )
        return manifest
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("ranking.manifest.parse_error", error=str(e))
        return RankingManifest(model_version=None, dataset_version=None,
                               trained_at=None, git_sha=None)

def write_manifest(manifest: RankingManifest) -> None:
    """Write manifest to package data (called by training pipeline)."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_MANIFEST_PATH, json.dumps({
        "model_version": manifest.model_version,
        "dataset_version": manifest.dataset_version,
        "trained_at": manifest.trained_at,
        "git_sha": manifest.git_sha,
        "metrics": manifest.metrics,
        "repos_trained": manifest.repos_trained,
        "queries_trained": manifest.queries_trained,
        "notes": manifest.notes,
    }, indent=2) + "\n")
    log.debug("ranking.manifest.written", path=str(_MANIFEST_PATH))
