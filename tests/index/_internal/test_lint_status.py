"""Tests for lint_status module."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index._internal.db import Database, create_additional_indexes


@pytest.fixture
def lint_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)
    return db


class TestLintStatus:
    def test_persist_and_read(self, lint_db: Database) -> None:
        from coderecon.index._internal.analysis.lint_status import (
            get_file_lint_status,
            persist_lint_status,
        )

        persist_lint_status(
            lint_db.engine, "src/foo.py", "ruff", "lint",
            error_count=2, warning_count=1, info_count=0, epoch=1,
        )

        status = get_file_lint_status(lint_db.engine, "src/foo.py")
        assert len(status) == 1
        assert status[0]["tool_id"] == "ruff"
        assert status[0]["error_count"] == 2
        assert status[0]["warning_count"] == 1

    def test_upsert(self, lint_db: Database) -> None:
        from coderecon.index._internal.analysis.lint_status import (
            get_file_lint_status,
            persist_lint_status,
        )

        persist_lint_status(
            lint_db.engine, "src/foo.py", "ruff", "lint",
            error_count=5, warning_count=0, info_count=0, epoch=1,
        )
        persist_lint_status(
            lint_db.engine, "src/foo.py", "ruff", "lint",
            error_count=0, warning_count=0, info_count=0, epoch=2,
        )

        status = get_file_lint_status(lint_db.engine, "src/foo.py")
        assert len(status) == 1
        assert status[0]["error_count"] == 0  # Updated

    def test_lint_summary(self, lint_db: Database) -> None:
        from coderecon.index._internal.analysis.lint_status import (
            get_lint_summary,
            persist_lint_status,
        )

        persist_lint_status(
            lint_db.engine, "a.py", "ruff", "lint",
            error_count=1, warning_count=2, info_count=0, epoch=1,
        )
        persist_lint_status(
            lint_db.engine, "b.py", "ruff", "lint",
            error_count=0, warning_count=0, info_count=0, epoch=1,
        )

        summary = get_lint_summary(lint_db.engine)
        assert summary["files_checked"] == 2
        assert summary["total_errors"] == 1
        assert summary["total_warnings"] == 2
        assert summary["clean_files"] == 1
