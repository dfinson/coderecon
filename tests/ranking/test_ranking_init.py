from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.ranking import _load_lgb_model


class TestLoadLgbModel:
    def test_returns_none_when_file_missing(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.txt"
        result = _load_lgb_model(missing, "ranker")
        assert result is None

    @patch("coderecon.ranking.lgb", create=True)
    def test_loads_booster_when_file_exists(self, mock_lgb: MagicMock, tmp_path: Path):
        model_file = tmp_path / "model.txt"
        model_file.write_text("dummy")

        sentinel = MagicMock(name="booster")
        mock_lgb.Booster.return_value = sentinel

        with patch.dict("sys.modules", {"lightgbm": mock_lgb}):
            result = _load_lgb_model(model_file, "ranker")

        mock_lgb.Booster.assert_called_once_with(model_file=str(model_file))
        assert result is sentinel

    @patch("coderecon.ranking.lgb", create=True)
    def test_passes_component_name_to_log(self, mock_lgb: MagicMock, tmp_path: Path):
        model_file = tmp_path / "model.txt"
        model_file.write_text("dummy")
        mock_lgb.Booster.return_value = MagicMock()

        with patch.dict("sys.modules", {"lightgbm": mock_lgb}):
            # Smoke test — just ensure it completes without error
            _load_lgb_model(model_file, "cutoff")
