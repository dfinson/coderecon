"""Checkpoint helper functions: test debt, commit validation, result handling."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from coderecon.git._internal.hooks import run_hook
from coderecon.git.errors import EmptyCommitMessageError, PathsNotFoundError

if TYPE_CHECKING:
    from coderecon.testing.models import TestResult, TestTarget

log = structlog.get_logger(__name__)

def _detect_test_debt(
    changed_files: list[str],
    repo_root: Path,
) -> dict[str, Any] | None:
    """Detect source files changed without corresponding test updates.
    Scans ``changed_files`` for source files, uses convention-based
    pairing to find their expected test counterparts, and reports any
    that exist on disk but were NOT included in ``changed_files``.
    Returns a dict with ``source_files``, ``missing_test_updates``, and
    ``hint`` if debt is found, otherwise ``None``.
    """
    from coderecon.core.languages import find_test_pairs, is_test_file
    changed_set = set(changed_files)
    # Separate source files from test files
    source_files = [f for f in changed_files if not is_test_file(f)]
    test_files_changed = [f for f in changed_files if is_test_file(f)]
    if not source_files:
        return None
    missing: list[dict[str, str]] = []
    for src in source_files:
        pairs = find_test_pairs(src)
        # Find test files that exist on disk but weren't in changed_files
        for test_path in pairs:
            if test_path in changed_set:
                break  # At least one test counterpart was updated — no debt
        else:
            # No test counterpart was in changed_files — check if any exist
            existing_tests = [test_path for test_path in pairs if (repo_root / test_path).exists()]
            if existing_tests:
                missing.append(
                    {
                        "source": src,
                        "test_file": existing_tests[0],
                    }
                )
    if not missing:
        return None
    sources_str = ", ".join(m["source"].split("/")[-1] for m in missing[:5])
    tests_str = ", ".join(m["test_file"] for m in missing[:5])
    truncated = " (and more)" if len(missing) > 5 else ""
    hint = (
        f"TEST DEBT: {len(missing)} source file(s) changed without test updates"
        f"{truncated}. Source: {sources_str}. "
        f"Test counterpart(s): {tests_str}. "
        "Consider updating tests to cover your changes."
    )
    return {
        "source_files_changed": len(source_files),
        "test_files_changed": len(test_files_changed),
        "missing_test_updates": [
            {"source": m["source"], "test_file": m["test_file"]} for m in missing
        ],
        "hint": hint,
    }

# Commit Helpers

def _validate_commit_message(message: str) -> None:
    """Validate commit message is not empty or whitespace-only."""
    if not message or not message.strip():
        raise EmptyCommitMessageError()

def _validate_paths_exist(
    repo_path: Path, paths: list[str], *, tracked_files: set[str] | None = None
) -> None:
    """Validate paths are known to the repository.
    A path is valid if it exists on disk OR is tracked by git (i.e. a deletion).
    Only truly unknown paths (typos, never-existed) raise PathsNotFoundError.
    """
    if not paths:
        return
    tracked = tracked_files or set()
    unknown: list[str] = []
    for p in paths:
        if (repo_path / p).exists():
            continue  # exists on disk — valid
        if p in tracked:
            continue  # tracked deletion — valid
        unknown.append(p)
    if unknown:
        raise PathsNotFoundError(unknown)

def _run_hook_with_retry(
    repo_path: Path,
    paths_to_restage: list[str],
    stage_fn: Callable[[list[str]], None],
) -> tuple[Any, dict[str, Any] | None]:
    """Run pre-commit hooks with auto-fix retry logic.
    Returns:
        Tuple of (hook_result, failure_response).
        If failure_response is None, hooks passed and commit can proceed.
    """
    hook_result = run_hook(repo_path, "pre-commit")
    if hook_result.success:
        return hook_result, None
    auto_fixed = hook_result.modified_files or []
    if not auto_fixed:
        return hook_result, {
            "hook_failure": {
                "code": "HOOK_FAILED",
                "hook_type": "pre-commit",
                "exit_code": hook_result.exit_code,
                "stdout": hook_result.stdout,
                "stderr": hook_result.stderr,
                "modified_files": [],
            },
            "summary": f"pre-commit hook failed (exit {hook_result.exit_code})",
            "agentic_hint": (
                "Hook failed with errors that require manual fixing. "
                "Review the output above and fix the reported issues, then retry."
            ),
        }
    # Hook auto-fixed files — re-stage and retry
    restage_paths = list(set(auto_fixed + paths_to_restage))
    stage_fn(restage_paths)
    retry_result = run_hook(repo_path, "pre-commit")
    if not retry_result.success:
        return hook_result, {
            "hook_failure": {
                "code": "HOOK_FAILED_AFTER_RETRY",
                "hook_type": "pre-commit",
                "exit_code": retry_result.exit_code,
                "attempts": [
                    {
                        "attempt": 1,
                        "exit_code": hook_result.exit_code,
                        "stdout": hook_result.stdout,
                        "stderr": hook_result.stderr,
                        "auto_fixed_files": auto_fixed,
                    },
                    {
                        "attempt": 2,
                        "exit_code": retry_result.exit_code,
                        "stdout": retry_result.stdout,
                        "stderr": retry_result.stderr,
                        "auto_fixed_files": retry_result.modified_files or [],
                    },
                ],
            },
            "summary": "pre-commit hook failed after auto-fix retry",
            "agentic_hint": (
                "Hook auto-fixed files on the first attempt but still failed on retry. "
                "This requires manual fixing."
            ),
        }
    return hook_result, None

def _summarize_commit(sha: str, message: str) -> str:
    from coderecon.core.formatting import truncate_at_word
    short_sha = sha[:7]
    first_line = message.split("\n")[0]
    truncated = truncate_at_word(first_line, 45)
    return f'{short_sha} "{truncated}"'

# Target Matching

def _normalize_selector(selector: str) -> str:
    """Normalize target selector for path matching.
    Handles Go package selectors (./path), wildcard selectors (./...),
    and project root selectors (.).
    """
    if selector in (".", "./..."):
        return ""
    if selector.startswith("./"):
        return selector[2:]
    return selector

def _target_matches_affected_files(
    target: TestTarget,
    affected_paths: set[str],
    repo_root: Path,
) -> bool:
    """Check if a test target's scope contains any affected test file.
    For 'file' targets (e.g., Python pytest), this is an exact path match.
    For 'package' targets (e.g., Go packages), checks if any affected file
    is within the package directory.
    For 'project' targets (e.g., Maven modules, Gradle), checks if any affected
    file is within the project root scope.
    """
    ws = Path(target.workspace_root)
    sel = _normalize_selector(target.selector)
    scope_abs = ws / sel if sel else ws
    try:
        scope_rel = str(scope_abs.relative_to(repo_root))
    except ValueError:
        # Target workspace outside repo root, fall back to exact selector match
        return target.selector in affected_paths
    if scope_rel == ".":
        # Scope is the entire repo — all files match
        return bool(affected_paths)
    return any(p == scope_rel or p.startswith(scope_rel + "/") for p in affected_paths)

# Test Result Helpers

def _summarize_run(result: "TestResult") -> str:
    """Generate compact summary for a test run."""
    if not result.run_status:
        return "no run status"
    status = result.run_status
    if status.progress:
        p = status.progress
        if status.status == "completed":
            if p.cases.failed > 0:
                return (
                    f"{p.cases.passed} passed, {p.cases.failed} failed "
                    f"({status.duration_seconds:.1f}s)"
                )
            return f"{p.cases.passed} passed ({status.duration_seconds:.1f}s)"
        elif status.status == "running":
            parts = [f"{p.cases.passed} passed"]
            if p.cases.failed:
                parts.append(f"{p.cases.failed} failed")
            return f"running: {p.targets.completed}/{p.targets.total} targets ({', '.join(parts)})"
        elif status.status == "cancelled":
            return "cancelled"
        elif status.status == "failed":
            return "run failed"
        # Other statuses
        status_parts: list[str] = [status.status]
        if p.cases.total > 0:
            status_parts.append(f"{p.cases.passed}/{p.cases.total} passed")
            if p.cases.failed:
                status_parts.append(f"{p.cases.failed} failed")
        return ", ".join(status_parts)
    return status.status

def _build_coverage_text(
    coverage_artifacts: list[dict[str, str]],
    filter_paths: set[str] | None = None,
) -> tuple[str, str | None]:
    """Build tiered inline coverage summary.
    Returns:
        (inline_summary, coverage_hint) where:
        - inline_summary: tiered multi-line coverage text
        - coverage_hint: always None (sidecar cache eliminated)
    """
    from coderecon.testing.coverage import (
        CoverageParseError,
        CoverageReport,
        build_tiered_coverage,
        merge,
        parse_artifact,
    )
    if not coverage_artifacts:
        return "coverage: no data", None
    # Dedupe by path
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for c in coverage_artifacts:
        p = c.get("path", "")
        if p and p not in seen:
            seen.add(p)
            deduped.append(c)
    # Parse all
    reports: list[CoverageReport] = []
    for cov in deduped:
        path_str = cov.get("path", "")
        fmt = cov.get("format")
        if not path_str:
            continue
        try:
            report = parse_artifact(
                Path(path_str),
                format_id=fmt if fmt and fmt != "unknown" else None,
            )
            reports.append(report)
        except CoverageParseError:
            log.debug("coverage_parse_failed", exc_info=True)
        except OSError as e:
            log.debug("Coverage parse failed", path=path_str, error=str(e), exc_info=True)
    if not reports:
        return "coverage: parse failed", None
    merged = merge(*reports) if len(reports) > 1 else reports[0]
    inline = build_tiered_coverage(merged, filter_paths=filter_paths)
    return inline, None

# Failure-focused snippet extraction

_SNIPPET_CONTEXT_LINES = 15  # lines of context above/below each failure point

def _extract_traceback_locations(traceback: str | None) -> list[tuple[str, int]]:
    """Extract (file_path, line_number) pairs from a pytest traceback string.
    Looks for patterns like ``path/to/file.py:123: in function_name``.
    Returns all unique (path, line) tuples found.
    """
    if not traceback:
        return []
    import re
    locations: list[tuple[str, int]] = []
    for m in re.finditer(r"(\S+\.py):(\d+):", traceback):
        path, line = m.group(1), int(m.group(2))
        locations.append((path, line))
    return locations

def _build_failure_snippets(
    failure_list: list[dict[str, Any]],
    file_contents: dict[str, str],
    context_lines: int = _SNIPPET_CONTEXT_LINES,
) -> dict[str, str]:
    """Build focused code snippets around failure locations.
    For each file referenced in failures, extracts the lines around each
    failure point (± context_lines).  Adjacent/overlapping ranges are merged.
    Returns a dict of ``{path: rendered_snippet_text}``.
    Each snippet includes line numbers and ``>`` markers at failure lines,
    so the agent sees exactly where the problem is.
    """
    # Collect all (path, line) locations from failures
    locations_by_file: dict[str, list[int]] = {}
    for f in failure_list:
        path = f.get("path", "")
        line = f.get("line")
        if path and line and path in file_contents:
            locations_by_file.setdefault(path, []).append(line)
        # Also extract locations from traceback
        for tb_path, tb_line in _extract_traceback_locations(f.get("traceback")):
            if tb_path in file_contents:
                locations_by_file.setdefault(tb_path, []).append(tb_line)
    snippets: dict[str, str] = {}
    for path, lines_list in locations_by_file.items():
        content = file_contents[path]
        file_lines = content.splitlines()
        total = len(file_lines)
        failure_lines_set = set(lines_list)
        # Build merged ranges
        ranges: list[tuple[int, int]] = []
        for line_no in sorted(set(lines_list)):
            start = max(1, line_no - context_lines)
            end = min(total, line_no + context_lines)
            if ranges and start <= ranges[-1][1] + 2:
                # Merge with previous range (allow 1-line gap)
                ranges[-1] = (ranges[-1][0], end)
            else:
                ranges.append((start, end))
        # Render snippet with line numbers and failure markers
        parts: list[str] = []
        for i, (start, end) in enumerate(ranges):
            if i > 0:
                parts.append("  ...")
            for ln in range(start, end + 1):
                marker = ">" if ln in failure_lines_set else " "
                line_text = file_lines[ln - 1] if ln <= total else ""
                parts.append(f"{ln:4d} {marker}| {line_text}")
        snippets[path] = "\n".join(parts)
    return snippets

def _serialize_test_result(
    result: "TestResult",
    coverage_filter_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Convert TestResult to compact serializable dict.
    Outer structure is flat JSON for parseability.  Inner details
    (failures, diagnostics) are compressed text strings to minimise
    token count.
    Args:
        result: TestResult to serialize.
        coverage_filter_paths: If provided, only include coverage for these
            source files (excluding test files).
    """
    output: dict[str, Any] = {
        "summary": _summarize_run(result),
    }
    if result.run_status:
        status = result.run_status
        output["status"] = status.status
        output["duration"] = round(status.duration_seconds, 2)
        if status.progress:
            p = status.progress
            output["passed"] = p.cases.passed
            output["failed"] = p.cases.failed
            output["skipped"] = p.cases.skipped
            output["targets"] = p.targets.total
        if status.failures:
            lines: list[str] = []
            for f in status.failures:
                loc = f"{f.path}:{f.line}" if f.line else f.path
                lines.append(f"{loc} {f.name}: {f.message}")
                if f.traceback:
                    # First 3 meaningful lines of traceback
                    tb_lines = [ln for ln in f.traceback.strip().splitlines() if ln.strip()][:3]
                    lines.extend(f"  {ln.strip()}" for ln in tb_lines)
            output["failures"] = "\n".join(lines)
            # Structured failure list for sidecar rendering — each failure
            # becomes its own cache entry so agents can read one at a time
            output["failure_list"] = [
                {
                    "name": f.name,
                    "path": f.path,
                    "line": f.line,
                    "message": f.message,
                    "traceback": (f.traceback.strip() if f.traceback else None),
                }
                for f in status.failures
            ]
        if status.diagnostics:
            diag_lines: list[str] = []
            for d in status.diagnostics:
                exit_info = f" (exit {d.exit_code})" if d.exit_code is not None else ""
                detail = d.error_detail or "no detail"
                diag_lines.append(f"{d.target_id} [{d.error_type}]: {detail}{exit_info}")
                if d.suggested_action:
                    diag_lines.append(f"  → {d.suggested_action}")
            output["diagnostics"] = "\n".join(diag_lines)
        if status.coverage:
            inline_cov, cov_hint = _build_coverage_text(
                status.coverage,
                filter_paths=coverage_filter_paths,
            )
            output["coverage"] = inline_cov
            if cov_hint:
                output["coverage_hint"] = cov_hint
    if isinstance(result.agentic_hint, str) and result.agentic_hint:
        output["agentic_hint"] = result.agentic_hint
    return output

# Coverage Persistence

def _ingest_checkpoint_coverage(
    app_ctx: "AppContext",
    coverage_dir: Path,
    failed_test_ids: set[str] | None = None,
) -> None:
    """Persist coverage artifacts from a checkpoint test run to TestCoverageFact.
    Best-effort: logs and continues on any error.
    """
    try:
        from coderecon.testing.coverage import (
            CoverageParseError,
            merge,
            parse_artifact,
        )
        if not coverage_dir.is_dir():
            return
        # Find all coverage artifact files in the directory
        artifact_files = list(coverage_dir.rglob("*"))
        if not artifact_files:
            return
        reports = []
        for f in artifact_files:
            if not f.is_file():
                continue
            try:
                report = parse_artifact(f, base_path=app_ctx.repo_root)
                reports.append(report)
            except (CoverageParseError, OSError):  # best-effort artifact parse
                log.debug("artifact_parse_failed", path=str(f), exc_info=True)
        if not reports:
            return
        from coderecon.index._internal.analysis.coverage_ingestion import (
            ingest_coverage,
        )
        merged = merge(*reports) if len(reports) > 1 else reports[0]
        engine = app_ctx.coordinator.db.engine
        epoch = app_ctx.coordinator.current_epoch
        written = ingest_coverage(
            engine, merged, epoch, failed_test_ids=failed_test_ids,
        )
        if written:
            log.debug("checkpoint.coverage_ingested", facts=written)
    except (OSError, CoverageParseError, ImportError, RuntimeError, ValueError):
        log.debug("checkpoint.coverage_ingest_failed", exc_info=True)

# Verify Summary

