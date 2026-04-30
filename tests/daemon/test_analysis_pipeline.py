"""Tests for daemon.analysis_pipeline — background lint analysis."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

@dataclass
class _FakeDiag:
    path: str
    severity: MagicMock

@dataclass
class _FakeToolResult:
    tool_id: str
    diagnostics: list[_FakeDiag]

@dataclass
class _FakeLintResult:
    tools_run: list[_FakeToolResult]
    total_diagnostics: int

def _severity(val: int) -> MagicMock:
    s = MagicMock()
    s.value = val
    return s

def _make_pipeline(*, lint_ops: MagicMock | None = None, test_ops: MagicMock | None = None):  # noqa: ANN202
    from coderecon.daemon.analysis_pipeline import AnalysisPipeline

    coordinator = MagicMock()
    coordinator.db.engine = MagicMock()
    coordinator.current_epoch = 1

    if lint_ops is None:
        lint_ops = MagicMock()
        lint_ops.check = AsyncMock(
            return_value=_FakeLintResult(tools_run=[], total_diagnostics=0)
        )
    if test_ops is None:
        test_ops = MagicMock()

    return AnalysisPipeline(
        coordinator=coordinator,
        lint_ops=lint_ops,
        test_ops=test_ops,
        repo_root=Path("/repo"),
    )

class TestTier1:
    """Tier 1: lint analysis."""

    @pytest.mark.asyncio
    async def test_tier1_persists_lint_facts(self) -> None:
        diag = _FakeDiag(path="src/a.py", severity=_severity(2))
        tool = _FakeToolResult(tool_id="ruff", diagnostics=[diag])
        lint_ops = MagicMock()
        lint_ops.check = AsyncMock(
            return_value=_FakeLintResult(tools_run=[tool], total_diagnostics=1)
        )
        pipeline = _make_pipeline(lint_ops=lint_ops)

        with patch(
            "coderecon.index.analysis.lint_status.persist_lint_status"
        ) as mock_persist:
            await pipeline._run_tier1(["src/a.py"])

        assert mock_persist.call_count >= 1
        # The diag file should have error_count=1
        calls = mock_persist.call_args_list
        diag_call = [c for c in calls if c.kwargs.get("file_path") == "src/a.py"][0]
        assert diag_call.kwargs["error_count"] == 1

    @pytest.mark.asyncio
    async def test_tier1_marks_clean_files(self) -> None:
        # Lint returns diagnostics only for b.py, a.py should be marked clean
        diag = _FakeDiag(path="src/b.py", severity=_severity(2))
        tool = _FakeToolResult(tool_id="ruff", diagnostics=[diag])
        lint_ops = MagicMock()
        lint_ops.check = AsyncMock(
            return_value=_FakeLintResult(tools_run=[tool], total_diagnostics=1)
        )
        pipeline = _make_pipeline(lint_ops=lint_ops)

        with patch(
            "coderecon.index.analysis.lint_status.persist_lint_status"
        ) as mock_persist:
            await pipeline._run_tier1(["src/a.py", "src/b.py"])

        clean_calls = [
            c for c in mock_persist.call_args_list
            if c.kwargs.get("file_path") == "src/a.py"
        ]
        assert len(clean_calls) == 1
        assert clean_calls[0].kwargs["error_count"] == 0
        assert clean_calls[0].kwargs["warning_count"] == 0

    @pytest.mark.asyncio
    async def test_tier1_handles_lint_error_gracefully(self) -> None:
        lint_ops = MagicMock()
        lint_ops.check = AsyncMock(side_effect=RuntimeError("lint broke"))
        pipeline = _make_pipeline(lint_ops=lint_ops)
        # Should not raise
        await pipeline._run_tier1(["src/a.py"])

    @pytest.mark.asyncio
    async def test_tier1_warning_severity(self) -> None:
        diag = _FakeDiag(path="src/a.py", severity=_severity(1))
        tool = _FakeToolResult(tool_id="ruff", diagnostics=[diag])
        lint_ops = MagicMock()
        lint_ops.check = AsyncMock(
            return_value=_FakeLintResult(tools_run=[tool], total_diagnostics=1)
        )
        pipeline = _make_pipeline(lint_ops=lint_ops)

        with patch(
            "coderecon.index.analysis.lint_status.persist_lint_status"
        ) as mock_persist:
            await pipeline._run_tier1(["src/a.py"])

        diag_call = [
            c for c in mock_persist.call_args_list
            if c.kwargs.get("file_path") == "src/a.py"
        ][0]
        assert diag_call.kwargs["warning_count"] == 1
        assert diag_call.kwargs["error_count"] == 0

class TestOnIndexComplete:
    """Integration: on_index_complete triggers lint."""

    @pytest.mark.asyncio
    async def test_skips_empty_paths(self) -> None:
        pipeline = _make_pipeline()
        await pipeline.on_index_complete(stats=MagicMock(), paths=[])
        pipeline.lint_ops.check.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_not_running(self) -> None:
        pipeline = _make_pipeline()
        pipeline._running = False
        await pipeline.on_index_complete(stats=MagicMock(), paths=[Path("a.py")])
        pipeline.lint_ops.check.assert_not_called()

class TestStop:
    """Pipeline stop behaviour."""

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self) -> None:
        pipeline = _make_pipeline()
        await pipeline.stop()
        assert pipeline._running is False
