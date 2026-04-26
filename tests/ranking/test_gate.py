"""Tests for coderecon.ranking.gate."""

from __future__ import annotations

from pathlib import Path

from coderecon.ranking.gate import Gate, load_gate
from coderecon.ranking.models import GateLabel


class TestGate:
    """Tests for Gate classifier."""

    def test_no_model_file_sets_unavailable(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        gate = Gate(model_path)
        assert gate.is_available is False

    def test_classify_returns_ok_when_no_model(self, tmp_path: Path) -> None:
        model_path = tmp_path / "nonexistent.lgbm"
        gate = Gate(model_path)
        result = gate.classify({"feature_a": 1.0})
        assert result == GateLabel.OK


class TestLoadGate:
    """Tests for load_gate factory."""

    def test_load_with_explicit_path(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.lgbm"
        gate = load_gate(model_path)
        assert isinstance(gate, Gate)
        assert gate.is_available is False

    def test_load_default_path(self) -> None:
        gate = load_gate()
        assert isinstance(gate, Gate)
