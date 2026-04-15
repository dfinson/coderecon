"""File ranker — LightGBM LambdaMART model (Stage 1).

Scores P(relevant | query, file) by aggregating def-level signals
to file level.  High-scoring files pass through to the def ranker
(Stage 2) while low-scoring files are pruned.
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Any

log = structlog.get_logger(__name__)


class FileRanker:
    """LambdaMART file ranker backed by a serialized LightGBM model."""

    def __init__(self, model_path: Path) -> None:
        self._model = None
        if model_path.exists():
            import lightgbm as lgb

            self._model = lgb.Booster(model_file=str(model_path))
            log.info("ranking.file_ranker.loaded", path=str(model_path))
        else:
            log.warning("ranking.file_ranker.no_model", path=str(model_path))

    @property
    def is_available(self) -> bool:
        return self._model is not None

    def score(self, file_features: list[dict[str, Any]]) -> list[float]:
        """Score each file. Returns scores in input order."""
        if self._model is None:
            # Fallback: return zero scores
            return [0.0 for f in file_features]

        if not file_features:
            return []

        import numpy as np

        feature_names = self._model.feature_name()
        X = np.array([
            [f.get(name, 0) for name in feature_names]
            for f in file_features
        ])
        return self._model.predict(X).tolist()


def load_file_ranker(model_path: Path | None = None) -> FileRanker:
    """Load the file ranker from package data or an explicit path."""
    if model_path is None:
        model_path = Path(__file__).parent / "data" / "file_ranker.lgbm"
    return FileRanker(model_path)
