"""Ranking system runtime inference.

Loads serialized LightGBM models (ranker, cutoff, gate) and scores
candidate DefFacts from raw retrieval signals.  Ships as package data
with coderecon — model artifacts live in ``ranking/data/``.

Public API
----------
rank_candidates : Score and cut a raw-signal candidate pool.
classify_gate   : Classify a (query, repo) pair before ranking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


def _load_lgb_model(model_path: Path, component: str) -> Any:
    """Load a LightGBM Booster from *model_path*.

    Returns ``None`` when the file is missing.
    """
    if model_path.exists():
        import lightgbm as lgb

        model = lgb.Booster(model_file=str(model_path))
        _log.info(f"ranking.{component}.loaded", path=str(model_path))
        return model
    _log.warning(f"ranking.{component}.no_model", path=str(model_path))
    return None
