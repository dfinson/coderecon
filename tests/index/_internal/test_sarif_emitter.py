"""Tests for SARIF emitter."""

from __future__ import annotations

import json


class TestGateResultToSarif:
    def test_empty_result(self) -> None:
        from coderecon.index._internal.analysis.gate_engine import GateResult
        from coderecon.index._internal.analysis.sarif_emitter import gate_result_to_sarif

        sarif = gate_result_to_sarif(GateResult())
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"]) == 1
        assert sarif["runs"][0]["results"] == []

    def test_with_violations(self) -> None:
        from coderecon.index._internal.analysis.gate_engine import GateResult, GateViolation
        from coderecon.index._internal.analysis.sarif_emitter import gate_result_to_sarif

        result = GateResult(violations=[
            GateViolation(
                rule="lint_clean",
                level="error",
                message="Lint errors found.",
                details={"diagnostics": 5},
            ),
            GateViolation(
                rule="test_debt",
                level="warning",
                message="Missing tests.",
            ),
        ])

        sarif = gate_result_to_sarif(result)
        runs = sarif["runs"]
        assert len(runs) == 1
        results = runs[0]["results"]
        assert len(results) == 2
        assert results[0]["ruleId"] == "governance/lint_clean"
        assert results[0]["level"] == "error"
        assert results[1]["ruleId"] == "governance/test_debt"
        assert results[1]["level"] == "warning"

        rules = runs[0]["tool"]["driver"]["rules"]
        assert len(rules) == 2

    def test_serializable(self) -> None:
        from coderecon.index._internal.analysis.gate_engine import GateResult, GateViolation
        from coderecon.index._internal.analysis.sarif_emitter import gate_result_to_sarif

        result = GateResult(violations=[
            GateViolation(rule="test", level="error", message="msg"),
        ])
        sarif = gate_result_to_sarif(result)
        # Should be JSON serializable
        json.dumps(sarif)


class TestLintDiagnosticsToSarif:
    def test_basic(self) -> None:
        from coderecon.index._internal.analysis.sarif_emitter import lint_diagnostics_to_sarif

        diags = [
            {"path": "src/a.py", "line": 10, "column": 5, "severity": "error", "code": "E501", "message": "line too long"},
            {"path": "src/b.py", "line": 20, "column": 1, "severity": "warning", "code": "W291", "message": "trailing whitespace"},
        ]

        sarif = lint_diagnostics_to_sarif(diags)
        results = sarif["runs"][0]["results"]
        assert len(results) == 2
        assert results[0]["level"] == "error"
        assert results[1]["level"] == "warning"
        assert results[0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 10


class TestWriteSarif:
    def test_writes_file(self, tmp_path) -> None:
        from coderecon.index._internal.analysis.sarif_emitter import write_sarif

        sarif = {"version": "2.1.0", "runs": []}
        path = write_sarif(sarif, tmp_path / "output.sarif")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["version"] == "2.1.0"
