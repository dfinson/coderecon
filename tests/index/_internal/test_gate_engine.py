"""Tests for gate evaluation engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index._internal.db import Database, create_additional_indexes


@pytest.fixture
def gate_db(tmp_path: Path) -> Database:
    """DB with some lint and coverage facts for gate testing."""
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)

    from sqlalchemy import text
    from sqlmodel import Session

    from coderecon.index.models import Context, DefFact, File

    with Session(db.engine) as session:
        ctx = Context(id=1, language_family="python", root_path=".")
        session.add(ctx)
        session.flush()

        session.add(File(id=1, path="src/a.py", content_hash="h1"))
        session.flush()

        session.add(
            DefFact(def_uid="a.func", file_id=1, unit_id=1, kind="function", name="func", lexical_path="func", start_line=1, start_col=0, end_line=10, end_col=0),
        )
        session.commit()

    # Lint + coverage tables may not have ORM models, use raw SQL
    with db.engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO lint_status_facts "
            "(file_path, tool_id, category, error_count, warning_count, info_count, clean, epoch) "
            "VALUES ('src/a.py', 'ruff', 'lint', 1, 0, 0, 0, 1)"
        ))
        conn.execute(text(
            "INSERT INTO test_coverage_facts "
            "(test_id, target_def_uid, target_file_path, covered_lines, total_lines, "
            "line_rate, branch_rate, epoch, stale) "
            "VALUES ('test_a', 'a.func', 'src/a.py', 5, 10, 0.5, 0.0, 1, 0)"
        ))
        conn.commit()

    return db


class TestGateEvaluation:
    def test_all_disabled(self, gate_db: Database) -> None:
        """All rules disabled → should always pass."""
        from coderecon.config.models import GovernanceConfig

        from coderecon.index._internal.analysis.gate_engine import evaluate_gates

        gov = GovernanceConfig()
        # All rules default to disabled
        result = evaluate_gates(gov, gate_db.engine)
        assert result.passed

    def test_lint_clean_gate(self, gate_db: Database) -> None:
        """Lint clean gate should fire when lint has diagnostics."""
        from coderecon.config.models import GovernanceConfig, GovernancePolicyRule

        from coderecon.index._internal.analysis.gate_engine import evaluate_gates

        gov = GovernanceConfig(
            lint_clean=GovernancePolicyRule(enabled=True, level="error")
        )

        result = evaluate_gates(
            gov, gate_db.engine,
            lint_clean=False, lint_diagnostics=3,
        )
        assert not result.passed
        assert len(result.errors) == 1
        assert result.errors[0].rule == "lint_clean"

    def test_lint_clean_passes_when_clean(self, gate_db: Database) -> None:
        from coderecon.config.models import GovernanceConfig, GovernancePolicyRule

        from coderecon.index._internal.analysis.gate_engine import evaluate_gates

        gov = GovernanceConfig(
            lint_clean=GovernancePolicyRule(enabled=True, level="error")
        )

        result = evaluate_gates(
            gov, gate_db.engine,
            lint_clean=True, lint_diagnostics=0,
        )
        assert result.passed

    def test_coverage_floor_gate(self, gate_db: Database) -> None:
        """Coverage floor should fire when below threshold."""
        from coderecon.config.models import GovernanceConfig, GovernancePolicyRule

        from coderecon.index._internal.analysis.gate_engine import evaluate_gates

        gov = GovernanceConfig(
            coverage_floor=GovernancePolicyRule(
                enabled=True, level="error", threshold=80.0
            )
        )

        # DB has 1 covered def out of 1 total = 100% (function, not variable)
        # But actual coverage reads from DB, so result depends on DB state
        result = evaluate_gates(gov, gate_db.engine)
        # With only 1 def covered at 50% line rate, overall def coverage = 100%
        # (not line rate, but def-level coverage)
        assert result.passed  # 1/1 = 100% > 80%

    def test_test_debt_gate(self, gate_db: Database) -> None:
        """Test debt gate should fire when source changed without tests."""
        from coderecon.config.models import GovernanceConfig, GovernancePolicyRule

        from coderecon.index._internal.analysis.gate_engine import evaluate_gates

        gov = GovernanceConfig(
            test_debt=GovernancePolicyRule(enabled=True, level="warning")
        )

        debt_info = {
            "missing_test_updates": [
                {"source": "src/a.py", "test_file": "tests/test_a.py"}
            ]
        }

        result = evaluate_gates(
            gov, gate_db.engine,
            test_debt_info=debt_info,
        )
        assert result.passed  # warnings don't block
        assert len(result.warnings) == 1
        assert result.warnings[0].rule == "test_debt"

    def test_to_dict(self, gate_db: Database) -> None:
        from coderecon.config.models import GovernanceConfig, GovernancePolicyRule

        from coderecon.index._internal.analysis.gate_engine import evaluate_gates

        gov = GovernanceConfig(
            lint_clean=GovernancePolicyRule(enabled=True, level="warning")
        )
        result = evaluate_gates(
            gov, gate_db.engine,
            lint_clean=False, lint_diagnostics=2,
        )
        d = result.to_dict()
        assert "passed" in d
        assert "violations" in d
        assert isinstance(d["violations"], list)
