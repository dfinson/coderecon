"""Background analysis pipeline (Tier 1: lint after indexing).

Runs after each indexing pass:
  Tier 1 (~200ms): lint on changed files → LintStatusFact

Coverage collection (formerly Tier 2) is now inherent to the reindex
operations themselves — see ops_coverage.py.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine, IndexStats
    from coderecon.lint.ops import LintOps
    from coderecon.testing.ops import TestOps

log = structlog.get_logger(__name__)

@dataclass
class AnalysisPipeline:
    """Background analysis that runs after indexing.

    Tier 1 (fast): lint changed files, persist LintStatusFact

    Coverage (formerly Tier 2) is now run as an inherent step inside
    reindex_incremental/reindex_full — see coderecon.index.ops_coverage.
    """

    coordinator: IndexCoordinatorEngine
    lint_ops: LintOps
    test_ops: TestOps
    repo_root: Path

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

    async def stop(self) -> None:
        """Stop the pipeline gracefully."""
        self._running = False
