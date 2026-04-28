"""Tests for ranking module load_lgb_model."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from coderecon.ranking import _load_lgb_model


class TestLoadLgbModel:
    def test_missing_file_returns_none(self) -> None:
        result = _load_lgb_model(Path("/nonexistent/model.txt"), "ranker")
        assert result is None

    def test_missing_file_logs_warning(self) -> None:
        with patch("coderecon.ranking._log") as mock_log:
            _load_lgb_model(Path("/nonexistent/model.txt"), "ranker")
            mock_log.warning.assert_called_once()
