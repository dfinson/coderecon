"""Tests for checkpoint_pipeline — core checkpoint orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.mcp.tools.checkpoint_pipeline import (
    _readonly_checkpoint,
    _run_lint_phase,
    _run_test_phase,
)


def _make_app_ctx(repo_root: Path | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.repo_root = repo_root or Path("/repo")
    ctx.git_ops.path = str(ctx.repo_root)
    ctx.git_ops.status.return_value = {}
    ctx.git_ops.tracked_files.return_value = set()
    ctx.coordinator.db.engine = MagicMock()
    ctx.coordinator.get_current_epoch.return_value = 1
    ctx.refactor_ops.clear_pending = MagicMock()
    return ctx


def _make_session(*, read_only: bool = False) -> MagicMock:
    session = MagicMock()
    session.read_only = read_only
    session.mutation_ctx.clear = MagicMock()
    return session


def _make_progress() -> AsyncMock:
    progress = AsyncMock()
    progress.report_progress = AsyncMock()
    progress.info = AsyncMock()
    progress.warning = AsyncMock()
    return progress


# ── _readonly_checkpoint ──────────────────────────────────────────


class TestReadonlyCheckpoint:
    @patch("coderecon.mcp.delivery.wrap_response", side_effect=lambda r, **kw: r)
    def test_clean_tree(self, _mock_wrap: MagicMock) -> None:
        app_ctx = _make_app_ctx()
        app_ctx.git_ops.status.return_value = {}
        session = _make_session(read_only=True)

        result = _readonly_checkpoint(app_ctx, session)

        assert result["read_only"] is True
        assert result["clean_tree"] is True
        assert result["passed"] is True
        session.mutation_ctx.clear.assert_called_once()

    @patch("coderecon.mcp.delivery.wrap_response", side_effect=lambda r, **kw: r)
    def test_dirty_tree(self, _mock_wrap: MagicMock) -> None:
        app_ctx = _make_app_ctx()
        app_ctx.git_ops.status.return_value = {"src/foo.py": 1, "src/bar.py": 2}
        session = _make_session(read_only=True)

        result = _readonly_checkpoint(app_ctx, session)

        assert result["clean_tree"] is False
        assert result["passed"] is False
        assert len(result["dirty_files"]) == 2

    @patch("coderecon.mcp.delivery.wrap_response", side_effect=lambda r, **kw: r)
    def test_git_error_treated_as_clean(self, _mock_wrap: MagicMock) -> None:
        from coderecon.git.errors import GitError

        app_ctx = _make_app_ctx()
        app_ctx.git_ops.status.side_effect = GitError("failed")
        session = _make_session(read_only=True)

        result = _readonly_checkpoint(app_ctx, session)

        assert result["clean_tree"] is True
        assert result["passed"] is True


# ── _run_lint_phase ───────────────────────────────────────────────


class TestRunLintPhase:
    @pytest.mark.anyio
    async def test_lint_clean(self) -> None:
        app_ctx = _make_app_ctx()
        lint_result = MagicMock()
        lint_result.status = "clean"
        lint_result.total_diagnostics = 0
        lint_result.total_files_modified = 0
        lint_result.tools_run = []
        lint_result.agentic_hint = None
        app_ctx.lint_ops.check = AsyncMock(return_value=lint_result)
        progress = _make_progress()
        result: dict = {}

        out = await _run_lint_phase(
            app_ctx, ["src/foo.py"], autofix=True,
            phase=0, total_phases=4, progress=progress, result=result,
        )

        assert out["lint_status"] == "clean"
        assert out["lint_diagnostics"] == 0
        assert out["skip_tests"] is False
        assert result["lint"]["status"] == "clean"

    @pytest.mark.anyio
    async def test_lint_dirty_skips_tests(self) -> None:
        app_ctx = _make_app_ctx()
        lint_result = MagicMock()
        lint_result.status = "dirty"
        lint_result.total_diagnostics = 3
        lint_result.total_files_modified = 0
        lint_result.tools_run = []
        lint_result.agentic_hint = None
        app_ctx.lint_ops.check = AsyncMock(return_value=lint_result)
        progress = _make_progress()
        result: dict = {}

        out = await _run_lint_phase(
            app_ctx, ["src/foo.py"], autofix=False,
            phase=0, total_phases=4, progress=progress, result=result,
        )

        assert out["lint_status"] == "dirty"
        assert out["lint_diagnostics"] == 3
        assert out["skip_tests"] is True
        assert result["tests"]["status"] == "skipped"

    @pytest.mark.anyio
    async def test_cached_lint_clean(self) -> None:
        app_ctx = _make_app_ctx()
        progress = _make_progress()
        result: dict = {}

        cached = MagicMock()
        cached.clean = True
        cached.total_errors = 0
        cached.total_warnings = 0
        cached.issues = []

        with patch(
            "coderecon.mcp.tools._checkpoint_cache.try_read_lint_facts",
            return_value=cached,
        ):
            out = await _run_lint_phase(
                app_ctx, ["src/foo.py"], autofix=False,
                phase=0, total_phases=4, progress=progress, result=result,
            )

        assert out["lint_status"] == "clean"
        assert result["lint"]["cached"] is True

    @pytest.mark.anyio
    async def test_cached_lint_dirty(self) -> None:
        app_ctx = _make_app_ctx()
        progress = _make_progress()
        result: dict = {}

        cached = MagicMock()
        cached.clean = False
        cached.total_errors = 2
        cached.total_warnings = 1
        cached.issues = [{"file": "x.py", "tool": "ruff", "errors": 2, "warnings": 1}]

        with patch(
            "coderecon.mcp.tools._checkpoint_cache.try_read_lint_facts",
            return_value=cached,
        ):
            out = await _run_lint_phase(
                app_ctx, ["src/foo.py"], autofix=False,
                phase=0, total_phases=4, progress=progress, result=result,
            )

        assert out["lint_status"] == "dirty"
        assert out["lint_diagnostics"] == 3
        assert out["skip_tests"] is True

    @pytest.mark.anyio
    async def test_lint_with_issues_populates_issue_lines(self) -> None:
        app_ctx = _make_app_ctx()
        diag = MagicMock()
        diag.path = "src/foo.py"
        diag.line = 10
        diag.column = 5
        diag.severity.value = "error"
        diag.code = "E001"
        diag.message = "bad code"
        tool_run = MagicMock()
        tool_run.diagnostics = [diag]
        lint_result = MagicMock()
        lint_result.status = "dirty"
        lint_result.total_diagnostics = 1
        lint_result.total_files_modified = 0
        lint_result.tools_run = [tool_run]
        lint_result.agentic_hint = "fix it"
        app_ctx.lint_ops.check = AsyncMock(return_value=lint_result)
        progress = _make_progress()
        result: dict = {}

        await _run_lint_phase(
            app_ctx, ["src/foo.py"], autofix=True,
            phase=0, total_phases=4, progress=progress, result=result,
        )

        assert "issues" in result["lint"]
        assert any("E001" in i for i in result["lint"]["issues"])


# ── _run_test_phase ───────────────────────────────────────────────


class TestRunTestPhase:
    @pytest.mark.anyio
    async def test_no_targets_discovered(self) -> None:
        app_ctx = _make_app_ctx()
        discover_result = MagicMock()
        discover_result.targets = []
        app_ctx.test_ops.discover = AsyncMock(return_value=discover_result)
        progress = _make_progress()
        result: dict = {}

        out = await _run_test_phase(
            app_ctx, ["src/foo.py"], None, None, None,
            phase=0, total_phases=4, progress=progress, result=result,
        )

        assert out["test_status"] == "skipped"
        assert result["tests"]["reason"] == "no test targets discovered"

    @pytest.mark.anyio
    async def test_empty_changed_files(self) -> None:
        app_ctx = _make_app_ctx()
        discover_result = MagicMock()
        discover_result.targets = [MagicMock()]
        app_ctx.test_ops.discover = AsyncMock(return_value=discover_result)
        progress = _make_progress()
        result: dict = {}

        out = await _run_test_phase(
            app_ctx, [], None, None, None,
            phase=0, total_phases=4, progress=progress, result=result,
        )

        assert out["test_status"] == "skipped"

    @pytest.mark.anyio
    async def test_no_affected_targets_after_filtering(self) -> None:
        app_ctx = _make_app_ctx()
        discover_result = MagicMock()
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "tests/unrelated.py"
        discover_result.targets = [target]
        app_ctx.test_ops.discover = AsyncMock(return_value=discover_result)

        graph_result = MagicMock()
        graph_result.test_files = ["tests/other_test.py"]
        graph_result.confidence.tier = "high"
        app_ctx.coordinator.get_affected_test_targets = AsyncMock(return_value=graph_result)

        progress = _make_progress()
        result: dict = {}

        with patch(
            "coderecon.mcp.tools.checkpoint_pipeline._target_matches_affected_files",
            return_value=False,
        ):
            out = await _run_test_phase(
                app_ctx, ["src/foo.py"], None, None, None,
                phase=0, total_phases=4, progress=progress, result=result,
            )

        assert out["test_status"] == "skipped"
        assert result["tests"]["status"] == "skipped"

    @pytest.mark.anyio
    async def test_affected_targets_run_tiered(self, tmp_path: Path) -> None:
        app_ctx = _make_app_ctx(repo_root=tmp_path)
        discover_result = MagicMock()
        target = MagicMock()
        target.workspace_root = str(tmp_path)
        target.selector = "tests/test_foo.py"
        discover_result.targets = [target]
        app_ctx.test_ops.discover = AsyncMock(return_value=discover_result)

        graph_result = MagicMock()
        graph_result.test_files = ["tests/test_foo.py"]
        graph_result.confidence.tier = "high"
        app_ctx.coordinator.get_affected_test_targets = AsyncMock(return_value=graph_result)

        progress = _make_progress()
        result: dict = {}

        tiered_result = {
            "serialized": {"status": "passed", "coverage_hint": None},
            "status": "passed",
            "passed": 5,
            "failed": 0,
            "failed_test_ids": [],
        }

        with (
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._target_matches_affected_files",
                return_value=True,
            ),
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._run_tiered_tests",
                new_callable=AsyncMock,
                return_value=tiered_result,
            ),
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._ingest_checkpoint_coverage",
            ),
        ):
            out = await _run_test_phase(
                app_ctx, ["src/foo.py"], None, None, None,
                phase=0, total_phases=4, progress=progress, result=result,
            )

        assert out["test_status"] == "passed"
        assert out["test_passed"] == 5
        assert out["test_failed"] == 0

    @pytest.mark.anyio
    async def test_max_test_hops_from_commit_message(self, tmp_path: Path) -> None:
        """When commit_message is provided, effective_hops = _COMMIT_MAX_TEST_HOPS."""
        app_ctx = _make_app_ctx(repo_root=tmp_path)
        discover_result = MagicMock()
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "tests/test_foo.py"
        discover_result.targets = [target]
        app_ctx.test_ops.discover = AsyncMock(return_value=discover_result)

        graph_result = MagicMock()
        graph_result.test_files = ["tests/test_foo.py"]
        graph_result.confidence.tier = "high"
        app_ctx.coordinator.get_affected_test_targets = AsyncMock(return_value=graph_result)

        progress = _make_progress()
        result: dict = {}

        with (
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._target_matches_affected_files",
                return_value=True,
            ),
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._run_tiered_tests",
                new_callable=AsyncMock,
                return_value={"serialized": {}, "status": "passed", "passed": 1, "failed": 0, "failed_test_ids": []},
            ) as mock_tiered,
            patch(
                "coderecon.mcp.tools.checkpoint_helpers._ingest_checkpoint_coverage",
            ),
        ):
            await _run_test_phase(
                app_ctx, ["src/foo.py"], None, None, "feat: add feature",
                phase=0, total_phases=4, progress=progress, result=result,
            )

        # _COMMIT_MAX_TEST_HOPS = 2
        call_kwargs = mock_tiered.call_args[1]
        assert call_kwargs["max_test_hops"] == 2

    @pytest.mark.anyio
    async def test_max_test_hops_explicit_override(self, tmp_path: Path) -> None:
        """Explicit max_test_hops overrides both default and commit value."""
        app_ctx = _make_app_ctx(repo_root=tmp_path)
        discover_result = MagicMock()
        target = MagicMock()
        target.workspace_root = str(tmp_path)
        target.selector = "tests/test_foo.py"
        discover_result.targets = [target]
        app_ctx.test_ops.discover = AsyncMock(return_value=discover_result)

        graph_result = MagicMock()
        graph_result.test_files = ["tests/test_foo.py"]
        graph_result.confidence.tier = "high"
        app_ctx.coordinator.get_affected_test_targets = AsyncMock(return_value=graph_result)

        progress = _make_progress()
        result: dict = {}

        with (
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._target_matches_affected_files",
                return_value=True,
            ),
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._run_tiered_tests",
                new_callable=AsyncMock,
                return_value={"serialized": {}, "status": "passed", "passed": 1, "failed": 0, "failed_test_ids": []},
            ) as mock_tiered,
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._ingest_checkpoint_coverage",
            ),
        ):
            await _run_test_phase(
                app_ctx, ["src/foo.py"], None, 5, "feat: something",
                phase=0, total_phases=4, progress=progress, result=result,
            )

        call_kwargs = mock_tiered.call_args[1]
        assert call_kwargs["max_test_hops"] == 5

    @pytest.mark.anyio
    async def test_coverage_hint_extracted(self, tmp_path: Path) -> None:
        app_ctx = _make_app_ctx(repo_root=tmp_path)
        discover_result = MagicMock()
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "tests/test_foo.py"
        discover_result.targets = [target]
        app_ctx.test_ops.discover = AsyncMock(return_value=discover_result)

        graph_result = MagicMock()
        graph_result.test_files = ["tests/test_foo.py"]
        graph_result.confidence.tier = "high"
        app_ctx.coordinator.get_affected_test_targets = AsyncMock(return_value=graph_result)

        progress = _make_progress()
        result: dict = {}

        tiered_result = {
            "serialized": {"status": "passed", "coverage_hint": "low coverage on foo.py"},
            "status": "passed",
            "passed": 3,
            "failed": 0,
            "failed_test_ids": [],
        }

        with (
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._target_matches_affected_files",
                return_value=True,
            ),
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._run_tiered_tests",
                new_callable=AsyncMock,
                return_value=tiered_result,
            ),
            patch(
                "coderecon.mcp.tools.checkpoint_pipeline._ingest_checkpoint_coverage",
            ),
        ):
            await _run_test_phase(
                app_ctx, ["src/foo.py"], None, None, None,
                phase=0, total_phases=4, progress=progress, result=result,
            )

        assert result["coverage_hint"] == "low coverage on foo.py"
        # coverage_hint should be popped from serialized
        assert "coverage_hint" not in result["tests"]
