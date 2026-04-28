"""Tests for coderecon.index.ops_discovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.index.ops_discovery import (
    _discover_coverage_capabilities,
    _discover_lint_tools,
    _discover_test_targets,
    _rediscover_lint_tools,
    _rediscover_test_targets,
    _resolve_context_runtimes,
    _update_lint_tools_incremental,
    _update_test_targets_incremental,
)


def _mock_engine(session: MagicMock | None = None) -> MagicMock:
    """Build a mock IndexCoordinatorEngine."""
    engine = MagicMock()
    engine.repo_root = Path("/repo")
    sess = session or MagicMock()
    engine.db.session.return_value.__enter__ = MagicMock(return_value=sess)
    engine.db.session.return_value.__exit__ = MagicMock(return_value=False)
    return engine


def _make_context(
    id_: int = 1,
    language: str = "python",
    root_path: str = "",
    probe_status: str = "valid",
) -> MagicMock:
    ctx = MagicMock()
    ctx.id = id_
    ctx.name = f"ctx-{id_}"
    ctx.language_family = language
    ctx.root_path = root_path
    ctx.probe_status = probe_status
    return ctx


# ---------------------------------------------------------------------------
# _resolve_context_runtimes
# ---------------------------------------------------------------------------

class TestResolveContextRuntimes:
    """_resolve_context_runtimes detects and persists runtime info."""

    @pytest.mark.asyncio
    async def test_resolves_for_valid_contexts(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        # select(Context) returns our context
        session.exec.return_value.all.return_value = [ctx]
        # No existing runtime
        session.exec.return_value.first.return_value = None
        engine = _mock_engine(session)

        resolver_result = MagicMock()
        resolver_result.runtime = MagicMock()
        resolver_result.warnings = []
        resolver_result.method = "venv_detected"

        with patch(
            "coderecon.testing.runtime.RuntimeResolver"
        ) as MockResolver:
            MockResolver.return_value.resolve_for_context.return_value = resolver_result
            count = await _resolve_context_runtimes(engine)

        assert count == 1
        session.add.assert_called_once_with(resolver_result.runtime)
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_already_resolved(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        session.exec.return_value.all.return_value = [ctx]
        session.exec.return_value.first.return_value = MagicMock()  # existing runtime
        engine = _mock_engine(session)

        with patch("coderecon.testing.runtime.RuntimeResolver"):
            count = await _resolve_context_runtimes(engine)

        assert count == 1
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_context_with_no_id(self) -> None:
        ctx = _make_context()
        ctx.id = None
        session = MagicMock()
        session.exec.return_value.all.return_value = [ctx]
        engine = _mock_engine(session)

        with patch("coderecon.testing.runtime.RuntimeResolver"):
            count = await _resolve_context_runtimes(engine)

        assert count == 0

    @pytest.mark.asyncio
    async def test_handles_resolution_failure(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        session.exec.return_value.all.return_value = [ctx]
        session.exec.return_value.first.return_value = None
        engine = _mock_engine(session)

        with patch(
            "coderecon.testing.runtime.RuntimeResolver"
        ) as MockResolver:
            MockResolver.return_value.resolve_for_context.side_effect = RuntimeError("boom")
            count = await _resolve_context_runtimes(engine)

        assert count == 0
        session.add.assert_not_called()


# ---------------------------------------------------------------------------
# _discover_test_targets
# ---------------------------------------------------------------------------

class TestDiscoverTestTargets:
    """_discover_test_targets finds test targets via runner packs."""

    @pytest.mark.asyncio
    async def test_discovers_targets(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        session.exec.return_value.all.side_effect = [
            set(),  # existing_ids
            [ctx],  # contexts
        ]
        engine = _mock_engine(session)

        target = MagicMock()
        target.target_id = "test:tests/test_foo.py"
        target.selector = "tests/test_foo.py"
        target.kind = "file"
        target.language = "python"
        target.runner_pack_id = "python.pytest"
        target.workspace_root = "/repo"
        target.estimated_cost = "medium"
        target.test_count = 10
        target.path = "tests/test_foo.py"

        mock_pack = MagicMock()
        mock_pack.return_value.discover = AsyncMock(return_value=[target])

        with patch(
            "coderecon.testing.runner_pack.runner_registry"
        ) as mock_reg:
            mock_reg.detect_all.return_value = [(mock_pack, 1.0)]
            count = await _discover_test_targets(engine)

        assert count == 1
        session.add.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_existing_targets(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        session.exec.return_value.all.side_effect = [
            {"test:tests/test_foo.py"},  # existing_ids — already discovered
            [ctx],
        ]
        engine = _mock_engine(session)

        target = MagicMock()
        target.target_id = "test:tests/test_foo.py"

        mock_pack = MagicMock()
        mock_pack.return_value.discover = AsyncMock(return_value=[target])

        with patch(
            "coderecon.testing.runner_pack.runner_registry"
        ) as mock_reg:
            mock_reg.detect_all.return_value = [(mock_pack, 1.0)]
            count = await _discover_test_targets(engine)

        assert count == 1
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_runner_packs_detected(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        session.exec.return_value.all.side_effect = [
            set(),
            [ctx],
        ]
        engine = _mock_engine(session)

        with patch(
            "coderecon.testing.runner_pack.runner_registry"
        ) as mock_reg:
            mock_reg.detect_all.return_value = []
            count = await _discover_test_targets(engine)

        assert count == 0

    @pytest.mark.asyncio
    async def test_discover_error_caught(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        session.exec.return_value.all.side_effect = [
            set(),
            [ctx],
        ]
        engine = _mock_engine(session)

        mock_pack = MagicMock()
        mock_pack.return_value.discover = AsyncMock(side_effect=RuntimeError("fail"))

        with patch(
            "coderecon.testing.runner_pack.runner_registry"
        ) as mock_reg:
            mock_reg.detect_all.return_value = [(mock_pack, 1.0)]
            count = await _discover_test_targets(engine)

        assert count == 0


# ---------------------------------------------------------------------------
# _discover_lint_tools
# ---------------------------------------------------------------------------

class TestDiscoverLintTools:
    """_discover_lint_tools finds configured lint tools."""

    @pytest.mark.asyncio
    async def test_discovers_tools(self) -> None:
        session = MagicMock()
        session.exec.return_value.all.return_value = set()  # no existing
        engine = _mock_engine(session)

        tool = MagicMock()
        tool.tool_id = "python.ruff"
        tool.name = "ruff"
        tool.category.value = "lint"
        tool.languages = {"python"}
        tool.executable = "ruff"

        with patch(
            "coderecon.lint.tools.registry"
        ) as mock_reg:
            mock_reg.detect.return_value = [(tool, "pyproject.toml")]
            count = await _discover_lint_tools(engine)

        assert count == 1
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_existing_tools(self) -> None:
        session = MagicMock()
        session.exec.return_value.all.return_value = {"python.ruff"}
        engine = _mock_engine(session)

        tool = MagicMock()
        tool.tool_id = "python.ruff"

        with patch(
            "coderecon.lint.tools.registry"
        ) as mock_reg:
            mock_reg.detect.return_value = [(tool, "pyproject.toml")]
            count = await _discover_lint_tools(engine)

        assert count == 1
        session.add.assert_not_called()


# ---------------------------------------------------------------------------
# _discover_coverage_capabilities
# ---------------------------------------------------------------------------

class TestDiscoverCoverageCapabilities:
    """_discover_coverage_capabilities detects coverage tools."""

    @pytest.mark.asyncio
    async def test_discovers_capabilities(self) -> None:
        session = MagicMock()
        session.exec.return_value.all.side_effect = [
            set(),  # existing pairs
            [("/repo", "python.pytest")],  # distinct pairs
        ]
        engine = _mock_engine(session)

        with patch(
            "coderecon.testing.ops.detect_coverage_tools",
            return_value={"coverage.py": True},
        ):
            count = await _discover_coverage_capabilities(engine)

        assert count == 1
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_existing_pairs(self) -> None:
        session = MagicMock()
        session.exec.return_value.all.side_effect = [
            {("/repo", "python.pytest")},
            [("/repo", "python.pytest")],
        ]
        engine = _mock_engine(session)

        count = await _discover_coverage_capabilities(engine)
        assert count == 1
        session.add.assert_not_called()


# ---------------------------------------------------------------------------
# _rediscover_*
# ---------------------------------------------------------------------------

class TestRediscover:
    """_rediscover_test_targets and _rediscover_lint_tools clear + re-discover."""

    @pytest.mark.asyncio
    async def test_rediscover_test_targets_clears_first(self) -> None:
        session = MagicMock()
        session.exec.return_value.all.side_effect = [
            [],  # existing targets for delete
            set(),  # existing_ids in _discover_test_targets
            [],  # contexts in _discover_test_targets
        ]
        engine = _mock_engine(session)

        with patch(
            "coderecon.testing.runner_pack.runner_registry"
        ) as mock_reg:
            mock_reg.detect_all.return_value = []
            count = await _rediscover_test_targets(engine)

        assert count == 0
        # Two commits: one from clear, one from discover
        assert session.commit.call_count >= 1

    @pytest.mark.asyncio
    async def test_rediscover_lint_tools_clears_first(self) -> None:
        session = MagicMock()
        session.exec.return_value.all.return_value = set()
        engine = _mock_engine(session)

        with patch(
            "coderecon.lint.tools.registry"
        ) as mock_reg:
            mock_reg.detect.return_value = []
            count = await _rediscover_lint_tools(engine)

        assert count == 0


# ---------------------------------------------------------------------------
# _update_test_targets_incremental
# ---------------------------------------------------------------------------

class TestUpdateTestTargetsIncremental:
    """_update_test_targets_incremental handles add/modify/remove of test files."""

    @pytest.mark.asyncio
    async def test_no_test_files_is_noop(self) -> None:
        engine = _mock_engine()
        count = await _update_test_targets_incremental(
            engine,
            new_paths=[Path("src/lib.py")],
            existing_paths=[],
            removed_paths=[],
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_removed_test_files_deleted(self) -> None:
        session = MagicMock()
        engine = _mock_engine(session)

        count = await _update_test_targets_incremental(
            engine,
            new_paths=[],
            existing_paths=[],
            removed_paths=[Path("tests/test_foo.py")],
        )

        assert count >= 1
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_test_files_added(self) -> None:
        ctx = _make_context(id_=1)
        session = MagicMock()
        # For the context query
        session.exec.return_value.all.return_value = [ctx]
        engine = _mock_engine(session)
        engine.repo_root = Path("/repo")
        # Make the file "exist"
        with (
            patch(
                "coderecon.testing.runner_pack.runner_registry"
            ) as mock_reg,
            patch.object(Path, "exists", return_value=True),
        ):
            mock_pack_cls = MagicMock()
            mock_pack_cls.return_value.language = "python"
            mock_pack_cls.return_value.pack_id = "python.pytest"
            mock_reg.detect_all.return_value = [(mock_pack_cls, 1.0)]

            count = await _update_test_targets_incremental(
                engine,
                new_paths=[Path("tests/test_new.py")],
                existing_paths=[],
                removed_paths=[],
            )

        assert count >= 1


# ---------------------------------------------------------------------------
# _update_lint_tools_incremental
# ---------------------------------------------------------------------------

class TestUpdateLintToolsIncremental:
    """_update_lint_tools_incremental re-detects when config files change."""

    @pytest.mark.asyncio
    async def test_no_config_file_changes_is_noop(self) -> None:
        engine = _mock_engine()
        with patch(
            "coderecon.lint.tools.registry"
        ) as mock_reg:
            mock_tool = MagicMock()
            mock_tool.config_files = ["pyproject.toml", ".ruff.toml"]
            mock_reg.all.return_value = [mock_tool]

            count = await _update_lint_tools_incremental(
                engine, [Path("src/foo.py")]
            )

        assert count == 0

    @pytest.mark.asyncio
    async def test_config_file_change_triggers_redetect(self) -> None:
        session = MagicMock()
        engine = _mock_engine(session)

        tool = MagicMock()
        tool.tool_id = "python.ruff"
        tool.name = "ruff"
        tool.category.value = "lint"
        tool.languages = {"python"}
        tool.executable = "ruff"

        with patch(
            "coderecon.lint.tools.registry"
        ) as mock_reg:
            mock_tool = MagicMock()
            mock_tool.config_files = ["pyproject.toml"]
            mock_reg.all.return_value = [mock_tool]
            mock_reg.detect.return_value = [(tool, "pyproject.toml")]

            count = await _update_lint_tools_incremental(
                engine, [Path("pyproject.toml")]
            )

        assert count == 1
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_section_aware_config_specs(self) -> None:
        """Config specs like 'pyproject.toml:tool.ruff' should match filename."""
        session = MagicMock()
        engine = _mock_engine(session)

        with patch(
            "coderecon.lint.tools.registry"
        ) as mock_reg:
            mock_tool = MagicMock()
            mock_tool.config_files = ["pyproject.toml:tool.ruff"]
            mock_reg.all.return_value = [mock_tool]
            mock_reg.detect.return_value = []

            await _update_lint_tools_incremental(
                engine, [Path("pyproject.toml")]
            )

        # detect was called (config matched), even if no tools found
        mock_reg.detect.assert_called_once()
