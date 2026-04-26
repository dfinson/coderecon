"""Tests for index/_internal/indexing/materialize.py — export surfaces, thunks, anchor groups."""

from unittest.mock import MagicMock, patch

import pytest

from coderecon.index._internal.indexing.materialize import (
    materialize_all,
    materialize_anchor_groups,
    materialize_exports,
    materialize_thunks,
)


@pytest.fixture()
def mock_db():
    """Create a mock Database with a mock session context manager."""
    db = MagicMock()
    session = MagicMock()
    db.session.return_value.__enter__ = MagicMock(return_value=session)
    db.session.return_value.__exit__ = MagicMock(return_value=False)
    return db, session


def test_materialize_exports_no_defs(mock_db):
    """materialize_exports returns 0 when no def_facts exist."""
    db, session = mock_db
    session.execute.return_value.fetchall.return_value = []
    result = materialize_exports(db)
    assert result == 0


def test_materialize_thunks_no_imports(mock_db):
    """materialize_thunks returns 0 when no re-export imports exist."""
    db, session = mock_db
    # file_unit_rows, imp_file_rows, path_rows, js_rows all empty
    session.execute.return_value.fetchall.return_value = []
    result = materialize_thunks(db)
    assert result == 0


def test_materialize_anchor_groups_no_unresolved(mock_db):
    """materialize_anchor_groups returns 0 when no unresolved refs exist."""
    db, session = mock_db
    session.execute.return_value.fetchall.return_value = []
    result = materialize_anchor_groups(db)
    assert result == 0


def test_materialize_all_calls_all_three():
    """materialize_all invokes exports, thunks, and anchor_groups."""
    db = MagicMock()
    with patch(
        "coderecon.index._internal.indexing.materialize.materialize_exports", return_value=5
    ) as mock_exp, patch(
        "coderecon.index._internal.indexing.materialize.materialize_thunks", return_value=3
    ) as mock_thunks, patch(
        "coderecon.index._internal.indexing.materialize.materialize_anchor_groups", return_value=2
    ) as mock_ag:
        result = materialize_all(db)
    assert result == {"surfaces": 5, "thunks": 3, "anchor_groups": 2}
    mock_exp.assert_called_once_with(db, context_ids=None)
    mock_thunks.assert_called_once_with(db, context_ids=None)
    mock_ag.assert_called_once_with(db, context_ids=None)


def test_materialize_all_passes_context_ids():
    """materialize_all forwards context_ids to all sub-functions."""
    db = MagicMock()
    ids = [1, 2]
    with patch(
        "coderecon.index._internal.indexing.materialize.materialize_exports", return_value=0
    ) as mock_exp, patch(
        "coderecon.index._internal.indexing.materialize.materialize_thunks", return_value=0
    ) as mock_thunks, patch(
        "coderecon.index._internal.indexing.materialize.materialize_anchor_groups", return_value=0
    ) as mock_ag:
        materialize_all(db, context_ids=ids)
    mock_exp.assert_called_once_with(db, context_ids=ids)
    mock_thunks.assert_called_once_with(db, context_ids=ids)
    mock_ag.assert_called_once_with(db, context_ids=ids)
