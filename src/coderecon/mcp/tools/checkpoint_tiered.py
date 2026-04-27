"""Checkpoint tiered testing: verify summary, hop assignment, test execution."""

from __future__ import annotations

import asyncio
import textwrap
from typing import TYPE_CHECKING, Any

import structlog

from coderecon.mcp.tools.checkpoint_helpers import (
    _build_coverage_text,
    _build_failure_snippets,
    _extract_traceback_locations,
    _normalize_selector,
    _serialize_test_result,
    _summarize_run,
    _target_matches_affected_files,
)

if TYPE_CHECKING:
    from coderecon.mcp.tools.checkpoint import ProgressSink
    from coderecon.testing.models import TestResult, TestTarget

log = structlog.get_logger(__name__)

def _summarize_verify(
    lint_status: str,
    lint_diagnostics: int,
    test_passed: int,
    test_failed: int,
    test_status: str,
) -> str:
    """Generate compact summary for verify result."""
    parts: list[str] = []
    if lint_status == "clean":
        parts.append("lint: clean")
    elif lint_status == "skipped":
        parts.append("lint: skipped")
    elif lint_diagnostics > 0:
        parts.append(f"lint: {lint_diagnostics} issues")
    else:
        parts.append(f"lint: {lint_status}")
    if test_status == "skipped":
        parts.append("tests: skipped")
    elif test_failed > 0:
        parts.append(f"tests: {test_passed} passed, {test_failed} FAILED")
    elif test_passed > 0:
        parts.append(f"tests: {test_passed} passed")
    else:
        parts.append(f"tests: {test_status}")
    return " | ".join(parts)

# Tiered Test Execution

# Targets with estimated_cost at or below this threshold are batched together
# into a single subprocess call when they share the same runner + workspace.
_BATCH_COST_THRESHOLD = 1.0

def _assign_target_hops(
    targets: list[TestTarget],
    graph_result: ImportGraphResult,
    repo_root: Path,
) -> dict[int, list[TestTarget]]:
    """Map test targets to their import-graph hop distance.
    Returns dict[hop_number, list_of_targets].  Targets that don't match any
    hop in the graph result (e.g., discovered-but-not-in-graph) default to
    hop 0 to ensure they always run.
    """
    from coderecon.index._internal.indexing.import_graph import ImportGraphResult
    if not isinstance(graph_result, ImportGraphResult):
        raise TypeError(f"Expected ImportGraphResult, got {type(graph_result).__name__}")
    tests_by_hop = graph_result.tests_by_hop()
    # Build reverse map: test_file -> hop
    file_to_hop: dict[str, int] = {}
    for hop, files in tests_by_hop.items():
        for f in files:
            if f not in file_to_hop:
                file_to_hop[f] = hop
    # Map targets to hops via _target_matches_affected_files logic
    hop_targets: dict[int, list[Any]] = {}
    for target in targets:
        ws = Path(target.workspace_root)
        sel = _normalize_selector(target.selector)
        scope_abs = ws / sel if sel else ws
        try:
            scope_rel = str(scope_abs.relative_to(repo_root))
        except ValueError:
            scope_rel = target.selector
        # Find the hop for this target's file path
        if scope_rel in file_to_hop:
            hop = file_to_hop[scope_rel]
        else:
            # Target didn't match directly — check prefix matching
            hop = 0  # default to hop 0 (always run)
            for fpath, fhop in file_to_hop.items():
                if fpath == scope_rel or fpath.startswith(scope_rel + "/"):
                    hop = fhop
                    break
        hop_targets.setdefault(hop, []).append(target)
    return hop_targets

def _partition_for_batching(
    targets: list[Any],
) -> tuple[list[list[Any]], list[Any]]:
    """Split targets into batchable groups and solo targets.
    Batchable: multiple targets that share the same (runner_pack_id,
    workspace_root) and all have estimated_cost <= _BATCH_COST_THRESHOLD.
    Solo: targets with higher estimated cost, or unique runner/workspace
    combinations.
    Returns (batch_groups, solo_targets).
    """
    from collections import defaultdict
    # Group by (runner_pack_id, workspace_root)
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for t in targets:
        key = (t.runner_pack_id, t.workspace_root)
        groups[key].append(t)
    batch_groups: list[list[Any]] = []
    solo_targets: list[Any] = []
    for _key, group in groups.items():
        # Separate low-cost and high-cost targets
        low_cost = [t for t in group if t.estimated_cost <= _BATCH_COST_THRESHOLD]
        high_cost = [t for t in group if t.estimated_cost > _BATCH_COST_THRESHOLD]
        solo_targets.extend(high_cost)
        if len(low_cost) >= 2:
            # Worth batching: 2+ targets save subprocess overhead
            batch_groups.append(low_cost)
        else:
            # Single target — no point batching
            solo_targets.extend(low_cost)
    return batch_groups, solo_targets

async def _run_tiered_tests(
    *,
    app_ctx: "AppContext",
    progress: ProgressSink,
    graph_result: ImportGraphResult,
    filtered_targets: list[TestTarget],
    repo_root: Path,
    test_filter: str | None,
    coverage: bool,
    coverage_dir: str | None,
    coverage_filter_paths: set[str] | None,
    max_test_hops: int,
    phase: int,
    total_phases: int,
) -> dict[str, Any]:
    """Execute tests in hop tiers: direct tests first, then transitive.
    If direct tests (hop 0) fail, transitive tests (hop 1+) are skipped
    on the assumption that direct-import failures will cascade.
    Within each tier, low-cost targets are batched into fewer subprocess
    invocations to reduce startup overhead.
    Args:
        coverage_filter_paths: Source files to include in coverage report
            (changed files excluding tests).
        max_test_hops: Maximum hop tier to execute.  0 = direct only,
            1 = direct + first transitive, etc.  Use a large number
            (e.g. 99) for unlimited.
    Returns a dict with keys: serialized, status, passed, failed, tier_log.
    """
    hop_targets = _assign_target_hops(filtered_targets, graph_result, repo_root)
    sorted_hops = sorted(hop_targets.keys())
    max_hop = sorted_hops[-1] if sorted_hops else 0
    # Limit hops to the requested depth
    executable_hops = [h for h in sorted_hops if h <= max_test_hops]
    skipped_hops = [h for h in sorted_hops if h > max_test_hops]
    # Accumulate results across tiers
    total_passed = 0
    total_failed = 0
    all_test_results: list[Any] = []
    all_batch_results: list[Any] = []
    all_batch_coverage: list[Any] = []  # Coverage artifacts from batched runs
    all_failure_lines: list[str] = []  # Failure details across all hops
    tier_log: list[dict[str, Any]] = []
    final_status = "completed"
    stopped_at_hop: int | None = None
    for hop in executable_hops:
        targets_this_hop = hop_targets[hop]
        target_count = len(targets_this_hop)
        tier_label = "direct" if hop == 0 else f"hop {hop}"
        await progress.report_progress(
            phase,
            total_phases,
            f"Running {target_count} {tier_label} test target(s)",
        )
        # Partition into batches and solo targets
        batch_groups, solo_targets = _partition_for_batching(targets_this_hop)
        batch_count = sum(len(g) for g in batch_groups)
        batched_into = len(batch_groups)
        # Build effective target list: solo targets run individually
        effective_target_ids = [t.target_id for t in solo_targets]
        # Run solo targets via normal test_ops.run
        solo_result = None
        if effective_target_ids:
            solo_result = await app_ctx.test_ops.run(
                targets=effective_target_ids,
                target_filter=None,
                test_filter=test_filter,
                tags=None,
                failed_only=False,
                parallelism=None,
                timeout_sec=None,
                fail_fast=False,
                coverage=coverage,
                coverage_dir=coverage_dir,
            )
        # Run batched targets
        import asyncio
        import uuid
        hop_batch_results: list[tuple[Any, Any]] = []
        if batch_groups:
            # coverage_dir is passed through to batch execution
            cov_dir_path = Path(coverage_dir) if coverage_dir else None
            async def run_batch(
                group: list[Any], cov_path: Path | None = cov_dir_path
            ) -> tuple[Any, Any]:
                artifact_dir = (
                    app_ctx.repo_root / ".recon" / "artifacts" / "tests" / uuid.uuid4().hex[:8]
                )
                artifact_dir.mkdir(parents=True, exist_ok=True)
                return await app_ctx.test_ops._run_batch_targets(
                    targets=group,
                    artifact_dir=artifact_dir,
                    test_filter=test_filter,
                    tags=None,
                    timeout_sec=300,
                    coverage_dir=cov_path,
                )
            batch_tasks = [asyncio.create_task(run_batch(g)) for g in batch_groups]
            hop_batch_results = await asyncio.gather(*batch_tasks)
            # Separate results and coverage artifacts
            for result, cov_artifact in hop_batch_results:
                all_batch_results.append(result)
                if cov_artifact:
                    all_batch_coverage.append(cov_artifact)
        # Collect per-hop batch results for tier counting (avoid cross-hop bleed)
        hop_batch_parsed: list[Any] = (
            [result for result, _ in hop_batch_results] if batch_groups else []
        )
        # Aggregate results for this tier
        tier_passed = 0
        tier_failed = 0
        tier_total = 0
        tier_duration = 0.0
        if solo_result and solo_result.run_status:
            rs = solo_result.run_status
            if rs.progress:
                tier_passed += rs.progress.cases.passed
                tier_failed += rs.progress.cases.failed
                tier_total += rs.progress.cases.total
            tier_duration += rs.duration_seconds
            all_test_results.append(solo_result)
            # Collect failure details from solo result
            if rs.failures:
                for f in rs.failures:
                    loc = f"{f.path}:{f.line}" if f.line else f.path
                    all_failure_lines.append(f"{loc} {f.name}: {f.message}")
                    if f.traceback:
                        tb_lines = [ln for ln in f.traceback.strip().splitlines() if ln.strip()][:3]
                        all_failure_lines.extend(f"  {ln.strip()}" for ln in tb_lines)
        for br in hop_batch_parsed:
            tier_passed += br.passed
            tier_failed += br.failed
            tier_total += br.total
            tier_duration += br.duration_seconds
            # Collect failure details from batch results
            if br.tests:
                for tc in br.tests:
                    if tc.status in ("failed", "error"):
                        loc = (
                            f"{tc.file_path}:{tc.line_number}"
                            if tc.file_path and tc.line_number
                            else tc.file_path or tc.classname or "unknown"
                        )
                        msg = tc.message or "no message"
                        all_failure_lines.append(f"{loc} {tc.name}: {msg}")
                        if tc.traceback:
                            tb_lines = [
                                ln for ln in tc.traceback.strip().splitlines() if ln.strip()
                            ][:3]
                            all_failure_lines.extend(f"  {ln.strip()}" for ln in tb_lines)
        total_passed += tier_passed
        total_failed += tier_failed
        tier_entry: dict[str, Any] = {
            "hop": hop,
            "label": tier_label,
            "targets": target_count,
            "batched": batch_count,
            "batch_groups": batched_into,
            "passed": tier_passed,
            "failed": tier_failed,
            "total": tier_total,
            "duration_seconds": round(tier_duration, 2),
        }
        tier_log.append(tier_entry)
        # Tiered fail-fast: if this hop has failures, skip remaining hops
        if tier_failed > 0 and hop < max_hop:
            remaining_hops = [h for h in executable_hops if h > hop] + skipped_hops
            stopped_at_hop = hop
            skipped_info = ", ".join(
                f"hop {h} ({len(hop_targets[h])} targets)" for h in remaining_hops
            )
            tier_entry["stopped_reason"] = (
                f"Failures in {tier_label} — skipped transitive tiers: {skipped_info}"
            )
            break
    # Build combined serialized result
    combined: dict[str, Any] = {}
    if all_test_results and all_test_results[0].run_status:
        # Merge batch coverage artifacts into the solo result's coverage
        if all_batch_coverage:
            existing_cov = all_test_results[0].run_status.coverage or []
            for cov in all_batch_coverage:
                existing_cov.append(
                    {"format": cov.format, "path": str(cov.path), "pack_id": cov.pack_id}
                )
            all_test_results[0].run_status.coverage = existing_cov
        combined = _serialize_test_result(
            all_test_results[0],
            coverage_filter_paths=coverage_filter_paths,
        )
        # Overlay batch results into flat counters
        for br in all_batch_results:
            combined["passed"] = combined.get("passed", 0) + br.passed
            combined["failed"] = combined.get("failed", 0) + br.failed
            combined["skipped"] = combined.get("skipped", 0) + br.skipped
    elif all_batch_results:
        # Only batched targets, no solo - build coverage from batch artifacts
        combined = {
            "status": "completed",
            "passed": sum(br.passed for br in all_batch_results),
            "failed": sum(br.failed for br in all_batch_results),
            "skipped": sum(br.skipped for br in all_batch_results),
        }
        # Add coverage from batch runs
        if all_batch_coverage:
            batch_cov_dicts = [
                {"format": c.format, "path": str(c.path), "pack_id": c.pack_id}
                for c in all_batch_coverage
            ]
            inline_cov, cov_hint = _build_coverage_text(
                batch_cov_dicts,
                filter_paths=coverage_filter_paths,
            )
            combined["coverage"] = inline_cov
            if cov_hint:
                combined["coverage_hint"] = cov_hint
    # Overlay accumulated failure details from ALL hops and batch results
    if all_failure_lines:
        combined["failures"] = "\n".join(all_failure_lines)
    # Compact tier execution as text string
    tier_parts: list[str] = []
    for entry in tier_log:
        label = entry["label"]
        t_count = entry["targets"]
        p = entry["passed"]
        f = entry["failed"]
        dur = entry["duration_seconds"]
        tier_parts.append(f"{label}: {t_count}t {p}p/{f}f {dur}s")
        if entry.get("stopped_reason"):
            # Append skip info
            tier_parts.append("→ STOPPED")
    combined["tiers"] = " | ".join(tier_parts)
    # Log skipped hops due to hop limit
    if skipped_hops and stopped_at_hop is None:
        skipped_targets = sum(len(hop_targets[h]) for h in skipped_hops)
        skipped_info = ", ".join(f"hop {h} ({len(hop_targets[h])} targets)" for h in skipped_hops)
        tier_log.append(
            {
                "hop": skipped_hops[0],
                "label": "skipped (hop limit)",
                "targets": skipped_targets,
                "batched": 0,
                "batch_groups": 0,
                "passed": 0,
                "failed": 0,
                "total": 0,
                "duration_seconds": 0.0,
                "stopped_reason": f"max_test_hops={max_test_hops} \u2014 skipped: {skipped_info}",
            }
        )
    # Build transparent summary
    tier_log_idx = next(
        (i for i, t in enumerate(tier_log) if t["hop"] == stopped_at_hop),
        None,
    )
    if stopped_at_hop is not None and tier_log_idx is not None:
        combined["summary"] = (
            f"{total_passed} passed, {total_failed} failed "
            f"(stopped at {tier_log[tier_log_idx]['label']}, "
            f"transitive tiers skipped)"
        )
    else:
        total_duration = sum(t["duration_seconds"] for t in tier_log)
        hop_note = f", {len(executable_hops)} tier(s)" if executable_hops else ""
        skip_note = f", {len(skipped_hops)} skipped" if skipped_hops else ""
        combined["summary"] = (
            f"{total_passed} passed"
            + (f", {total_failed} failed" if total_failed > 0 else "")
            + f" ({total_duration:.1f}s{hop_note}{skip_note})"
        )
    # Collect failed test IDs for coverage ingestion
    _failed_test_ids: set[str] = set()
    for _tr in all_test_results:
        if _tr.run_status and _tr.run_status.failures:
            _failed_test_ids.update(
                f"{f.path}::{f.name}"
                for f in _tr.run_status.failures
            )
    return {
        "serialized": combined,
        "status": final_status,
        "passed": total_passed,
        "failed": total_failed,
        "tier_log": tier_log,
        "failed_test_ids": _failed_test_ids or None,
    }

# Core Pipeline (transport-agnostic)

class _NullProgress:
    """No-op progress sink for callers that don't need progress."""
    async def report_progress(self, current: int, total: int, message: str) -> None:
        """No-op: progress discarded when no listener is attached."""
        return None
    async def info(self, message: str) -> None:
        """No-op: info message discarded when no listener is attached."""
        return None
    async def warning(self, message: str) -> None:
        """No-op: warning discarded when no listener is attached."""
        return None

_DEFAULT_MAX_TEST_HOPS = 0
_COMMIT_MAX_TEST_HOPS = 2

