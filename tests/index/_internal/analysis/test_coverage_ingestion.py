"""Tests for index/_internal/analysis/coverage_ingestion.py."""

from unittest.mock import MagicMock

import pytest

from coderecon.index._internal.analysis.coverage_ingestion import (
    get_coverage_summary,
    get_covering_tests,
    get_uncovered_defs,
    ingest_coverage,
    mark_stale_coverage,
)


@pytest.fixture()
def mock_engine():
    """Create a mock SQLAlchemy engine with a mock connection."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return engine, conn


def test_ingest_coverage_empty_report(mock_engine):
    """ingest_coverage returns 0 when report has no files."""
    engine, conn = mock_engine
    report = MagicMock()
    report.files = {}
    result = ingest_coverage(engine, report, epoch=1)
    assert result == 0


def test_ingest_coverage_file_with_no_lines(mock_engine):
    """ingest_coverage skips files with empty line data."""
    engine, conn = mock_engine
    file_cov = MagicMock()
    file_cov.lines = {}
    report = MagicMock()
    report.files = {"src/foo.py": file_cov}
    result = ingest_coverage(engine, report, epoch=1)
    assert result == 0


def test_mark_stale_coverage_empty_list(mock_engine):
    """mark_stale_coverage returns 0 for empty def list."""
    engine, _ = mock_engine
    result = mark_stale_coverage(engine, [])
    assert result == 0


def test_mark_stale_coverage_updates_rows(mock_engine):
    """mark_stale_coverage executes UPDATE for given def UIDs."""
    engine, conn = mock_engine
    mock_result = MagicMock()
    mock_result.rowcount = 3
    conn.execute.return_value = mock_result
    result = mark_stale_coverage(engine, ["def1", "def2"])
    assert result == 3
    conn.execute.assert_called()
    conn.commit.assert_called_once()


def test_get_covering_tests_empty_list():
    """get_covering_tests returns empty dict for empty input."""
    engine = MagicMock()
    result = get_covering_tests(engine, [])
    assert result == {}


def test_get_covering_tests_returns_mapping(mock_engine):
    """get_covering_tests returns {def_uid: [test_ids]}."""
    engine, conn = mock_engine
    conn.execute.return_value.fetchall.return_value = [
        ("uid1", "test_a"),
        ("uid1", "test_b"),
        ("uid2", "test_c"),
    ]
    result = get_covering_tests(engine, ["uid1", "uid2"])
    assert "uid1" in result
    assert "test_a" in result["uid1"]
    assert "test_b" in result["uid1"]
    assert "test_c" in result["uid2"]


def test_get_uncovered_defs(mock_engine):
    """get_uncovered_defs returns list of uncovered def dicts."""
    engine, conn = mock_engine
    conn.execute.return_value.fetchall.return_value = [
        ("uid1", "my_func", "function", "src/mod.py"),
    ]
    result = get_uncovered_defs(engine)
    assert len(result) == 1
    assert result[0]["def_uid"] == "uid1"
    assert result[0]["name"] == "my_func"


def test_get_coverage_summary_empty(mock_engine):
    """get_coverage_summary returns zeros when no data exists."""
    engine, conn = mock_engine
    conn.execute.return_value.fetchone.side_effect = [None, None]
    result = get_coverage_summary(engine)
    assert result["defs_covered"] == 0
    assert result["total_defs"] == 0
    assert result["coverage_rate"] == 0.0
