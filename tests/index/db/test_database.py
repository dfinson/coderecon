"""Tests for index/_internal/db/database.py — Database and BulkWriter."""

from unittest.mock import patch

import pytest

from coderecon.index.db.database import (
    BulkWriter,
    Database,
)

@pytest.fixture()
def db(tmp_path):
    """Create a Database instance with a tmp_path SQLite file."""
    db_path = tmp_path / "test.db"
    with patch(
        "coderecon.index.db.database._run_index_migrations"
    ):
        return Database(db_path)

def test_database_constructor(tmp_path):
    """Database sets db_path and creates an engine."""
    db_path = tmp_path / "test.db"
    with patch("coderecon.index.db.database._run_index_migrations"):
        database = Database(db_path)
    assert database.db_path == db_path
    assert database.engine is not None

def test_database_session_yields_session(db):
    """session() yields a session that can execute SQL."""
    from sqlalchemy import text
    with db.session() as session:
        row = session.execute(text("SELECT 1")).fetchone()
        assert row[0] == 1

def test_database_execute_raw(db):
    """execute_raw runs arbitrary SQL."""
    _result = db.execute_raw("SELECT 42 AS answer")
    # result is a CursorResult — just verify it ran without error

def test_database_checkpoint(db):
    """checkpoint() runs WAL checkpoint without error."""
    db.checkpoint("PASSIVE")

def test_database_checkpoint_rejects_invalid_mode(db):
    """checkpoint() raises ValueError for invalid mode."""
    with pytest.raises(ValueError, match="Invalid checkpoint mode"):
        db.checkpoint("INVALID")

def test_bulk_writer_context_manager(db):
    """bulk_writer() yields a BulkWriter and commits on exit."""
    with db.bulk_writer() as writer:
        assert isinstance(writer, BulkWriter)

def test_database_create_all_runs_migrations(tmp_path):
    """create_all() invokes Alembic migrations."""
    db_path = tmp_path / "test.db"
    with patch("coderecon.index.db.database._run_index_migrations") as mock_migrate:
        database = Database(db_path)
        database.create_all()
    assert mock_migrate.call_count == 1

def test_database_immediate_transaction(db):
    """immediate_transaction() yields a session that can execute SQL."""
    from sqlalchemy import text
    with db.immediate_transaction() as session:
        row = session.execute(text("SELECT 1")).fetchone()
        assert row[0] == 1
