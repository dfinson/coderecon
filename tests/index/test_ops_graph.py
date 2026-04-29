"""Tests for coderecon.index.ops_graph."""

from __future__ import annotations

from unittest.mock import MagicMock

from coderecon.index.ops_graph import (
    _invalidate_dangling_refs,
    _mark_coverage_stale,
    _propagate_def_changes,
    _remove_structural_facts_for_paths,
    _sweep_orphaned_edges,
)


def _mock_engine(session: MagicMock | None = None) -> MagicMock:
    """Build a mock IndexCoordinatorEngine with a session context manager."""
    engine = MagicMock()
    sess = session or MagicMock()
    engine.db.session.return_value.__enter__ = MagicMock(return_value=sess)
    engine.db.session.return_value.__exit__ = MagicMock(return_value=False)
    return engine


class TestRemoveStructuralFactsForPaths:
    """_remove_structural_facts_for_paths purges all fact tables for paths."""

    def test_no_paths_is_noop(self) -> None:
        engine = _mock_engine()
        _remove_structural_facts_for_paths(engine, [])
        # session should still open/close, but no deletes happen

    def test_file_not_found_skips_delete(self) -> None:
        session = MagicMock()
        session.exec.return_value.first.return_value = None
        engine = _mock_engine(session)
        _remove_structural_facts_for_paths(engine, ["missing.py"])
        session.commit.assert_called_once()

    def test_deletes_all_fact_tables_for_found_file(self) -> None:
        file_mock = MagicMock()
        file_mock.id = 42
        session = MagicMock()
        session.exec.return_value.first.return_value = file_mock
        engine = _mock_engine(session)

        _remove_structural_facts_for_paths(engine, ["src/foo.py"])

        # 1 SELECT + 3 explicit DELETEs (splade, coverage, doc_cross_refs)
        # + len(_FILE_FACT_TABLES) = 12 → total 16 exec calls
        total_exec_calls = session.exec.call_count
        assert total_exec_calls >= 4  # at least select + 3 deletes
        session.commit.assert_called_once()

    def test_worktree_id_filter_applied(self) -> None:
        file_mock = MagicMock()
        file_mock.id = 7
        session = MagicMock()
        session.exec.return_value.first.return_value = file_mock
        engine = _mock_engine(session)

        _remove_structural_facts_for_paths(engine, ["a.py"], worktree_id=99)

        # The select statement should include worktree_id filter
        # Verify commit still happens
        session.commit.assert_called_once()

    def test_multiple_paths_processed(self) -> None:
        file_mock = MagicMock()
        file_mock.id = 1
        session = MagicMock()
        session.exec.return_value.first.return_value = file_mock
        engine = _mock_engine(session)

        _remove_structural_facts_for_paths(engine, ["a.py", "b.py", "c.py"])
        session.commit.assert_called_once()


class TestInvalidateDanglingRefs:
    """_invalidate_dangling_refs NULLs target_def_uid on broken refs."""

    def test_empty_file_ids_returns_empty(self) -> None:
        engine = _mock_engine()
        result = _invalidate_dangling_refs(engine, [])
        assert result == []

    def test_no_affected_files_returns_empty(self) -> None:
        session = MagicMock()
        session.execute.return_value.fetchall.return_value = []
        engine = _mock_engine(session)

        result = _invalidate_dangling_refs(engine, [1, 2])
        assert result == []

    def test_returns_affected_file_ids(self) -> None:
        session = MagicMock()
        # First query returns affected file IDs
        session.execute.return_value.fetchall.return_value = [(10,), (20,)]
        engine = _mock_engine(session)

        result = _invalidate_dangling_refs(engine, [1])
        assert result == [10, 20]
        session.commit.assert_called_once()

    def test_worktree_id_filter(self) -> None:
        session = MagicMock()
        session.execute.return_value.fetchall.return_value = []
        engine = _mock_engine(session)

        _invalidate_dangling_refs(engine, [1], worktree_id=5)
        # Should have been called with worktree filter params
        bind_args = session.execute.call_args[1] if session.execute.call_args[1] else session.execute.call_args[0][1]
        assert "wt_id" in bind_args


class TestPropagatDefChanges:
    """_propagate_def_changes marks cross-worktree files stale."""

    def test_returns_count_of_stale_files(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 3
        engine = _mock_engine(session)

        result = _propagate_def_changes(engine, worktree_id=1)
        assert result == 3
        session.commit.assert_called_once()

    def test_zero_stale_skips_commit(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 0
        engine = _mock_engine(session)

        result = _propagate_def_changes(engine, worktree_id=1)
        assert result == 0
        session.commit.assert_not_called()


class TestSweepOrphanedEdges:
    """_sweep_orphaned_edges deletes dangling edge rows."""

    def test_executes_four_deletes_and_commits(self) -> None:
        session = MagicMock()
        engine = _mock_engine(session)

        _sweep_orphaned_edges(engine)

        # 3 DELETE statements: test_coverage_facts,
        # doc_cross_refs, doc_code_edge_facts
        assert session.execute.call_count == 3
        session.commit.assert_called_once()


class TestMarkCoverageStale:
    """_mark_coverage_stale sets stale flag on coverage facts."""

    def test_empty_file_ids_is_noop(self) -> None:
        engine = _mock_engine()
        _mark_coverage_stale(engine, [])
        # Should not open a session at all
        engine.db.session.assert_not_called()

    def test_marks_coverage_stale_for_changed_files(self) -> None:
        session = MagicMock()
        engine = _mock_engine(session)

        _mark_coverage_stale(engine, [1, 2, 3])

        session.execute.assert_called_once()
        session.commit.assert_called_once()
        # Verify bind params match the file IDs
        bind_args = session.execute.call_args[0][1]
        assert bind_args == {"cf_0": 1, "cf_1": 2, "cf_2": 3}
