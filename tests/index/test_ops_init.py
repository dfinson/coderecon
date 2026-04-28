"""Tests for index/ops_init.py module.

Covers:
- initialize() orchestration: step sequencing and result assembly
- collect_initial_coverage() best-effort error handling
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.index.models import ProbeStatus
from coderecon.index.ops_init import collect_initial_coverage, initialize
from coderecon.index.ops_types import InitResult


def _make_engine(repo_root: Path | None = None) -> MagicMock:
    """Build a mock IndexCoordinatorEngine with required attributes."""
    engine = MagicMock()
    engine.repo_root = repo_root or Path("/fake/repo")
    engine.tantivy_path = Path("/fake/tantivy")
    engine.db = MagicMock()
    engine._parser = None
    engine._lexical = None
    engine._epoch_manager = None
    engine._router = None
    engine._structural = None
    engine._state = None
    engine._reconciler = None
    engine._facts = None
    engine._initialized = False
    engine.current_epoch = 1

    # Async methods
    engine._resolve_context_runtimes = AsyncMock()
    engine._discover_test_targets = AsyncMock()
    engine._discover_lint_tools = AsyncMock()
    engine._discover_coverage_capabilities = AsyncMock()
    engine._index_all_files = AsyncMock(return_value=(0, [], {}))
    engine._get_or_create_worktree_id = MagicMock(return_value=1)

    # DB session context manager
    session_mock = MagicMock()
    session_mock.__enter__ = MagicMock(return_value=session_mock)
    session_mock.__exit__ = MagicMock(return_value=False)
    engine.db.session.return_value = session_mock

    return engine


class TestInitialize:
    """initialize() orchestrates discovery → probe → index pipeline."""

    @pytest.mark.asyncio
    async def test_returns_init_result(self) -> None:
        engine = _make_engine()
        progress_cb = MagicMock()

        with (
            patch("coderecon.index.ops_init.ContextDiscovery") as mock_discovery_cls,
            patch("coderecon.index.ops_init.Tier1AuthorityFilter") as mock_auth_cls,
            patch("coderecon.index.ops_init.MembershipResolver") as mock_membership_cls,
            patch("coderecon.index.ops_init.ContextProbe") as mock_probe_cls,
            patch("coderecon.index.ops_init.ContextRouter"),
            patch("coderecon.index.ops_init.StructuralIndexer"),
            patch("coderecon.index.ops_init.FileStateService"),
            patch("coderecon.index.ops_init.Reconciler") as mock_reconciler_cls,
            patch("coderecon.index.ops_init.LexicalIndex") as mock_lexical_cls,
            patch("coderecon.index.ops_init.EpochManager"),
            patch("coderecon.index.ops_init.create_additional_indexes"),
            patch("coderecon.index.ops_init.tree_sitter_service"),
        ):
            # Discovery returns no candidates
            disc_result = MagicMock()
            disc_result.candidates = []
            mock_discovery_cls.return_value.discover_all.return_value = disc_result

            # Authority filter returns no pending/detached
            auth_result = MagicMock()
            auth_result.pending = []
            auth_result.detached = []
            mock_auth_cls.return_value.apply.return_value = auth_result

            # Membership returns empty
            membership_result = MagicMock()
            membership_result.contexts = []
            mock_membership_cls.return_value.resolve.return_value = membership_result

            # Reconciler
            mock_reconciler_cls.return_value.reconcile.return_value = MagicMock()

            # Lexical
            mock_lexical_cls.return_value.reload = MagicMock()

            result = await initialize(engine, progress_cb)

        assert isinstance(result, InitResult)
        assert result.contexts_discovered == 0
        assert result.files_indexed == 0
        assert engine._initialized is True

    @pytest.mark.asyncio
    async def test_counts_valid_and_failed_contexts(self) -> None:
        engine = _make_engine()
        progress_cb = MagicMock()

        with (
            patch("coderecon.index.ops_init.ContextDiscovery") as mock_disc_cls,
            patch("coderecon.index.ops_init.Tier1AuthorityFilter") as mock_auth_cls,
            patch("coderecon.index.ops_init.MembershipResolver") as mock_mem_cls,
            patch("coderecon.index.ops_init.ContextProbe") as mock_probe_cls,
            patch("coderecon.index.ops_init.ContextRouter"),
            patch("coderecon.index.ops_init.StructuralIndexer"),
            patch("coderecon.index.ops_init.FileStateService"),
            patch("coderecon.index.ops_init.Reconciler") as mock_rec_cls,
            patch("coderecon.index.ops_init.LexicalIndex") as mock_lex_cls,
            patch("coderecon.index.ops_init.EpochManager"),
            patch("coderecon.index.ops_init.create_additional_indexes"),
            patch("coderecon.index.ops_init.tree_sitter_service"),
        ):
            # One valid candidate, one failed
            valid_cand = MagicMock()
            valid_cand.is_root_fallback = False
            valid_cand.probe_status = ProbeStatus.VALID
            valid_cand.root_path = "src"
            valid_cand.language_family.value = "python"
            valid_cand.tier = 1
            valid_cand.include_spec = None
            valid_cand.exclude_spec = None
            valid_cand.markers = []

            failed_cand = MagicMock()
            failed_cand.is_root_fallback = False
            failed_cand.probe_status = ProbeStatus.FAILED
            failed_cand.root_path = "tests"
            failed_cand.language_family.value = "python"
            failed_cand.tier = 1
            failed_cand.include_spec = None
            failed_cand.exclude_spec = None
            failed_cand.markers = []

            disc_result = MagicMock()
            disc_result.candidates = [valid_cand, failed_cand]
            mock_disc_cls.return_value.discover_all.return_value = disc_result

            auth_result = MagicMock()
            auth_result.pending = [valid_cand, failed_cand]
            auth_result.detached = []
            mock_auth_cls.return_value.apply.return_value = auth_result

            mem_result = MagicMock()
            mem_result.contexts = [valid_cand, failed_cand]
            mock_mem_cls.return_value.resolve.return_value = mem_result

            # Probe: first valid, second failed
            probe_results = iter([
                MagicMock(valid=True, reason=None),
                MagicMock(valid=False, reason="parse error"),
            ])
            mock_probe_cls.return_value.validate.side_effect = lambda c: next(probe_results)

            mock_rec_cls.return_value.reconcile.return_value = MagicMock()
            mock_lex_cls.return_value.reload = MagicMock()

            result = await initialize(engine, progress_cb)

        assert result.contexts_valid == 1
        assert result.contexts_failed == 1
        assert result.contexts_discovered == 2


class TestCollectInitialCoverage:
    """collect_initial_coverage is best-effort, never raises."""

    @pytest.mark.asyncio
    async def test_returns_zero_on_no_coverage(self) -> None:
        engine = _make_engine()

        with patch("coderecon.testing.ops.TestOps") as mock_ops_cls:
            mock_result = MagicMock()
            mock_result.run_status = None
            mock_ops_cls.return_value.run = AsyncMock(return_value=mock_result)

            result = await collect_initial_coverage(engine)

        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_exception(self) -> None:
        engine = _make_engine()

        with patch(
            "coderecon.testing.ops.TestOps",
            side_effect=RuntimeError("boom"),
        ):
            result = await collect_initial_coverage(engine)

        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_empty_reports(self) -> None:
        engine = _make_engine()

        with patch("coderecon.testing.ops.TestOps") as mock_ops_cls:
            run_status = MagicMock()
            run_status.coverage = [{"path": "", "format": "unknown"}]
            run_status.failures = []
            mock_result = MagicMock()
            mock_result.run_status = run_status
            mock_ops_cls.return_value.run = AsyncMock(return_value=mock_result)

            result = await collect_initial_coverage(engine)

        assert result == 0
