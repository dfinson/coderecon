"""Two-tier background analysis pipeline.

Runs after each indexing pass:
  Tier 1 (~200ms): lint on changed files → LintStatusFact
  Tier 2 (~2s debounce): tests with coverage on affected defs → TestCoverageFact

Both tiers are fire-and-forget background tasks that persist facts to the DB.
Checkpoint can read these facts instead of re-running lint/tests.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from coderecon.testing.coverage.models import CoverageParseError

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine, IndexStats
    from coderecon.lint.ops import LintOps
    from coderecon.testing.ops import TestOps

log = structlog.get_logger(__name__)

# Tier 2 debounce: batch rapid changes before running tests
_TIER2_DEBOUNCE_SEC = 2.0

@dataclass
class AnalysisPipeline:
    """Background analysis that runs after indexing.

    Tier 1 (fast): lint changed files, persist LintStatusFact
    Tier 2 (slow): run affected tests with coverage, persist TestCoverageFact
    """

    coordinator: IndexCoordinatorEngine
    lint_ops: LintOps
    test_ops: TestOps
    repo_root: Path

    _tier2_task: asyncio.Task[None] | None = field(default=None, init=False)
    _tier2_pending: set[str] = field(default_factory=set, init=False)
    _running: bool = field(default=True, init=False)

    async def on_index_complete(self, stats: IndexStats, paths: list[Path]) -> None:
        """Called by BackgroundIndexer after a successful reindex.

        Args:
            stats: Index operation statistics.
            paths: File paths that were reindexed.
        """
        if not self._running or not paths:
            return

        str_paths = [str(p) for p in paths]

        # Tier 1: quick lint (fire-and-forget)
        asyncio.get_event_loop().create_task(
            self._run_tier1(str_paths),
            name="analysis-tier1",
        )

        # Tier 2: accumulate paths and debounce
        self._tier2_pending.update(str_paths)
        self._schedule_tier2()

    async def _run_tier1(self, paths: list[str]) -> None:
        """Tier 1: lint changed files → persist LintStatusFact."""
        t0 = time.monotonic()
        try:
            result = await self.lint_ops.check(paths=paths, dry_run=True)
            engine = self.coordinator.db.engine
            epoch = self.coordinator.current_epoch

            from coderecon.index.analysis.lint_status import persist_lint_status

            for tool_result in result.tools_run:
                # Group diagnostics by file
                file_counts: dict[str, dict[str, int]] = {}
                for diag in tool_result.diagnostics:
                    counts = file_counts.setdefault(diag.path, {"e": 0, "w": 0, "i": 0})
                    if diag.severity.value >= 2:  # noqa: PLR2004
                        counts["e"] += 1
                    elif diag.severity.value >= 1:
                        counts["w"] += 1
                    else:
                        counts["i"] += 1

                # Persist per-file status
                for file_path, counts in file_counts.items():
                    persist_lint_status(
                        engine=engine,
                        file_path=file_path,
                        tool_id=tool_result.tool_id,
                        category="lint",
                        error_count=counts["e"],
                        warning_count=counts["w"],
                        info_count=counts["i"],
                        epoch=epoch,
                    )

                # Files with no diagnostics → mark clean
                checked_files = {diag.path for diag in tool_result.diagnostics}
                for p in paths:
                    if p not in checked_files:
                        persist_lint_status(
                            engine=engine,
                            file_path=p,
                            tool_id=tool_result.tool_id,
                            category="lint",
                            error_count=0,
                            warning_count=0,
                            info_count=0,
                            epoch=epoch,
                        )

            elapsed = time.monotonic() - t0
            log.debug(
                "tier1_complete",
                files=len(paths),
                tools=len(result.tools_run),
                diagnostics=result.total_diagnostics,
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception:
            log.warning("tier1_failed", exc_info=True)

    def _schedule_tier2(self) -> None:
        """Schedule or reschedule tier 2 with debounce."""
        if self._tier2_task is not None and not self._tier2_task.done():
            self._tier2_task.cancel()

        self._tier2_task = asyncio.get_event_loop().create_task(
            self._debounced_tier2(),
            name="analysis-tier2",
        )

    async def _debounced_tier2(self) -> None:
        """Wait for debounce then run tier 2."""
        try:
            await asyncio.sleep(_TIER2_DEBOUNCE_SEC)
            await self._run_tier2()
        except asyncio.CancelledError:
            structlog.get_logger().debug("tier2_debounce_cancelled", exc_info=True)
            pass

    async def _run_tier2(self) -> None:
        """Tier 2: run affected tests with coverage → persist TestCoverageFact."""
        if not self._tier2_pending:
            return

        paths = list(self._tier2_pending)
        self._tier2_pending.clear()
        t0 = time.monotonic()

        try:
            # Discover affected tests via import graph
            graph_result = await self.coordinator.get_affected_test_targets(paths)
            if not graph_result.test_files:
                log.debug("tier2_no_affected_tests", changed_files=len(paths))
                return

            # Run tests with coverage — target_ids need "test:" prefix
            result = await self.test_ops.run(
                targets=[f"test:{f}" for f in graph_result.test_files],
                coverage=True,
                fail_fast=True,
                timeout_sec=60,
            )

            # Ingest coverage from artifacts produced by test runners
            if result.run_status and result.run_status.coverage:
                from coderecon.testing.coverage import (
                    merge,
                    parse_artifact,
                )

                reports = []
                for cov in result.run_status.coverage:
                    cov_path = cov.get("path", "")
                    if not cov_path:
                        continue
                    try:
                        fmt = cov.get("format")
                        report = parse_artifact(
                            Path(cov_path),
                            format_id=fmt if fmt and fmt != "unknown" else None,
                            base_path=self.repo_root,
                        )
                        reports.append(report)
                    except (CoverageParseError, OSError):  # best-effort coverage parse
                        log.debug("tier2_coverage_parse_failed", path=cov_path, exc_info=True)

                if reports:
                    from coderecon.index.analysis.coverage_ingestion import (
                        ingest_coverage,
                    )

                    merged = merge(*reports) if len(reports) > 1 else reports[0]
                    engine = self.coordinator.db.engine
                    epoch = self.coordinator.current_epoch
                    failed_ids: set[str] | None = None
                    if result.run_status and result.run_status.failures:
                        failed_ids = {
                            f"{f.path}::{f.name}"
                            for f in result.run_status.failures
                        }
                    written = ingest_coverage(
                        engine, merged, epoch, failed_test_ids=failed_ids,
                    )
                    log.debug("tier2_coverage_ingested", facts=written)

            elapsed = time.monotonic() - t0
            log.debug(
                "tier2_complete",
                affected_tests=len(graph_result.test_files),
                passed=getattr(result, "passed", 0),
                failed=getattr(result, "failed", 0),
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception:
            log.warning("tier2_failed", exc_info=True)

    async def stop(self) -> None:
        """Stop the pipeline gracefully."""
        self._running = False
        if self._tier2_task is not None and not self._tier2_task.done():
            self._tier2_task.cancel()
            try:
                await self._tier2_task
            except asyncio.CancelledError:
                structlog.get_logger().debug("tier2_task_stop_cancelled", exc_info=True)
                pass
