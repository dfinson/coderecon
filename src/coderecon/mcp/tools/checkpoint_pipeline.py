"""Core checkpoint pipeline orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from coderecon.git.errors import GitError

if TYPE_CHECKING:
    from coderecon.mcp.context import AppContext
    from coderecon.mcp.session import SessionState
from coderecon.mcp.tools.checkpoint import (
    _COMMIT_MAX_TEST_HOPS,
    _DEFAULT_MAX_TEST_HOPS,
    ProgressSink,
    _NullProgress,
)
from coderecon.mcp.tools.checkpoint_helpers import (
    _detect_test_debt,
    _enrich_failure_result,
    _ingest_checkpoint_coverage,
    _run_hook_with_retry,
    _summarize_commit,
    _target_matches_affected_files,
    _validate_commit_message,
    _validate_paths_exist,
)
from coderecon.mcp.tools.checkpoint_tiered import _run_tiered_tests, _summarize_verify

log = structlog.get_logger(__name__)
async def checkpoint_pipeline(
    app_ctx: AppContext,
    session: SessionState,
    *,
    changed_files: list[str],
    lint: bool = True,
    autofix: bool = True,
    tests: bool = True,
    test_filter: str | None = None,
    max_test_hops: int | None = None,
    commit_message: str | None = None,
    push: bool = False,
    progress: ProgressSink | None = None,
) -> dict[str, Any]:
    """Core checkpoint logic — lint, test, commit, push.
    Decoupled from MCP: accepts an ``AppContext``, a ``SessionState``,
    and an optional ``ProgressSink``.  Both the MCP ``@mcp.tool`` wrapper
    and the stdio dispatch layer call this function.
    """
    if progress is None:
        progress = _NullProgress()
    # ── Read-only checkpoint: clean-tree verification only ──
    if getattr(session, "read_only", None) is True:
        return _readonly_checkpoint(app_ctx, session)
    # Compute total phases for progress reporting
    total_phases = int(lint) + int(tests) * 3  # tests = discover + filter + run
    phase = 0
    result: dict[str, Any] = {"action": "checkpoint", "changed_files": changed_files}
    lint_status = "skipped"
    lint_diagnostics = 0
    test_passed = 0
    test_failed = 0
    test_status = "skipped"
    if lint:
        lint_out = await _run_lint_phase(
            app_ctx, changed_files, autofix, phase, total_phases, progress, result,
        )
        lint_status = lint_out["lint_status"]
        lint_diagnostics = lint_out["lint_diagnostics"]
        phase = lint_out["phase"]
        if lint_out["skip_tests"]:
            test_status = "skipped"
            tests = False
    if tests:
        test_out = await _run_test_phase(
            app_ctx, changed_files, test_filter, max_test_hops, commit_message,
            phase, total_phases, progress, result,
        )
        test_status = test_out["test_status"]
        test_passed = test_out["test_passed"]
        test_failed = test_out["test_failed"]
    result["summary"] = _summarize_verify(
        lint_status, lint_diagnostics, test_passed, test_failed, test_status
    )
    has_lint_issues = lint_diagnostics > 0 and lint_status != "clean"
    has_test_failures = test_failed > 0
    has_test_error = test_status == "error"
    if has_lint_issues or has_test_failures or has_test_error:
        result["passed"] = False
        await progress.report_progress(total_phases, total_phases, "Checkpoint FAILED")
        hints: list[str] = []
        if has_lint_issues:
            hints.append(f"Fix {lint_diagnostics} lint issues.")
        if has_test_failures:
            hints.append(f"Fix {test_failed} failing test(s).")
        if has_test_error:
            hints.append("Test phase errored — check tests section for details.")
        # Add tier execution transparency
        tiers_text = (
            result.get("tests", {}).get("tiers", "")
            if isinstance(result.get("tests"), dict)
            else ""
        )
        if tiers_text:
            hints.append(f"Tiers: {tiers_text}")
        hints.append(
            "STOP! You passed changed_files — lint and tests ran ONLY on "
            "code affected by YOUR changes. These failures are almost "
            "certainly YOUR fault. Do NOT dismiss them as 'pre-existing' "
            "or 'not related to my changes'. Errors in OTHER files are "
            "often transitive — caused by your changes breaking downstream "
            "dependents. These are still YOUR responsibility. "
            "Fix ALL issues before proceeding."
        )
        # ── Build failure-focused enrichment ──
        _enrich_failure_result(app_ctx, session, result, changed_files)
        result["agentic_hint"] = " ".join(hints)
    else:
        result["passed"] = True
        await progress.report_progress(total_phases, total_phases, "Checks passed")
        # ── Governance gate evaluation ──
        await _evaluate_governance(app_ctx, result, changed_files, lint_status, lint_diagnostics, progress)
        # ── Reset mutation state ──
        try:
            session.mutation_ctx.clear()
            app_ctx.refactor_ops.clear_pending()
        except (AttributeError, RuntimeError):  # best-effort reset
            log.debug("checkpoint.mutation_reset.failed", exc_info=True)
        if commit_message:
            await _commit_and_push(
                app_ctx, result, changed_files, commit_message, push,
                progress, total_phases,
            )
        else:
            result["agentic_hint"] = (
                'All checks passed. Call checkpoint again with commit_message="..." '
                "to commit your changes."
            )
        # ── Test debt detection ──
        if changed_files:
            try:
                debt = _detect_test_debt(changed_files, app_ctx.repo_root)
                if debt:
                    result["test_debt"] = debt
                    existing = result.get("agentic_hint", "")
                    result["agentic_hint"] = f"{existing}\n\n{debt['hint']}"
            except (ImportError, OSError, ValueError):
                log.debug("test_debt_detection_failed", exc_info=True)
    from coderecon.mcp.delivery import wrap_response
    return wrap_response(
        result,
        resource_kind="checkpoint",
    )


def _readonly_checkpoint(
    app_ctx: AppContext,
    session: SessionState,
) -> dict[str, Any]:
    """Handle read-only checkpoint: verify clean tree and return."""
    try:
        wt_status = app_ctx.git_ops.status()
        dirty_files = [p for p, flags in wt_status.items() if flags != 0]
    except GitError:
        dirty_files = []
    clean = len(dirty_files) == 0
    ro_result: dict[str, Any] = {
        "action": "checkpoint",
        "read_only": True,
        "clean_tree": clean,
        "passed": clean,
    }
    if not clean:
        ro_result["dirty_files"] = dirty_files[:20]
        ro_result["agentic_hint"] = (
            f"Read-only checkpoint found {len(dirty_files)} "
            "uncommitted file(s). This is unexpected for a "
            "read-only session. Investigate or call "
            "recon(read_only=False) to switch to a read-write session."
        )
    else:
        ro_result["agentic_hint"] = (
            "Read-only session complete — working tree is clean. No mutations were made."
        )
    session.mutation_ctx.clear()
    app_ctx.refactor_ops.clear_pending()
    from coderecon.mcp.delivery import wrap_response
    return wrap_response(ro_result, resource_kind="checkpoint")


async def _run_test_phase(
    app_ctx: AppContext,
    changed_files: list[str],
    test_filter: str | None,
    max_test_hops: int | None,
    commit_message: str | None,
    phase: int,
    total_phases: int,
    progress: ProgressSink,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Discover, filter, and run tests. Populates *result*. Returns state dict."""
    test_status = "skipped"
    test_passed = 0
    test_failed = 0
    await progress.report_progress(phase, total_phases, "Discovering test targets")
    discover_result = await app_ctx.test_ops.discover(paths=None)
    all_targets = discover_result.targets or []
    phase += 1
    if all_targets and changed_files:
        await progress.report_progress(
            phase, total_phases,
            f"Filtering {len(all_targets)} targets by import graph",
        )
        graph_result = await app_ctx.coordinator.get_affected_test_targets(changed_files)
        affected_paths = set(graph_result.test_files)
        filtered = [
            t for t in all_targets
            if _target_matches_affected_files(t, affected_paths, app_ctx.repo_root)
        ]
        phase += 1
        if not filtered:
            test_status = "skipped"
            await progress.info("Tests: no affected targets — skipping")
            result["tests"] = {
                "status": "skipped",
                "reason": "no affected tests found",
                "confidence": graph_result.confidence.tier,
            }
        else:
            import uuid

            from coderecon.core.languages import is_test_file
            coverage_dir = str(
                app_ctx.repo_root / ".recon" / "artifacts" / "coverage" / uuid.uuid4().hex[:8]
            )
            Path(coverage_dir).mkdir(parents=True, exist_ok=True)
            coverage_filter_paths = {f for f in changed_files if not is_test_file(f)}
            if max_test_hops is not None:
                effective_hops = max_test_hops
            elif commit_message:
                effective_hops = _COMMIT_MAX_TEST_HOPS
            else:
                effective_hops = _DEFAULT_MAX_TEST_HOPS
            tiered_result = await _run_tiered_tests(
                app_ctx=app_ctx, progress=progress,
                graph_result=graph_result, filtered_targets=filtered,
                repo_root=app_ctx.repo_root, test_filter=test_filter,
                coverage=True, coverage_dir=coverage_dir,
                coverage_filter_paths=coverage_filter_paths,
                max_test_hops=effective_hops,
                phase=phase, total_phases=total_phases,
            )
            result["tests"] = tiered_result["serialized"]
            test_status = tiered_result["status"]
            test_passed = tiered_result["passed"]
            test_failed = tiered_result["failed"]
            _ingest_checkpoint_coverage(
                app_ctx, Path(coverage_dir),
                failed_test_ids=tiered_result.get("failed_test_ids"),
            )
            serialized = tiered_result["serialized"]
            if isinstance(serialized, dict) and serialized.get("coverage_hint"):
                result["coverage_hint"] = serialized.pop("coverage_hint")
            if test_failed > 0:
                await progress.warning(f"Tests: {test_passed} passed, {test_failed} FAILED")
            elif test_passed > 0:
                await progress.info(f"Tests: {test_passed} passed")
    else:
        test_status = "skipped"
        if not all_targets:
            reason = "no test targets discovered"
        else:
            reason = "changed_files is empty — nothing to match against"
        await progress.info(f"Tests: skipped — {reason}")
        result["tests"] = {"status": "skipped", "reason": reason}
    return {
        "test_status": test_status,
        "test_passed": test_passed,
        "test_failed": test_failed,
    }


async def _run_lint_phase(
    app_ctx: AppContext,
    changed_files: list[str],
    autofix: bool,
    phase: int,
    total_phases: int,
    progress: ProgressSink,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Execute lint (cached or live) and populate *result*. Returns state dict."""
    lint_status = "skipped"
    lint_diagnostics = 0
    skip_tests = False
    cached_lint = None
    if not autofix and changed_files:
        try:
            from coderecon.mcp.tools._checkpoint_cache import try_read_lint_facts
            cached_lint = try_read_lint_facts(
                engine=app_ctx.coordinator.db.engine,
                changed_files=changed_files,
                current_epoch=app_ctx.coordinator.get_current_epoch(),
            )
        except (ImportError, OSError, RuntimeError, ValueError):  # noqa: BLE001
            cached_lint = None
    if cached_lint is not None:
        lint_status = "clean" if cached_lint.clean else "dirty"
        lint_diagnostics = cached_lint.total_errors + cached_lint.total_warnings
        phase += 1
        if cached_lint.clean:
            await progress.info("Lint: clean (cached)")
        else:
            await progress.info(f"Lint: {lint_diagnostics} issue(s) (cached)")
        result["lint"] = {
            "status": lint_status,
            "diagnostics": lint_diagnostics,
            "fixed_files": 0,
            "duration": 0.0,
            "cached": True,
        }
        if cached_lint.issues:
            result["lint"]["issues"] = [
                f"{i['file']} {i['tool']} E:{i['errors']} W:{i['warnings']}"
                for i in cached_lint.issues
            ]
        if lint_diagnostics > 0 and lint_status != "clean":
            result["tests"] = {
                "status": "skipped",
                "reason": "lint failed — fix lint issues first",
            }
            skip_tests = True
    else:
        mode = "auto-fix" if autofix else "check-only"
        await progress.report_progress(phase, total_phases, f"Linting ({mode})")
        lint_result = await app_ctx.lint_ops.check(
            paths=changed_files or None,
            tools=None,
            categories=None,
            dry_run=not autofix,
        )
        lint_status = lint_result.status
        lint_diagnostics = lint_result.total_diagnostics
        phase += 1
        if lint_status == "clean":
            await progress.info("Lint: clean")
        else:
            await progress.info(
                f"Lint: {lint_diagnostics} issue(s), "
                f"{lint_result.total_files_modified} file(s) modified"
            )
        issue_lines: list[str] = []
        for t in lint_result.tools_run:
            for d in t.diagnostics:
                rel_path = d.path
                if "/" in rel_path:
                    for prefix in (str(app_ctx.repo_root) + "/",):
                        if rel_path.startswith(prefix):
                            rel_path = rel_path[len(prefix) :]
                            break
                sev = d.severity.value[0].upper()
                issue_lines.append(f"{rel_path}:{d.line}:{d.column} {sev} {d.code} {d.message}")
        result["lint"] = {
            "status": lint_result.status,
            "diagnostics": lint_result.total_diagnostics,
            "fixed_files": lint_result.total_files_modified,
            "duration": round(lint_result.duration_seconds, 2),
        }
        if issue_lines:
            result["lint"]["issues"] = issue_lines
        if lint_result.agentic_hint:
            result["lint"]["agentic_hint"] = lint_result.agentic_hint
        if lint_diagnostics > 0 and lint_status != "clean":
            result["tests"] = {
                "status": "skipped",
                "reason": "lint failed — fix lint issues first",
            }
            skip_tests = True
    return {
        "lint_status": lint_status,
        "lint_diagnostics": lint_diagnostics,
        "phase": phase,
        "skip_tests": skip_tests,
    }


async def _evaluate_governance(
    app_ctx: AppContext,
    result: dict[str, Any],
    changed_files: list[str],
    lint_status: str,
    lint_diagnostics: int,
    progress: ProgressSink,
) -> None:
    """Run governance gate evaluation, mutating *result* in place."""
    gate_hints: list[str] = []
    try:
        from coderecon.config.loader import load_config
        from coderecon.index._internal.analysis.gate_engine import evaluate_gates
        config = load_config(repo_root=app_ctx.repo_root)
        gate_result = evaluate_gates(
            governance=config.governance,
            engine=app_ctx.coordinator.db.engine,
            changed_files=changed_files,
            lint_clean=lint_status == "clean",
            lint_diagnostics=lint_diagnostics,
            test_debt_info=_detect_test_debt(changed_files, app_ctx.repo_root)
            if changed_files
            else None,
        )
        if gate_result.violations:
            result["governance"] = gate_result.to_dict()
            for v in gate_result.errors:
                gate_hints.append(f"[GATE ERROR] {v.rule}: {v.message}")
            for v in gate_result.warnings:
                gate_hints.append(f"[GATE WARNING] {v.rule}: {v.message}")
            if not gate_result.passed:
                result["passed"] = False
                result["agentic_hint"] = " ".join(gate_hints)
                await progress.warning(
                    f"Governance: {len(gate_result.errors)} error(s), "
                    f"{len(gate_result.warnings)} warning(s)"
                )
    except (ImportError, OSError, ValueError, AttributeError):  # best-effort governance
        log.debug("checkpoint.governance.failed", exc_info=True)


async def _commit_and_push(
    app_ctx: AppContext,
    result: dict[str, Any],
    changed_files: list[str],
    commit_message: str,
    push: bool,
    progress: ProgressSink,
    total_phases: int,
) -> None:
    """Validate, stage, hook, commit, optionally push — mutates *result*."""
    _validate_commit_message(commit_message)
    repo_path = Path(app_ctx.git_ops.path)
    await progress.report_progress(total_phases, total_phases + 2, "Staging changes")
    if changed_files:
        tracked = set(app_ctx.git_ops.tracked_files())
        _validate_paths_exist(repo_path, changed_files, tracked_files=tracked)
        app_ctx.git_ops.stage(changed_files)
        staged_paths = list(changed_files)
    else:
        staged_paths = app_ctx.git_ops.stage_all()
    await progress.report_progress(
        total_phases + 1, total_phases + 2, "Running pre-commit hooks"
    )
    _hook_result, failure = _run_hook_with_retry(
        repo_path, staged_paths, app_ctx.git_ops.stage
    )
    if failure:
        await progress.warning("Pre-commit hooks failed — skipping commit")
        result["commit"] = failure
    else:
        sha = app_ctx.git_ops.commit(commit_message)
        commit_result: dict[str, Any] = {
            "oid": sha,
            "summary": _summarize_commit(sha, commit_message),
        }
        if _hook_result and not _hook_result.success:
            fixed = _hook_result.modified_files or []
            commit_result["hook_warning"] = f"HOOK_AUTO_FIXED: {', '.join(fixed)}"
        if push:
            app_ctx.git_ops.push(remote="origin", force=False)
            commit_result["pushed"] = "origin"
            commit_result["summary"] += " → pushed to origin"
        result["commit"] = commit_result
        await progress.report_progress(
            total_phases + 2,
            total_phases + 2,
            f"Committed {sha[:7]}",
        )
        try:
            from coderecon.mcp.tools.diff import _run_git_diff
            from coderecon.mcp.tools.diff_formatting import (
                _result_to_dict,
            )
            diff_result = _run_git_diff(
                app_ctx, base="HEAD~1", target="HEAD", paths=None
            )
            minimal = _result_to_dict(diff_result, verbosity="minimal")
            diff_summary = minimal.get("summary", "")
            changes = minimal.get("structural_changes", [])
            if changes:
                change_lines = [
                    f"{c.get('change', '?')} {c.get('kind', '?')} "
                    f"{c.get('name', '?')} ({c.get('path', '?').split('/')[-1]})"
                    for c in changes[:15]
                ]
                commit_result["diff"] = f"{diff_summary}: " + ", ".join(change_lines)
            elif diff_summary:
                commit_result["diff"] = diff_summary
        except (ImportError, GitError, KeyError, ValueError, OSError, RuntimeError):
            log.debug("post-commit semantic diff skipped", exc_info=True)
    result["agentic_hint"] = (
        "All checks passed and changes committed."
        if "oid" in result.get("commit", {})
        else "All checks passed but commit failed — see commit section."
    )
