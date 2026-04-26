"""Tests for coderecon.ranking.ranker."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.ranking.ranker import Ranker, load_ranker


class TestRanker:
    """Tests for Ranker class."""

    def test_no_model_file_sets_unavailable(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        ranker = Ranker(model_path)
        assert ranker.is_available is False

    def test_score_returns_zeros_when_no_model(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        ranker = Ranker(model_path)
        result = ranker.score([{"f": 1.0}, {"f": 2.0}])
        assert result == [0.0, 0.0]

    def test_score_empty_list_returns_empty(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        ranker = Ranker(model_path)
        result = ranker.score([])
        assert result == []


class TestLoadRanker:
    """Tests for load_ranker factory."""

    def test_load_with_explicit_path(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.lgbm"
        ranker = load_ranker(model_path)
        assert isinstance(ranker, Ranker)

    def test_load_default_path(self) -> None:
        ranker = load_ranker()
        assert isinstance(ranker, Ranker)
