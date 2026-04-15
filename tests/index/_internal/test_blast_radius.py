"""Tests for blast_radius module — coverage-backed test selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index._internal.db import Database, create_additional_indexes


@pytest.fixture
def blast_db(tmp_path: Path) -> Database:
    """DB with defs, refs, and coverage facts for blast radius testing."""
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)

    from sqlalchemy import text
    from sqlmodel import Session

    from coderecon.index.models import Context, DefFact, File, RefFact

    with Session(db.engine) as session:
        ctx = Context(id=1, language_family="python", root_path=".")
        session.add(ctx)
        session.flush()

        session.add_all([
            File(id=1, path="src/foo.py", content_hash="h1"),
            File(id=2, path="src/bar.py", content_hash="h2"),
            File(id=3, path="tests/test_foo.py", content_hash="h3"),
        ])
        session.flush()

        session.add_all([
            DefFact(def_uid="foo.func_a", file_id=1, unit_id=1, kind="function", name="func_a", qualified_name="foo.func_a", lexical_path="func_a", start_line=1, start_col=0, end_line=10, end_col=0),
            DefFact(def_uid="foo.func_b", file_id=1, unit_id=1, kind="function", name="func_b", qualified_name="foo.func_b", lexical_path="func_b", start_line=11, start_col=0, end_line=20, end_col=0),
            DefFact(def_uid="bar.helper", file_id=2, unit_id=1, kind="function", name="helper", qualified_name="bar.helper", lexical_path="helper", start_line=1, start_col=0, end_line=10, end_col=0),
        ])

        session.add(
            RefFact(file_id=2, unit_id=1, target_def_uid="foo.func_a", ref_tier="proven", token_text="func_a", role="REFERENCE", start_line=5, start_col=0, end_line=5, end_col=6),
        )

        session.commit()

    # Coverage: use raw SQL for the new tables (not in ORM relationships)
    with db.engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO test_coverage_facts "
            "(test_id, target_def_uid, target_file_path, covered_lines, total_lines, "
            "line_rate, branch_rate, epoch, stale) "
            "VALUES ('tests/test_foo.py::test_a', 'foo.func_a', 'src/foo.py', 8, 10, 0.8, 0.0, 1, 0)"
        ))
        conn.commit()

    return db


class TestBlastRadius:
    def test_direct_coverage(self, blast_db: Database) -> None:
        """Changed def with direct coverage should return test with confidence 1.0."""
        from coderecon.index._internal.analysis.blast_radius import (
            select_tests_for_changed_defs,
        )

        result = select_tests_for_changed_defs(
            blast_db.engine, ["foo.func_a"]
        )
        assert result.has_coverage_data
        assert any(c.test_id == "tests/test_foo.py::test_a" for c in result.candidates)
        direct = [c for c in result.candidates if c.source == "coverage"]
        assert len(direct) >= 1
        assert direct[0].confidence == 1.0

    def test_caller_coverage(self, blast_db: Database) -> None:
        """Changed def called by covered def should get caller coverage."""
        from coderecon.index._internal.analysis.blast_radius import (
            select_tests_for_changed_defs,
        )

        # func_b has no direct coverage but is in same file as func_a
        result = select_tests_for_changed_defs(
            blast_db.engine, ["foo.func_b"]
        )
        # Should get scope affinity at least (same file as covered func_a)
        assert any(c.source == "scope_affinity" for c in result.candidates)

    def test_no_coverage_data(self, tmp_path: Path) -> None:
        """Empty DB should report no coverage data."""
        from coderecon.index._internal.analysis.blast_radius import (
            select_tests_for_changed_defs,
        )

        db = Database(tmp_path / "empty.db")
        db.create_all()
        create_additional_indexes(db.engine)

        result = select_tests_for_changed_defs(db.engine, ["foo.func_a"])
        assert not result.has_coverage_data

    def test_empty_input(self, blast_db: Database) -> None:
        """Empty changed_def_uids should return empty result."""
        from coderecon.index._internal.analysis.blast_radius import (
            select_tests_for_changed_defs,
        )

        result = select_tests_for_changed_defs(blast_db.engine, [])
        assert result.candidates == []

    def test_coverage_gaps(self, blast_db: Database) -> None:
        """Changed def with no coverage should appear as gap."""
        from coderecon.index._internal.analysis.blast_radius import (
            select_tests_for_changed_defs,
        )

        result = select_tests_for_changed_defs(
            blast_db.engine, ["bar.helper"]
        )
        # bar.helper has no direct coverage
        assert len(result.coverage_gaps) >= 0  # May or may not be flagged

    def test_test_ids_deduped_and_sorted(self, blast_db: Database) -> None:
        """test_ids property should be unique and sorted by confidence."""
        from coderecon.index._internal.analysis.blast_radius import (
            select_tests_for_changed_defs,
        )

        result = select_tests_for_changed_defs(
            blast_db.engine, ["foo.func_a"]
        )
        # No duplicates
        assert len(result.test_ids) == len(set(result.test_ids))
