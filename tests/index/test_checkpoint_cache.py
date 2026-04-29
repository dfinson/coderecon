"""Tests for checkpoint fact caching."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index.db import Database, create_additional_indexes

@pytest.fixture
def cache_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)

    from sqlalchemy import text

    with db.engine.connect() as conn:
        # Lint facts at epoch 1
        conn.execute(text(
            "INSERT INTO lint_status_facts "
            "(file_path, tool_id, category, error_count, warning_count, info_count, clean, epoch) "
            "VALUES ('src/a.py', 'ruff', 'lint', 0, 0, 0, 1, 1)"
        ))
        conn.execute(text(
            "INSERT INTO lint_status_facts "
            "(file_path, tool_id, category, error_count, warning_count, info_count, clean, epoch) "
            "VALUES ('src/b.py', 'ruff', 'lint', 2, 1, 0, 0, 1)"
        ))

        conn.commit()

    return db

class TestTryReadLintFacts:
    def test_all_files_cached_clean(self, cache_db: Database) -> None:
        from coderecon.mcp.tools._checkpoint_cache import try_read_lint_facts

        result = try_read_lint_facts(cache_db.engine, ["src/a.py"], current_epoch=1)
        assert result is not None
        assert result.clean
        assert result.total_errors == 0

    def test_all_files_cached_dirty(self, cache_db: Database) -> None:
        from coderecon.mcp.tools._checkpoint_cache import try_read_lint_facts

        result = try_read_lint_facts(cache_db.engine, ["src/b.py"], current_epoch=1)
        assert result is not None
        assert not result.clean
        assert result.total_errors == 2

    def test_missing_file(self, cache_db: Database) -> None:
        from coderecon.mcp.tools._checkpoint_cache import try_read_lint_facts

        result = try_read_lint_facts(
            cache_db.engine, ["src/a.py", "src/missing.py"], current_epoch=1
        )
        assert result is None  # Missing file → can't use cache

    def test_wrong_epoch(self, cache_db: Database) -> None:
        from coderecon.mcp.tools._checkpoint_cache import try_read_lint_facts

        result = try_read_lint_facts(cache_db.engine, ["src/a.py"], current_epoch=2)
        assert result is None  # Wrong epoch → stale

    def test_empty_files(self, cache_db: Database) -> None:
        from coderecon.mcp.tools._checkpoint_cache import try_read_lint_facts

        result = try_read_lint_facts(cache_db.engine, [], current_epoch=1)
        assert result is not None
        assert result.clean
        assert result.files_checked == 0

class TestTryReadTestFacts:
    def test_no_facts(self, cache_db: Database) -> None:
        from coderecon.mcp.tools._checkpoint_cache import try_read_test_facts

        result = try_read_test_facts(cache_db.engine, ["nonexistent.uid"])
        assert result is None

    def test_empty_input(self, cache_db: Database) -> None:
        from coderecon.mcp.tools._checkpoint_cache import try_read_test_facts

        result = try_read_test_facts(cache_db.engine, [])
        assert result is None
