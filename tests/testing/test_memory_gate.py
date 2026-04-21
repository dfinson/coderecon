"""Tests for memory-aware test execution in TestOps.

Covers the memory gate, RSS tracking, OOM retry, and history integration
added to _execute_tests / _run_single_target.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.testing.models import (
    ExecutionContext,
    ParsedTestSuite,
    TargetProgress,
    TestCaseProgress,
    TestProgress,
    TestTarget,
)
from coderecon.testing.ops import TestOps


def _make_progress() -> TestProgress:
    return TestProgress(
        targets=TargetProgress(),
        cases=TestCaseProgress(),
    )


def _make_target(tid: str = "t1", pack_id: str = "python.pytest") -> TestTarget:
    return TestTarget(
        target_id=tid,
        selector=tid,
        kind="file",
        language="python",
        runner_pack_id=pack_id,
        workspace_root="/fake/repo",
        estimated_cost=1.0,
        test_count=1,
    )


def _make_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.get_file_stats = AsyncMock(return_value={"python": 1})
    coord.get_indexed_file_count = AsyncMock(return_value=1)
    coord.get_indexed_files = AsyncMock(return_value=[])
    coord.get_contexts = AsyncMock(return_value=[])
    coord.get_test_targets = AsyncMock(return_value=[])
    coord.get_context_runtime = AsyncMock(return_value=None)
    coord.get_coverage_capability = AsyncMock(return_value={})
    coord.get_coverage_sources = AsyncMock(
        return_value=MagicMock(source_dirs=None)
    )
    return coord


class TestTestOpsMemoryParams:
    """Test that TestOps accepts and stores memory parameters."""

    def test_default_memory_params(self, tmp_path: Path) -> None:
        ops = TestOps(tmp_path, _make_coordinator())
        assert ops._memory_reserve_mb == 1024
        assert ops._subprocess_memory_limit_mb is None

    def test_custom_memory_params(self, tmp_path: Path) -> None:
        ops = TestOps(
            tmp_path,
            _make_coordinator(),
            memory_reserve_mb=2048,
            subprocess_memory_limit_mb=4096,
        )
        assert ops._memory_reserve_mb == 2048
        assert ops._subprocess_memory_limit_mb == 4096


class TestMemoryGateIntegration:
    """Integration tests for the memory gate inside _execute_tests."""

    @pytest.mark.asyncio
    async def test_gate_blocks_when_below_reserve(self, tmp_path: Path) -> None:
        """When available memory is below reserve, gate should delay launch."""
        ops = TestOps(tmp_path, _make_coordinator(), memory_reserve_mb=8000)

        call_count = 0

        def mock_available():
            nonlocal call_count
            call_count += 1
            vm = MagicMock()
            # First 2 calls: below reserve; then: above reserve
            if call_count <= 2:
                vm.available = 4000 * 1024 * 1024  # 4GB < 8GB reserve
            else:
                vm.available = 10000 * 1024 * 1024  # 10GB > 8GB reserve
            return vm

        # Patch psutil + _run_single_target to short-circuit
        normal_result = (
            ParsedTestSuite(name="t1", passed=1, total=1),
            None,
            50,  # peak_rss_mb
        )

        with (
            patch("coderecon.testing.resources.psutil.virtual_memory", side_effect=mock_available),
            patch.object(ops, "_run_single_target", new=AsyncMock(return_value=normal_result)),
        ):
            cancel = asyncio.Event()
            progress = _make_progress()
            failures: list = []
            (tmp_path / "art").mkdir(exist_ok=True)

            # Patch asyncio.sleep to not actually wait
            with patch("coderecon.testing.ops.asyncio.sleep", new=AsyncMock()):
                result = await ops._execute_tests(
                    run_id="test1",
                    targets=[_make_target()],
                    progress=progress,
                    failures=failures,
                    cancel_event=cancel,
                    artifact_dir=tmp_path / "art",
                    test_filter=None,
                    tags=None,
                    parallelism=4,
                    timeout_sec=60,
                    fail_fast=False,
                    coverage=False,
                    coverage_dir=None,
                )

            # Should have called virtual_memory multiple times (gate polling)
            assert call_count >= 3

    @pytest.mark.asyncio
    async def test_ceiling_passed_to_run_single(self, tmp_path: Path) -> None:
        """Verify subprocess_memory_limit_mb flows to _run_single_target."""
        ops = TestOps(
            tmp_path,
            _make_coordinator(),
            subprocess_memory_limit_mb=2048,
        )

        captured_kwargs: dict = {}

        async def capture_single(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return (
                ParsedTestSuite(name="t1", passed=1, total=1),
                None,
                100,
            )

        vm_ok = MagicMock()
        vm_ok.available = 12000 * 1024 * 1024

        with (
            patch("coderecon.testing.resources.psutil.virtual_memory", return_value=vm_ok),
            patch.object(ops, "_run_single_target", side_effect=capture_single),
        ):
            cancel = asyncio.Event()
            progress = _make_progress()
            (tmp_path / "art").mkdir(exist_ok=True)

            await ops._execute_tests(
                run_id="test2",
                targets=[_make_target()],
                progress=progress,
                failures=[],
                cancel_event=cancel,
                artifact_dir=tmp_path / "art",
                test_filter=None,
                tags=None,
                parallelism=4,
                timeout_sec=60,
                fail_fast=False,
                coverage=False,
                coverage_dir=None,
            )

        assert captured_kwargs.get("subprocess_memory_limit_mb") == 2048


class TestOOMRetry:
    """Tests for OOM detection and automatic retry."""

    @pytest.mark.asyncio
    async def test_retries_on_oom(self, tmp_path: Path) -> None:
        """When OOM is detected, should retry once with larger ceiling."""
        ops = TestOps(tmp_path, _make_coordinator(), subprocess_memory_limit_mb=1024)

        call_count = 0

        async def mock_run_single(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: OOM (exit code 137, RSS near ceiling)
                return (
                    ParsedTestSuite(
                        name="t1",
                        errors=1,
                        total=0,
                        error_type="command_failed",
                        execution=ExecutionContext(
                            command=["test"],
                            working_directory="/fake",
                            exit_code=137,
                            raw_stderr="Killed",
                        ),
                    ),
                    None,
                    900,  # near ceiling of 1024
                )
            else:
                # Retry: success
                return (
                    ParsedTestSuite(name="t1", passed=5, total=5),
                    None,
                    1800,
                )

        vm_ok = MagicMock()
        vm_ok.available = 12000 * 1024 * 1024

        with (
            patch("coderecon.testing.resources.psutil.virtual_memory", return_value=vm_ok),
            patch.object(ops, "_run_single_target", side_effect=mock_run_single),
        ):
            cancel = asyncio.Event()
            progress = _make_progress()
            (tmp_path / "art").mkdir(exist_ok=True)

            await ops._execute_tests(
                run_id="oom-test",
                targets=[_make_target()],
                progress=progress,
                failures=[],
                cancel_event=cancel,
                artifact_dir=tmp_path / "art",
                test_filter=None,
                tags=None,
                parallelism=4,
                timeout_sec=60,
                fail_fast=False,
                coverage=False,
                coverage_dir=None,
            )

        # Should have been called twice: original + retry
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_normal_failure(self, tmp_path: Path) -> None:
        """Normal test failures should NOT trigger retry."""
        ops = TestOps(tmp_path, _make_coordinator())

        call_count = 0

        async def mock_run_single(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return (
                ParsedTestSuite(
                    name="t1",
                    passed=3,
                    failed=2,
                    total=5,
                    execution=ExecutionContext(
                        command=["test"],
                        working_directory="/fake",
                        exit_code=1,
                        raw_stderr="FAILED test_foo - AssertionError",
                    ),
                ),
                None,
                200,  # low RSS
            )

        vm_ok = MagicMock()
        vm_ok.available = 12000 * 1024 * 1024

        with (
            patch("coderecon.testing.resources.psutil.virtual_memory", return_value=vm_ok),
            patch.object(ops, "_run_single_target", side_effect=mock_run_single),
        ):
            cancel = asyncio.Event()
            progress = _make_progress()
            (tmp_path / "art").mkdir(exist_ok=True)

            await ops._execute_tests(
                run_id="no-retry",
                targets=[_make_target()],
                progress=progress,
                failures=[],
                cancel_event=cancel,
                artifact_dir=tmp_path / "art",
                test_filter=None,
                tags=None,
                parallelism=4,
                timeout_sec=60,
                fail_fast=False,
                coverage=False,
                coverage_dir=None,
            )

        # Should have been called only once
        assert call_count == 1


class TestHistoryRecording:
    """Tests for RSS history recording after execution."""

    @pytest.mark.asyncio
    async def test_records_peak_rss_after_normal_run(self, tmp_path: Path) -> None:
        ops = TestOps(tmp_path, _make_coordinator())

        async def mock_run_single(*args, **kwargs):
            return (
                ParsedTestSuite(name="t1", passed=1, total=1),
                None,
                512,
            )

        vm_ok = MagicMock()
        vm_ok.available = 12000 * 1024 * 1024

        with (
            patch("coderecon.testing.resources.psutil.virtual_memory", return_value=vm_ok),
            patch.object(ops, "_run_single_target", side_effect=mock_run_single),
        ):
            cancel = asyncio.Event()
            progress = _make_progress()
            (tmp_path / "art").mkdir(exist_ok=True)

            await ops._execute_tests(
                run_id="hist-test",
                targets=[_make_target("mytest")],
                progress=progress,
                failures=[],
                cancel_event=cancel,
                artifact_dir=tmp_path / "art",
                test_filter=None,
                tags=None,
                parallelism=4,
                timeout_sec=60,
                fail_fast=False,
                coverage=False,
                coverage_dir=None,
            )

        # Verify history was written
        from coderecon.testing.resources import MemoryHistory

        history = MemoryHistory.for_repo(tmp_path)
        assert history.estimate_mb("mytest") == 512

    @pytest.mark.asyncio
    async def test_records_oom_count_after_oom(self, tmp_path: Path) -> None:
        ops = TestOps(tmp_path, _make_coordinator(), subprocess_memory_limit_mb=1024)

        async def mock_run_single(*args, **kwargs):
            # Always OOM
            return (
                ParsedTestSuite(
                    name="t1",
                    errors=1,
                    total=0,
                    execution=ExecutionContext(
                        command=["test"],
                        working_directory="/fake",
                        exit_code=137,
                        raw_stderr="Killed",
                    ),
                ),
                None,
                950,
            )

        vm_ok = MagicMock()
        vm_ok.available = 12000 * 1024 * 1024

        with (
            patch("coderecon.testing.resources.psutil.virtual_memory", return_value=vm_ok),
            patch.object(ops, "_run_single_target", side_effect=mock_run_single),
        ):
            cancel = asyncio.Event()
            progress = _make_progress()
            (tmp_path / "art").mkdir(exist_ok=True)

            await ops._execute_tests(
                run_id="oom-hist",
                targets=[_make_target("heavy")],
                progress=progress,
                failures=[],
                cancel_event=cancel,
                artifact_dir=tmp_path / "art",
                test_filter=None,
                tags=None,
                parallelism=4,
                timeout_sec=60,
                fail_fast=False,
                coverage=False,
                coverage_dir=None,
            )

        from coderecon.testing.resources import MemoryHistory

        history = MemoryHistory.for_repo(tmp_path)
        # Both original OOM and retry OOM should have been recorded
        assert history.oom_count("heavy") >= 1


class TestSafeExecutionConfigCeiling:
    """Test that _run_single_target passes ceiling to SafeExecutionConfig."""

    @pytest.mark.asyncio
    async def test_config_receives_memory_limit(self, tmp_path: Path) -> None:
        """Verify SafeExecutionConfig gets subprocess_memory_limit_mb."""
        from coderecon.testing.safe_execution import SafeExecutionConfig

        captured_configs: list[SafeExecutionConfig] = []
        original_init = SafeExecutionConfig.__init__

        def spy_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            captured_configs.append(self)

        ops = TestOps(tmp_path, _make_coordinator(), subprocess_memory_limit_mb=3000)

        # Need a registered pack for _run_single_target to proceed
        target = _make_target("t1", "python.pytest")

        with patch.object(SafeExecutionConfig, "__init__", spy_init):
            # This will fail at command building since we're in a fake repo,
            # but SafeExecutionConfig should have been created before that
            try:
                await ops._run_single_target(
                    target=target,
                    artifact_dir=tmp_path,
                    test_filter=None,
                    tags=None,
                    timeout_sec=60,
                    coverage=False,
                    coverage_dir=None,
                    subprocess_memory_limit_mb=3000,
                )
            except Exception:
                pass

        # Check that at least one config was created with our ceiling
        ceiling_values = [c.subprocess_memory_limit_mb for c in captured_configs]
        assert 3000 in ceiling_values
