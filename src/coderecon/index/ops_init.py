"""Initialization operations for the index coordinator.

Standalone functions extracted from IndexCoordinatorEngine. Each takes
``engine`` as its first parameter.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from coderecon.index._internal.db import (
    EpochManager,
    Reconciler,
    create_additional_indexes,
)
from coderecon.index._internal.discovery import (
    ContextDiscovery,
    ContextProbe,
    ContextRouter,
    MembershipResolver,
    Tier1AuthorityFilter,
)
from coderecon.index._internal.indexing import (
    LexicalIndex,
    StructuralIndexer,
)
from coderecon.index._internal.parsing.service import tree_sitter_service
from coderecon.index._internal.state import FileStateService
from coderecon.index.models import (
    CandidateContext,
    Context,
    ContextMarker,
    ProbeStatus,
)
from coderecon.index.ops_types import InitResult

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


async def initialize(
    engine: IndexCoordinatorEngine,
    on_index_progress: Callable[[int, int, dict[str, int], str], None],
) -> InitResult:
    """Full initialization: discover, probe, index."""
    errors: list[str] = []
    # Step 1-2: Database setup
    engine.db.create_all()
    create_additional_indexes(engine.db.engine)
    engine._get_or_create_worktree_id("main")
    # Initialize components
    engine._parser = tree_sitter_service.parser
    engine._lexical = LexicalIndex(engine.tantivy_path)
    engine._epoch_manager = EpochManager(engine.db, engine._lexical)
    # Step 3: Discover contexts
    discovery = ContextDiscovery(engine.repo_root)
    discovery_result = discovery.discover_all()
    all_candidates = discovery_result.candidates
    root_fallback = next(
        (c for c in all_candidates if getattr(c, "is_root_fallback", False)),
        None,
    )
    regular_candidates = [
        c for c in all_candidates if not getattr(c, "is_root_fallback", False)
    ]
    # Step 4: Apply authority filter
    authority = Tier1AuthorityFilter(engine.repo_root)
    authority_result = authority.apply(regular_candidates)
    pending_candidates = authority_result.pending
    detached_candidates = authority_result.detached
    # Step 5: Resolve membership
    membership = MembershipResolver()
    membership_result = membership.resolve(pending_candidates)
    resolved_candidates = membership_result.contexts
    # Step 6: Probe contexts
    probe = ContextProbe(engine.repo_root, parser=engine._parser)
    probed_candidates: list[CandidateContext] = []
    for candidate in resolved_candidates:
        probe_result = probe.validate(candidate)
        if probe_result.valid:
            candidate.probe_status = ProbeStatus.VALID
        elif probe_result.reason and "empty" in probe_result.reason.lower():
            candidate.probe_status = ProbeStatus.EMPTY
        else:
            candidate.probe_status = ProbeStatus.FAILED
        probed_candidates.append(candidate)
    if root_fallback is not None:
        probed_candidates.append(root_fallback)
    # Step 7: Persist contexts
    contexts_valid = 0
    contexts_failed = 0
    with engine.db.session() as session:
        for candidate in probed_candidates:
            if getattr(candidate, "is_root_fallback", False):
                name = "_root"
            else:
                name = candidate.root_path or "root"
            context = Context(
                name=name,
                language_family=candidate.language_family.value,
                root_path=candidate.root_path,
                tier=candidate.tier,
                probe_status=candidate.probe_status.value,
                include_spec=json.dumps(candidate.include_spec)
                if candidate.include_spec
                else None,
                exclude_spec=json.dumps(candidate.exclude_spec)
                if candidate.exclude_spec
                else None,
            )
            session.add(context)
            session.flush()
            for marker_path in candidate.markers:
                marker = ContextMarker(
                    context_id=context.id,
                    marker_path=marker_path,
                    marker_tier="tier1" if candidate.tier == 1 else "tier2",
                    detected_at=time.time(),
                )
                session.add(marker)
            if candidate.probe_status == ProbeStatus.VALID:
                contexts_valid += 1
            elif candidate.probe_status == ProbeStatus.FAILED:
                contexts_failed += 1
        for candidate in detached_candidates:
            context = Context(
                name=candidate.root_path or "root",
                language_family=candidate.language_family.value,
                root_path=candidate.root_path,
                tier=candidate.tier,
                probe_status=ProbeStatus.DETACHED.value,
            )
            session.add(context)
        session.commit()
    # Step 7.4-7.7: Runtimes, test targets, lint tools, coverage
    await engine._resolve_context_runtimes()
    await engine._discover_test_targets()
    await engine._discover_lint_tools()
    await engine._discover_coverage_capabilities()
    # Step 8: Initialize router and remaining components
    engine._router = ContextRouter()
    engine._structural = StructuralIndexer(engine.db, engine.repo_root)
    engine._state = FileStateService(engine.db)
    engine._reconciler = Reconciler(engine.db, engine.repo_root)
    engine._reconciler.reconcile(
        paths=[],
        worktree_id=engine._get_or_create_worktree_id("main"),
    )
    engine._facts = None
    # Step 9: Index all files
    files_indexed, indexed_paths, files_by_ext = await engine._index_all_files(
        on_progress=on_index_progress
    )
    if engine._lexical is not None:
        engine._lexical.reload()
    # Step 10: Publish initial epoch
    if engine._epoch_manager is not None:
        engine._epoch_manager.publish_epoch(
            files_indexed=files_indexed,
            indexed_paths=indexed_paths,
        )
    engine._initialized = True
    return InitResult(
        contexts_discovered=len(all_candidates),
        contexts_valid=contexts_valid,
        contexts_failed=contexts_failed,
        contexts_detached=len(detached_candidates),
        files_indexed=files_indexed,
        errors=errors,
        files_by_ext=files_by_ext,
    )


async def collect_initial_coverage(
    engine: IndexCoordinatorEngine,
    *,
    parallelism: int | None = None,
    memory_reserve_mb: int = 1024,
    subprocess_memory_limit_mb: int | None = None,
) -> int:
    """Run the full test suite with coverage and ingest results.
    Best-effort: returns 0 and logs on failure.  Never raises.
    """
    try:
        from coderecon.testing.ops import TestOps
        test_ops = TestOps(
            engine.repo_root,
            engine,
            memory_reserve_mb=memory_reserve_mb,
            subprocess_memory_limit_mb=subprocess_memory_limit_mb,
        )
        result = await test_ops.run(
            targets=None,
            coverage=True,
            fail_fast=False,
            parallelism=parallelism,
        )
        if not result.run_status or not result.run_status.coverage:
            log.debug("initial_coverage.no_artifacts")
            return 0
        from coderecon.testing.coverage import (
            CoverageParseError,
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
                    base_path=engine.repo_root,
                )
                reports.append(report)
            except (CoverageParseError, ValueError, KeyError, OSError):
                log.debug("initial_coverage.parse_failed", extra={"path": cov_path}, exc_info=True)
        if not reports:
            log.debug("initial_coverage.no_reports")
            return 0
        from coderecon.index._internal.analysis.coverage_ingestion import (
            ingest_coverage,
        )
        merged = merge(*reports) if len(reports) > 1 else reports[0]
        failed_ids: set[str] | None = None
        if result.run_status and result.run_status.failures:
            failed_ids = {
                f"{f.path}::{f.name}"
                for f in result.run_status.failures
            }
        written = ingest_coverage(
            engine.db.engine, merged, engine.current_epoch,
            failed_test_ids=failed_ids,
        )
        log.info("initial_coverage.ingested", extra={"facts": written})
        return written
    except (OSError, ValueError, RuntimeError):
        log.debug("initial_coverage.failed", exc_info=True)
        return 0
