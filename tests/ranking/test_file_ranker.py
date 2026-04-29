"""Tests for coderecon.ranking.file_ranker."""

from __future__ import annotations

from pathlib import Path

from coderecon.ranking.file_ranker import FileRanker, load_file_ranker

class TestFileRanker:
    """Tests for FileRanker class."""

    def test_no_model_file_sets_unavailable(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        ranker = FileRanker(model_path)
        assert ranker.is_available is False

    def test_score_returns_zeros_when_no_model(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        ranker = FileRanker(model_path)
        result = ranker.score([{"f": 1.0}, {"f": 2.0}])
        assert result == [0.0, 0.0]

    def test_score_empty_list_returns_empty(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        ranker = FileRanker(model_path)
        result = ranker.score([])
        assert result == []

class TestLoadFileRanker:
    """Tests for load_file_ranker factory."""

    def test_load_with_explicit_path(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.lgbm"
        ranker = load_file_ranker(model_path)
        assert isinstance(ranker, FileRanker)

    def test_load_default_path(self) -> None:
        ranker = load_file_ranker()
        assert isinstance(ranker, FileRanker)
