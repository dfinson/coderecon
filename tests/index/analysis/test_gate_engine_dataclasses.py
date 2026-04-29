"""Tests for index/_internal/analysis/gate_engine.py — governance gate evaluation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from coderecon.index.analysis.gate_engine import (
    GateResult,
    GateViolation,
    evaluate_gates,
)

def _rule(enabled: bool = True, level: str = "error", threshold: float = 80.0, message: str = "fail") -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, level=level, threshold=threshold, message=message)

def _governance(**overrides: object) -> SimpleNamespace:
    defaults = {
        "coverage_floor": _rule(enabled=False),
        "lint_clean": _rule(enabled=False),
        "no_new_cycles": _rule(enabled=False),
        "test_debt": _rule(enabled=False),
        "coverage_regression": _rule(enabled=False),
        "centrality_impact": _rule(enabled=False),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)

# ── Data class tests ──────────────────────────────────────────────

class TestGateViolation:
    def test_construction(self) -> None:
        v = GateViolation(rule="test", level="error", message="bad")
        assert v.rule == "test"
        assert v.level == "error"
        assert v.details is None

class TestGateResult:
    def test_empty_passes(self) -> None:
        r = GateResult()
        assert r.passed is True
        assert r.errors == []
        assert r.warnings == []

    def test_error_fails(self) -> None:
        r = GateResult(violations=[
            GateViolation(rule="a", level="error", message="x"),
        ])
        assert r.passed is False
        assert len(r.errors) == 1

    def test_warning_only_passes(self) -> None:
        r = GateResult(violations=[
            GateViolation(rule="a", level="warning", message="w"),
        ])
        assert r.passed is True
        assert len(r.warnings) == 1

    def test_to_dict(self) -> None:
        r = GateResult(violations=[
            GateViolation(rule="x", level="error", message="m", details={"k": 1}),
            GateViolation(rule="y", level="warning", message="w"),
        ])
        d = r.to_dict()
        assert d["passed"] is False
        assert d["error_count"] == 1
        assert d["warning_count"] == 1
        assert len(d["violations"]) == 2
        assert d["violations"][0]["details"] == {"k": 1}
        assert "details" not in d["violations"][1]

# ── evaluate_gates tests ──────────────────────────────────────────

class TestEvaluateGates:
    def test_all_disabled_passes(self) -> None:
        result = evaluate_gates(_governance(), MagicMock())
        assert result.passed is True
        assert len(result.violations) == 0

    def test_lint_clean_violation(self) -> None:
        gov = _governance(lint_clean=_rule(enabled=True, level="error", message="Lint dirty"))
        result = evaluate_gates(gov, MagicMock(), lint_clean=False, lint_diagnostics=3)
        assert not result.passed
        assert result.errors[0].rule == "lint_clean"
        assert result.errors[0].details == {"diagnostics": 3}

    def test_lint_clean_passes_when_clean(self) -> None:
        gov = _governance(lint_clean=_rule(enabled=True))
        result = evaluate_gates(gov, MagicMock(), lint_clean=True, lint_diagnostics=0)
        assert result.passed

    def test_test_debt_violation(self) -> None:
        gov = _governance(test_debt=_rule(enabled=True, level="warning", message="Debt"))
        debt_info = {"missing_test_updates": ["a.py", "b.py"]}
        result = evaluate_gates(gov, MagicMock(), test_debt_info=debt_info)
        assert result.passed  # warnings don't fail
        assert len(result.warnings) == 1
        assert result.warnings[0].rule == "test_debt"

    def test_test_debt_no_missing_passes(self) -> None:
        gov = _governance(test_debt=_rule(enabled=True))
        debt_info: dict[str, list[str]] = {"missing_test_updates": []}
        result = evaluate_gates(gov, MagicMock(), test_debt_info=debt_info)
        assert result.passed
        assert len(result.violations) == 0
