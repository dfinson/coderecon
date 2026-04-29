"""Tests for daemon.analysis_pipeline — two-tier background analysis."""

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

@dataclass
class _FakeGraphResult:
    test_files: list[str]

def _severity(val: int) -> MagicMock:
    s = MagicMock()
    s.value = val
    return s

def _make_pipeline(*, lint_ops: MagicMock | None = None, test_ops: MagicMock | None = None):  # noqa: ANN202
    from coderecon.daemon.analysis_pipeline import AnalysisPipeline

    coordinator = MagicMock()
    coordinator.db.engine = MagicMock()
    coordinator.current_epoch = 1
    coordinator.get_affected_test_targets = AsyncMock(
        return_value=_FakeGraphResult(test_files=[])
    )

    if lint_ops is None:
        lint_ops = MagicMock()
        lint_ops.check = AsyncMock(
            return_value=_FakeLintResult(tools_run=[], total_diagnostics=0)
        )
    if test_ops is None:
        test_ops = MagicMock()
        test_ops.run = AsyncMock(return_value=MagicMock(run_status=None, passed=0, failed=0))

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

class TestTier2:
    """Tier 2: test + coverage analysis."""

    @pytest.mark.asyncio
    async def test_tier2_skips_when_no_pending(self) -> None:
        pipeline = _make_pipeline()
        # _run_tier2 with empty pending set should be a no-op
        await pipeline._run_tier2()
        pipeline.coordinator.get_affected_test_targets.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier2_skips_when_no_affected_tests(self) -> None:
        pipeline = _make_pipeline()
        pipeline._tier2_pending = {"src/a.py"}
        pipeline.coordinator.get_affected_test_targets = AsyncMock(
            return_value=_FakeGraphResult(test_files=[])
        )
        await pipeline._run_tier2()
        pipeline.test_ops.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier2_runs_affected_tests(self) -> None:
        test_ops = MagicMock()
        result_mock = MagicMock(run_status=None, passed=1, failed=0)
        test_ops.run = AsyncMock(return_value=result_mock)
        pipeline = _make_pipeline(test_ops=test_ops)
        pipeline._tier2_pending = {"src/a.py"}
        pipeline.coordinator.get_affected_test_targets = AsyncMock(
            return_value=_FakeGraphResult(test_files=["tests/test_a.py"])
        )

        await pipeline._run_tier2()

        test_ops.run.assert_called_once()
        call_kwargs = test_ops.run.call_args.kwargs
        assert call_kwargs["targets"] == ["test:tests/test_a.py"]
        assert call_kwargs["coverage"] is True

    @pytest.mark.asyncio
    async def test_tier2_handles_error_gracefully(self) -> None:
        pipeline = _make_pipeline()
        pipeline._tier2_pending = {"src/a.py"}
        pipeline.coordinator.get_affected_test_targets = AsyncMock(
            side_effect=RuntimeError("graph broke")
        )
        # Should not raise
        await pipeline._run_tier2()

class TestOnIndexComplete:
    """Integration: on_index_complete triggers tiers."""

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

    @pytest.mark.asyncio
    async def test_accumulates_tier2_pending(self) -> None:
        pipeline = _make_pipeline()
        with patch.object(pipeline, "_schedule_tier2"):
            await pipeline.on_index_complete(
                stats=MagicMock(), paths=[Path("src/a.py")]
            )
        assert "src/a.py" in pipeline._tier2_pending

class TestStop:
    """Pipeline stop behaviour."""

    @pytest.mark.asyncio
    async def test_stop_cancels_tier2(self) -> None:
        pipeline = _make_pipeline()
        pipeline._tier2_task = asyncio.create_task(asyncio.sleep(100))
        await pipeline.stop()
        assert pipeline._running is False
        assert pipeline._tier2_task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_when_no_task(self) -> None:
        pipeline = _make_pipeline()
        await pipeline.stop()
        assert pipeline._running is False

class TestScheduleTier2:
    """Tier 2 scheduling and debounce."""

    @pytest.mark.asyncio
    async def test_schedule_creates_task(self) -> None:
        pipeline = _make_pipeline()
        pipeline._schedule_tier2()
        assert pipeline._tier2_task is not None
        pipeline._tier2_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pipeline._tier2_task

    @pytest.mark.asyncio
    async def test_reschedule_cancels_previous(self) -> None:
        pipeline = _make_pipeline()
        pipeline._schedule_tier2()
        first_task = pipeline._tier2_task
        pipeline._schedule_tier2()
        assert first_task is not None
        # Let the event loop process the cancellation
        await asyncio.sleep(0)
        assert first_task.cancelled()
        # Clean up the second task
        if pipeline._tier2_task:
            pipeline._tier2_task.cancel()
            await asyncio.sleep(0)
