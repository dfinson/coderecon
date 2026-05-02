"""Test execution orchestrator — memory-gated, semaphore-limited concurrent runs."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

from coderecon.testing.emitters import CoverageArtifact
from coderecon.testing.models import (
    ExecutionDiagnostic,
    ParsedTestSuite,
    TestFailure,
    TestProgress,
    TestRunStatus,
    TestTarget,
)
from coderecon.testing.ops_runner import _run_single_target
from coderecon.testing.resources import MemoryBudget, MemoryHistory, classify_oom

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)
_STDERR_TRUNCATION_CHARS = 2000


async def _execute_tests(
    coordinator: IndexCoordinatorEngine,
    workspace_root: Path,
    memory_reserve_mb: int,
    subprocess_memory_limit_mb: int | None,
    persist_callback: object,
    run_id: str,
    targets: list[TestTarget],
    progress: TestProgress,
    failures: list[TestFailure],
    cancel_event: asyncio.Event,
    artifact_dir: Path,
    test_filter: str | None,
    tags: list[str] | None,
    parallelism: int,
    timeout_sec: int,
    fail_fast: bool,
    coverage: bool,
    coverage_dir: Path | None,
    timeout_sec_by_language: dict[str, int] | None = None,
) -> TestRunStatus:
    """Execute tests concurrently with semaphore-limited parallelism."""
    start_time = time.time()
    diagnostics: list[ExecutionDiagnostic] = []
    coverage_artifacts: list[CoverageArtifact] = []
    source_dirs: list[str] | None = None
    if coverage and coverage_dir:
        try:
            test_files = [t.selector for t in targets]
            cov_result = await coordinator.get_coverage_sources(test_files)
            if cov_result.source_dirs:
                source_dirs = cov_result.source_dirs
        except (OSError, RuntimeError, ValueError, TypeError):  # noqa: BLE001
            log.debug("testing.coverage_sources.failed", exc_info=True)
    budget = MemoryBudget(reserve_mb=memory_reserve_mb)
    history = MemoryHistory.for_repo(workspace_root)
    ceiling = subprocess_memory_limit_mb or budget.ceiling_mb()
    sem = asyncio.Semaphore(parallelism)
    _lang_timeouts = timeout_sec_by_language or {}

    def _resolve_timeout(target: TestTarget) -> int:
        return _lang_timeouts.get(target.language, timeout_sec)
    async def run_target(
        target: TestTarget,
    ) -> tuple[TestTarget, ParsedTestSuite | None, CoverageArtifact | None]:
        if cancel_event.is_set():
            return (target, None, None)
        async with sem:
            estimate = history.estimate_mb(target.target_id)
            needed = estimate if estimate else 0
            while not cancel_event.is_set():
                avail = budget.available_mb()
                if needed and avail < needed + memory_reserve_mb:
                    log.debug(
                        "memory_gate.waiting target=%s available_mb=%d estimated_mb=%d",
                        target.target_id, avail, needed,
                    )
                    await asyncio.sleep(5)
                    continue
                if not budget.can_launch():
                    log.debug(
                        "memory_gate.below_reserve target=%s available_mb=%d",
                        target.target_id, avail,
                    )
                    await asyncio.sleep(5)
                    continue
                break
            if cancel_event.is_set():
                return (target, None, None)
            target_ceiling = ceiling
            if history.oom_count(target.target_id) >= 2:
                target_ceiling = budget.ceiling_mb()
            result, cov_artifact, peak_rss = await _run_single_target(
                coordinator, target=target, artifact_dir=artifact_dir,
                test_filter=test_filter, tags=tags, timeout_sec=_resolve_timeout(target),
                coverage=coverage, coverage_dir=coverage_dir,
                source_dirs=source_dirs,
                subprocess_memory_limit_mb=target_ceiling,
            )
            stderr_text = ""
            exit_code = None
            if result and result.execution:
                stderr_text = result.execution.raw_stderr or ""
                exit_code = result.execution.exit_code
            is_oom = classify_oom(exit_code, stderr_text, peak_rss, target_ceiling)
            if is_oom:
                history.record_oom(target.target_id, peak_rss)
                log.warning(
                    "test_target.oom target=%s peak_rss_mb=%d ceiling_mb=%d",
                    target.target_id, peak_rss, target_ceiling,
                )
                retry_ceiling = budget.ceiling_mb()
                log.info(
                    "test_target.oom_retry target=%s retry_ceiling_mb=%d",
                    target.target_id, retry_ceiling,
                )
                result, cov_artifact, peak_rss = await _run_single_target(
                    coordinator, target=target, artifact_dir=artifact_dir,
                    test_filter=test_filter, tags=tags, timeout_sec=_resolve_timeout(target),
                    coverage=coverage, coverage_dir=coverage_dir,
                    source_dirs=source_dirs,
                    subprocess_memory_limit_mb=retry_ceiling,
                )
                retry_stderr = ""
                retry_exit = None
                if result and result.execution:
                    retry_stderr = result.execution.raw_stderr or ""
                    retry_exit = result.execution.exit_code
                if classify_oom(retry_exit, retry_stderr, peak_rss, retry_ceiling):
                    history.record_oom(target.target_id, peak_rss)
                    log.warning(
                        "test_target.oom_retry_failed target=%s peak_rss_mb=%d",
                        target.target_id, peak_rss,
                    )
                else:
                    history.record(target.target_id, peak_rss)
            else:
                if peak_rss > 0:
                    history.record(target.target_id, peak_rss)
            return (target, result, cov_artifact)
    all_tasks = [asyncio.create_task(run_target(t)) for t in targets]
    coverage_artifacts, diagnostics = await _collect_results(
        all_tasks, cancel_event, fail_fast, progress, failures,
    )
    duration = time.time() - start_time
    status: Literal["running", "completed", "cancelled", "failed"] = (
        "cancelled" if cancel_event.is_set() else "completed"
    )
    coverage_dicts = [
        {"format": c.format, "path": str(c.path), "pack_id": c.pack_id}
        for c in coverage_artifacts
    ]
    final_status = TestRunStatus(
        run_id=run_id, status=status, progress=progress,
        failures=failures, diagnostics=diagnostics,
        duration_seconds=duration, artifact_dir=str(artifact_dir),
        coverage=coverage_dicts,
        target_selectors=[t.selector for t in targets],
    )
    persist_callback(artifact_dir, final_status)  # type: ignore[operator]
    return final_status


async def _collect_results(
    all_tasks: list[asyncio.Task[tuple[TestTarget, ParsedTestSuite | None, CoverageArtifact | None]]],
    cancel_event: asyncio.Event,
    fail_fast: bool,
    progress: TestProgress,
    failures: list[TestFailure],
) -> tuple[list[CoverageArtifact], list[ExecutionDiagnostic]]:
    """Await completed tasks and aggregate results into *progress* and *failures*."""
    coverage_artifacts: list[CoverageArtifact] = []
    diagnostics: list[ExecutionDiagnostic] = []
    for coro in asyncio.as_completed(all_tasks):
        if cancel_event.is_set() or (fail_fast and progress.cases.failed > 0):
            for t in all_tasks:
                t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await coro
            continue
        try:
            target, result, cov_artifact = await coro
        except asyncio.CancelledError:
            structlog.get_logger().debug("test_task_cancelled", exc_info=True)
            continue
        if cov_artifact:
            coverage_artifacts.append(cov_artifact)
        if result:
            progress.targets.completed += 1
            progress.cases.passed += result.passed
            progress.cases.failed += result.failed
            progress.cases.skipped += result.skipped
            progress.cases.errors += result.errors
            progress.cases.total += result.total
            if result.failed > 0 or result.errors > 0:
                progress.targets.failed += 1
            if result.error_type != "none":
                truncated_stderr = None
                if result.execution and result.execution.raw_stderr:
                    stderr_text = result.execution.raw_stderr
                    if len(stderr_text) > _STDERR_TRUNCATION_CHARS:
                        truncated_stderr = stderr_text[:_STDERR_TRUNCATION_CHARS] + "\n... (truncated)"
                    else:
                        truncated_stderr = stderr_text
                diagnostics.append(
                    ExecutionDiagnostic(
                        target_id=target.target_id,
                        error_type=result.error_type,
                        error_detail=result.error_detail,
                        suggested_action=result.suggested_action,
                        command=result.execution.command if result.execution else None,
                        working_directory=(
                            result.execution.working_directory if result.execution else None
                        ),
                        exit_code=result.execution.exit_code if result.execution else None,
                        raw_stderr=truncated_stderr,
                        parsed_test_count=result.parsed_test_count,
                    )
                )
            for test in result.tests:
                if test.status in ("failed", "error"):
                    failures.append(
                        TestFailure(
                            name=test.name,
                            path=test.file_path or test.classname or "",
                            line=test.line_number,
                            message=test.message or "Test failed",
                            traceback=test.traceback,
                            classname=test.classname,
                            duration_seconds=test.duration_seconds,
                        )
                    )
    return coverage_artifacts, diagnostics
