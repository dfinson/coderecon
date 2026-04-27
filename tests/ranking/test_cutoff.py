"""Tests for coderecon.ranking.cutoff."""

from __future__ import annotations

from pathlib import Path

from coderecon.ranking.cutoff import Cutoff, load_cutoff

class TestCutoff:
    """Tests for Cutoff class."""

    def test_no_model_file_sets_unavailable(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        cutoff = Cutoff(model_path)
        assert cutoff.is_available is False

    def test_predict_returns_default_when_no_model(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        cutoff = Cutoff(model_path)
        result = cutoff.predict({"feature_a": 1.0})
        assert result == 20  # _DEFAULT_N

class TestLoadCutoff:
    """Tests for load_cutoff factory."""

    def test_load_with_explicit_path(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.lgbm"
        cutoff = load_cutoff(model_path)
        assert isinstance(cutoff, Cutoff)
        assert cutoff.is_available is False

    def test_load_default_path(self) -> None:
        cutoff = load_cutoff()
        assert isinstance(cutoff, Cutoff)
        # Default path likely doesn't exist in test environment
