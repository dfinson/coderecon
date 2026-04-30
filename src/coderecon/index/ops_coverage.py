"""Post-reindex coverage collection — inherent to every index/reindex operation.

Runs tests with coverage after each reindex and ingests results into
TestCoverageFact rows. Best-effort: never raises, logs on failure.

Two modes:
  - Incremental: uses import graph to discover affected tests for changed files.
  - Full: runs the entire test suite with coverage.

IMPORTANT: This runs while still inside the reindex operation, BEFORE the
freshness gate is released. We must bypass wait_for_freshness() in all paths
to avoid a 30s timeout deadlock in the daemon thread-pool path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


async def run_coverage_for_changed_files(
    engine: IndexCoordinatorEngine,
    changed_files: list[str],
    *,
    worktree: str = "main",
    timeout_sec: int = 60,
) -> int:
    """Run affected tests with coverage for a set of changed files.

    Returns the number of TestCoverageFact rows written, or 0 on failure/skip.
    Never raises.
    """
    if not changed_files:
        return 0

    try:
        # Query import graph directly — we're inside the reindex operation
        # so the freshness gate hasn't been released yet. Calling the public
        # get_affected_test_targets() would deadlock on wait_for_freshness().
        from coderecon.index.graph.import_graph import ImportGraph

        with engine.db.session() as session:
            graph = ImportGraph(session)
            graph_result = graph.affected_tests(changed_files)

        if not graph_result.test_files:
            log.debug("post_reindex_coverage.no_affected_tests", changed=len(changed_files))
            return 0

        return await _run_and_ingest(
            engine,
            targets=[f"test:{f}" for f in graph_result.test_files],
            timeout_sec=timeout_sec,
        )
    except Exception:
        log.debug("post_reindex_coverage.failed", exc_info=True)
        return 0


async def run_coverage_full(
    engine: IndexCoordinatorEngine,
    *,
    timeout_sec: int = 300,
) -> int:
    """Run the full test suite with coverage.

    Returns the number of TestCoverageFact rows written, or 0 on failure/skip.
    Never raises.
    """
    try:
        return await _run_and_ingest(engine, targets=None, timeout_sec=timeout_sec)
    except Exception:
        log.debug("post_reindex_coverage.full_failed", exc_info=True)
        return 0


async def _run_and_ingest(
    engine: IndexCoordinatorEngine,
    targets: list[str] | None,
    *,
    timeout_sec: int = 60,
) -> int:
    """Run tests with coverage and ingest artifacts. Never raises."""
    from coderecon.testing.ops import TestOps

    # Resolve the effective repo root for this engine (supports worktrees)
    repo_root = engine.repo_root
    test_ops = TestOps(repo_root, engine)

    # Temporarily disable the freshness gate so that TestOps internal calls
    # to get_test_targets() / wait_for_freshness() don't block. We're inside
    # the reindex operation — the data IS fresh, but the gate hasn't been
    # released yet (mark_fresh happens after _index_sync returns).
    saved_gate = engine._freshness_gate
    engine._freshness_gate = None
    try:
        result = await test_ops.run(
            targets=targets,
            coverage=True,
            fail_fast=True,
            timeout_sec=timeout_sec,
        )
    finally:
        engine._freshness_gate = saved_gate

    if not result.run_status or not result.run_status.coverage:
        log.debug("post_reindex_coverage.no_artifacts", targets=targets)
        return 0

    from coderecon.testing.coverage import (
        merge,
        parse_artifact,
    )
    from coderecon.testing.coverage.models import CoverageParseError

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
                base_path=repo_root,
            )
            reports.append(report)
        except (CoverageParseError, ValueError, KeyError, OSError):
            log.debug("post_reindex_coverage.parse_failed", path=cov_path, exc_info=True)

    if not reports:
        log.debug("post_reindex_coverage.no_reports")
        return 0

    from coderecon.index.analysis.coverage_ingestion import ingest_coverage

    merged = merge(*reports) if len(reports) > 1 else reports[0]
    epoch = engine.current_epoch

    failed_ids: set[str] | None = None
    if result.run_status and result.run_status.failures:
        failed_ids = {
            f"{f.path}::{f.name}"
            for f in result.run_status.failures
        }

    written = ingest_coverage(
        engine.db.engine, merged, epoch, failed_test_ids=failed_ids,
    )
    log.info("post_reindex_coverage.ingested", facts=written, targets=targets)
    return written
