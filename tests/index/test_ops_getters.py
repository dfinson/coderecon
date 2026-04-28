"""Tests for index getter operations (ops_getters.py).

All DB access is mocked — no real SQLite needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from coderecon.index.models import (
    Context,
    DefFact,
    File,
    ImportFact,
    IndexedCoverageCapability,
    IndexedLintTool,
    ProbeStatus,
    RefFact,
    TestTarget,
)
from coderecon.index.ops_getters import (
    await_epoch,
    close,
    get_all_defs,
    get_all_references,
    get_callees,
    get_contexts,
    get_coverage_capability,
    get_coverage_gaps,
    get_current_epoch,
    get_def,
    get_file_imports,
    get_file_state,
    get_file_stats,
    get_indexed_file_count,
    get_indexed_files,
    get_lint_tools,
    get_references,
    get_test_targets,
    publish_epoch,
)


def _mock_engine(session_results=None):
    """Build a mock IndexCoordinatorEngine with a fake DB session."""
    engine = MagicMock()
    engine.wait_for_freshness = AsyncMock()
    engine.repo_root = Path("/repo")

    session = MagicMock()
    # session.exec(...).first() / .all() / .one() return the configured value
    if session_results is not None:
        session.exec.return_value.first.return_value = session_results
        session.exec.return_value.all.return_value = (
            session_results if isinstance(session_results, list) else [session_results]
        )
        session.exec.return_value.one.return_value = session_results
    else:
        session.exec.return_value.first.return_value = None
        session.exec.return_value.all.return_value = []
        session.exec.return_value.one.return_value = 0

    engine.db.session.return_value.__enter__ = MagicMock(return_value=session)
    engine.db.session.return_value.__exit__ = MagicMock(return_value=False)
    return engine, session


# ── get_def / get_all_defs ────────────────────────────────────────


class TestGetDef:
    @pytest.mark.asyncio
    async def test_returns_first_match(self) -> None:
        fake_def = MagicMock(spec=DefFact)
        fake_def.name = "my_func"
        engine, _ = _mock_engine(fake_def)
        result = await get_def(engine, "my_func")
        assert result is fake_def
        engine.wait_for_freshness.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self) -> None:
        engine, _ = _mock_engine(None)
        result = await get_def(engine, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_context_id(self) -> None:
        engine, _ = _mock_engine(None)
        await get_def(engine, "func", context_id=42)
        engine.wait_for_freshness.assert_awaited_once()


class TestGetAllDefs:
    @pytest.mark.asyncio
    async def test_returns_list(self) -> None:
        defs = [MagicMock(spec=DefFact), MagicMock(spec=DefFact)]
        engine, _ = _mock_engine(defs)
        result = await get_all_defs(engine, "my_func")
        assert result == defs

    @pytest.mark.asyncio
    async def test_with_path_filter(self) -> None:
        engine, _ = _mock_engine([])
        result = await get_all_defs(engine, "func", path="src/foo.py")
        assert result == []


# ── get_references / get_all_references ───────────────────────────


class TestGetReferences:
    @pytest.mark.asyncio
    async def test_delegates_to_fact_queries(self) -> None:
        fake_def = MagicMock(spec=DefFact)
        fake_def.def_uid = "uid123"
        engine, session = _mock_engine()

        with patch("coderecon.index.ops_getters.FactQueries") as MockFQ:
            mock_fq = MockFQ.return_value
            mock_fq.list_refs_by_def_uid.return_value = [MagicMock(spec=RefFact)]
            result = await get_references(engine, fake_def, 1)
            assert len(result) == 1
            mock_fq.list_refs_by_def_uid.assert_called_once_with(
                "uid123", limit=10_000, offset=0
            )

    @pytest.mark.asyncio
    async def test_all_references(self) -> None:
        fake_def = MagicMock(spec=DefFact)
        fake_def.def_uid = "uid456"
        engine, _ = _mock_engine()

        with patch("coderecon.index.ops_getters.FactQueries") as MockFQ:
            mock_fq = MockFQ.return_value
            mock_fq.list_all_refs_by_def_uid.return_value = []
            result = await get_all_references(engine, fake_def, 1)
            assert result == []


# ── get_callees ───────────────────────────────────────────────────


class TestGetCallees:
    @pytest.mark.asyncio
    async def test_delegates_to_fact_queries(self) -> None:
        fake_def = MagicMock(spec=DefFact)
        fake_def.file_id = 10
        fake_def.start_line = 5
        fake_def.end_line = 20
        engine, _ = _mock_engine()

        with patch("coderecon.index.ops_getters.FactQueries") as MockFQ:
            mock_fq = MockFQ.return_value
            mock_fq.list_callees_in_scope.return_value = [MagicMock(spec=DefFact)]
            result = await get_callees(engine, fake_def, limit=10)
            assert len(result) == 1
            mock_fq.list_callees_in_scope.assert_called_once_with(10, 5, 20, limit=10)


# ── get_file_imports ──────────────────────────────────────────────


class TestGetFileImports:
    @pytest.mark.asyncio
    async def test_returns_imports(self) -> None:
        engine, _ = _mock_engine()
        with patch("coderecon.index.ops_getters.FactQueries") as MockFQ:
            mock_fq = MockFQ.return_value
            file_rec = MagicMock()
            file_rec.id = 7
            mock_fq.get_file_by_path.return_value = file_rec
            mock_fq.list_imports.return_value = [MagicMock(spec=ImportFact)]
            result = await get_file_imports(engine, "src/foo.py")
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_file(self) -> None:
        engine, _ = _mock_engine()
        with patch("coderecon.index.ops_getters.FactQueries") as MockFQ:
            mock_fq = MockFQ.return_value
            mock_fq.get_file_by_path.return_value = None
            result = await get_file_imports(engine, "nonexistent.py")
            assert result == []


# ── get_file_state ────────────────────────────────────────────────


class TestGetFileState:
    @pytest.mark.asyncio
    async def test_returns_state_from_engine(self) -> None:
        engine, _ = _mock_engine()
        fake_state = MagicMock()
        fake_state.get_file_state.return_value = MagicMock()
        engine._state = fake_state
        result = await get_file_state(engine, file_id=1, context_id=2)
        fake_state.get_file_state.assert_called_once_with(1, 2)
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_unindexed_when_no_state(self) -> None:
        engine, _ = _mock_engine()
        engine._state = None
        result = await get_file_state(engine, file_id=1, context_id=2)
        assert result is not None


# ── get_file_stats / get_indexed_file_count / get_indexed_files ──


class TestGetFileStats:
    @pytest.mark.asyncio
    async def test_returns_language_counts(self) -> None:
        engine, session = _mock_engine()
        session.exec.return_value.all.return_value = [("python", 50), ("javascript", 30)]
        result = await get_file_stats(engine)
        assert result == {"python": 50, "javascript": 30}


class TestGetIndexedFileCount:
    @pytest.mark.asyncio
    async def test_returns_count(self) -> None:
        engine, _ = _mock_engine(42)
        result = await get_indexed_file_count(engine)
        assert result == 42

    @pytest.mark.asyncio
    async def test_returns_zero_when_none(self) -> None:
        engine, session = _mock_engine()
        session.exec.return_value.one.return_value = None
        result = await get_indexed_file_count(engine)
        assert result == 0


class TestGetIndexedFiles:
    @pytest.mark.asyncio
    async def test_returns_paths(self) -> None:
        engine, session = _mock_engine()
        session.exec.return_value.all.return_value = ["src/a.py", "src/b.py"]
        result = await get_indexed_files(engine)
        assert result == ["src/a.py", "src/b.py"]


# ── get_contexts / get_test_targets / get_lint_tools ──────────────


class TestGetContexts:
    @pytest.mark.asyncio
    async def test_returns_valid_contexts(self) -> None:
        ctx = MagicMock(spec=Context)
        engine, session = _mock_engine()
        session.exec.return_value.all.return_value = [ctx]
        result = await get_contexts(engine)
        assert result == [ctx]


class TestGetTestTargets:
    @pytest.mark.asyncio
    async def test_returns_all_targets(self) -> None:
        tt = MagicMock(spec=TestTarget)
        engine, session = _mock_engine()
        session.exec.return_value.all.return_value = [tt]
        result = await get_test_targets(engine)
        assert result == [tt]


class TestGetLintTools:
    @pytest.mark.asyncio
    async def test_returns_filtered_tools(self) -> None:
        tool = MagicMock(spec=IndexedLintTool)
        engine, session = _mock_engine()
        session.exec.return_value.all.return_value = [tool]
        result = await get_lint_tools(engine, category="formatter")
        assert result == [tool]


# ── get_coverage_capability ───────────────────────────────────────


class TestGetCoverageCapability:
    @pytest.mark.asyncio
    async def test_returns_tools_dict(self) -> None:
        cap = MagicMock(spec=IndexedCoverageCapability)
        cap.get_tools.return_value = {"coverage_py": True}
        engine, session = _mock_engine()
        session.exec.return_value.first.return_value = cap
        result = await get_coverage_capability(engine, "/repo", "python.pytest")
        assert result == {"coverage_py": True}

    @pytest.mark.asyncio
    async def test_returns_empty_when_missing(self) -> None:
        engine, session = _mock_engine()
        session.exec.return_value.first.return_value = None
        result = await get_coverage_capability(engine, "/repo", "python.pytest")
        assert result == {}


# ── Epoch operations ──────────────────────────────────────────────


class TestEpochOps:
    def test_get_current_epoch_with_manager(self) -> None:
        engine = MagicMock()
        engine._epoch_manager.get_current_epoch.return_value = 5
        assert get_current_epoch(engine) == 5

    def test_get_current_epoch_no_manager(self) -> None:
        engine = MagicMock()
        engine._epoch_manager = None
        assert get_current_epoch(engine) == 0

    def test_publish_epoch(self) -> None:
        engine = MagicMock()
        engine._epoch_manager.publish_epoch.return_value = MagicMock(epoch_id=6)
        result = publish_epoch(engine, files_indexed=10, commit_hash="abc")
        engine._epoch_manager.publish_epoch.assert_called_once_with(10, "abc")
        assert result.epoch_id == 6

    def test_publish_epoch_no_manager_raises(self) -> None:
        engine = MagicMock()
        engine._epoch_manager = None
        with pytest.raises(RuntimeError, match="not initialized"):
            publish_epoch(engine)

    def test_await_epoch_with_manager(self) -> None:
        engine = MagicMock()
        engine._epoch_manager.await_epoch.return_value = True
        assert await_epoch(engine, 3) is True

    def test_await_epoch_no_manager(self) -> None:
        engine = MagicMock()
        engine._epoch_manager = None
        assert await_epoch(engine, 3) is False


# ── close ─────────────────────────────────────────────────────────


class TestClose:
    def test_close_resets_state(self) -> None:
        engine = MagicMock()
        engine._lexical = MagicMock()
        engine._def_cache = MagicMock()
        engine._initialized = True
        close(engine)
        assert engine._lexical is None
        assert engine._def_cache is None
        assert engine._initialized is False
        engine.db.engine.dispose.assert_called_once()
