"""Test runner — single-target and batch execution with coverage and memory gating."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from coderecon.files.ops import atomic_write_text
from coderecon.testing.emitters import (
    CoverageArtifact,
    CoverageCapability,
    PackRuntime,
    get_emitter,
)
from coderecon.testing.models import ExecutionContext, ParsedTestSuite, TestTarget
from coderecon.testing.ops_workspaces import _classify_result_error
from coderecon.testing.resources import child_rss_mb
from coderecon.testing.runner_pack import runner_registry
from coderecon.testing.runtime import ExecutionContextBuilder, RuntimeExecutionContext
from coderecon.testing.safe_execution import SafeExecutionConfig, SafeExecutionContext

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


async def _get_execution_context(
    coordinator: IndexCoordinatorEngine,
    target: TestTarget,
) -> RuntimeExecutionContext | None:
    """Get pre-indexed execution context for a test target.
    Index-first approach: Runtime is captured at discovery time and stored
    in ContextRuntime table. This provides O(1) lookup instead of re-detecting
    venvs/runtimes for every test execution.
    Falls back to PATH-based resolution only if index lookup fails.
    Returns None if resolution fails, allowing graceful fallback to
    PATH-based execution.
    """
    try:
        workspace_root = Path(target.workspace_root)
        # Query indexed runtime (captured at discovery time)
        indexed_runtime = await coordinator.get_context_runtime(str(workspace_root))
        if indexed_runtime:
            # Build execution context from indexed runtime
            exec_ctx = ExecutionContextBuilder.build(
                context_root=workspace_root,
                runtime=indexed_runtime,
            )
            return exec_ctx
        # Index lookup failed - fall back to PATH-based execution
        # This should rarely happen if indexing is working correctly
        return None
    except (OSError, RuntimeError, ValueError):
        log.debug("runtime_resolution_failed", exc_info=True)
        return None


async def _run_single_target(
    coordinator: IndexCoordinatorEngine,
    target: TestTarget,
    artifact_dir: Path,
    test_filter: str | None,
    tags: list[str] | None,
    timeout_sec: int,
    coverage: bool,
    coverage_dir: Path | None,
    source_dirs: list[str] | None = None,
    subprocess_memory_limit_mb: int | None = None,
) -> tuple[ParsedTestSuite, CoverageArtifact | None, int]:
    """Run a single test target using its runner pack.
    Uses SafeExecutionContext to protect against misconfigurations in
    target repositories (coverage DB corruption, hanging tests, etc.).
    Returns:
        Tuple of (test results, coverage artifact if collected, peak RSS in MB)
    """
    pack_class = runner_registry.get(target.runner_pack_id)
    if not pack_class:
        return (
            _single_error_suite(target, "unknown",
                f"Runner pack not found: {target.runner_pack_id}",
                "Check that the runner pack is registered", [], ""),
            None, 0,
        )
    pack = pack_class()
    # Get pre-indexed execution context (runtime captured at discovery time)
    exec_ctx = await _get_execution_context(coordinator, target)
    # Create output file path
    safe_name = target.target_id.replace("/", "_").replace(":", "_")
    output_path = artifact_dir / f"{safe_name}.xml"
    # Build command with execution context (uses correct Python/Node/etc. if available)
    cmd = pack.build_command(
        target,
        output_path=output_path,
        pattern=test_filter,
        tags=tags,
        exec_ctx=exec_ctx,
    )
    if not cmd:
        return (
            _single_error_suite(target, "unknown",
                "Runner pack returned empty command",
                "Check target configuration", [], ""),
            None, 0,
        )
    # Handle coverage - use pre-indexed capability instead of detecting at runtime
    cov_artifact: CoverageArtifact | None = None
    coverage_available: bool = False
    emitter = get_emitter(target.runner_pack_id) if coverage else None
    if emitter:
        coverage_tools = await coordinator.get_coverage_capability(
            target.workspace_root, target.runner_pack_id
        )
        runtime = PackRuntime(
            workspace_root=Path(target.workspace_root),
            runner_available=True,
            coverage_tools=coverage_tools,
        )
        coverage_available = emitter.capability(runtime) == CoverageCapability.AVAILABLE
    # Create safe execution context — strip_coverage_flags removes existing
    # coverage flags BEFORE we add our own
    safe_ctx = SafeExecutionContext(
        SafeExecutionConfig(
            artifact_dir=artifact_dir,
            workspace_root=Path(target.workspace_root),
            timeout_sec=timeout_sec,
            strip_coverage_flags=coverage_available,
            subprocess_memory_limit_mb=subprocess_memory_limit_mb,
        )
    )
    # Sanitize command, then add our coverage flags
    cmd = safe_ctx.sanitize_command(cmd, target.runner_pack_id)
    if coverage_available and emitter and coverage_dir:
        cmd = emitter.modify_command(cmd, coverage_dir, source_dirs=source_dirs)
        cov_artifact = CoverageArtifact(
            format=emitter.format_id,
            path=emitter.artifact_path(coverage_dir),
            pack_id=target.runner_pack_id,
            invocation_id=target.target_id,
        )
    safe_env = safe_ctx.prepare_environment(target.runner_pack_id)
    # Merge execution context environment overrides
    if exec_ctx:
        runtime_env = exec_ctx.build_env()
        safe_env.update(runtime_env)
    # Verify executable exists
    executable = cmd[0]
    resolved_executable = shutil.which(executable, path=safe_env.get("PATH"))
    if not resolved_executable:
        safe_ctx.cleanup()
        return (
            _single_error_suite(target, "command_not_found",
                f"Executable not found: {executable}",
                f"Install {executable} or activate the correct environment",
                cmd, pack.get_cwd(target)),
            None, 0,
        )
    cwd = pack.get_cwd(target)
    try:
        result, peak_rss_mb = await _exec_and_parse(
            cmd, cwd, safe_env, timeout_sec,
            artifact_dir, safe_name, output_path, pack, target,
        )
        return (result, cov_artifact, peak_rss_mb)
    except TimeoutError:
        safe_ctx.cleanup()
        return (
            _single_error_suite(
                target, "timeout",
                f"Command timed out after {timeout_sec} seconds",
                "Increase timeout or run fewer tests",
                cmd, cwd,
            ),
            None, 0,
        )
    except OSError as e:
        safe_ctx.cleanup()
        return (
            _single_error_suite(
                target, "command_failed",
                f"OS error executing command: {e}",
                "Check that the command and working directory are valid",
                cmd, cwd,
            ),
            None, 0,
        )
    finally:
        safe_ctx.cleanup()


async def _run_batch_targets(
    coordinator: IndexCoordinatorEngine,
    targets: list[TestTarget],
    artifact_dir: Path,
    test_filter: str | None,
    tags: list[str] | None,
    timeout_sec: int,
    coverage_dir: Path | None = None,
) -> tuple[ParsedTestSuite, CoverageArtifact | None]:
    """Run multiple targets in a single subprocess invocation.
    All targets must share the same ``runner_pack_id`` and
    ``workspace_root``.  The runner pack's ``build_batch_command``
    method produces a single command that exercises every target.
    Returns (ParsedTestSuite, CoverageArtifact | None).
    """
    if not targets:
        return ParsedTestSuite(name="batch-empty", total=0), None
    first = targets[0]
    pack_class = runner_registry.get(first.runner_pack_id)
    if not pack_class:
        return ParsedTestSuite(
            name="batch",
            errors=len(targets),
            error_type="unknown",
            error_detail=f"Runner pack not found: {first.runner_pack_id}",
        ), None
    pack = pack_class()
    exec_ctx = await _get_execution_context(coordinator, first)
    # Deterministic batch name for artifact files
    batch_name = "batch_" + "_".join(
        t.target_id.replace("/", "_").replace(":", "_") for t in targets[:5]
    )
    if len(targets) > 5:
        batch_name += f"_plus{len(targets) - 5}"
    output_path = artifact_dir / f"{batch_name}.xml"
    cmd = pack.build_batch_command(
        targets,
        output_path=output_path,
        pattern=test_filter,
        tags=tags,
        exec_ctx=exec_ctx,
    )
    if not cmd:
        # Fallback: pack doesn't support batching
        return ParsedTestSuite(
            name="batch",
            errors=len(targets),
            error_type="unknown",
            error_detail="Runner pack does not support batch execution",
        ), None
    # Coverage handling - always enabled when supported
    cov_artifact, coverage_available, emitter = await _setup_batch_coverage(
        coordinator, first, coverage_dir,
    )
    safe_ctx = SafeExecutionContext(
        SafeExecutionConfig(
            artifact_dir=artifact_dir,
            workspace_root=Path(first.workspace_root),
            timeout_sec=timeout_sec,
            strip_coverage_flags=coverage_available,
        )
    )
    cmd = safe_ctx.sanitize_command(cmd, first.runner_pack_id)
    # Add coverage flags after sanitization
    if coverage_available and emitter and coverage_dir:
        cmd = emitter.modify_command(cmd, coverage_dir, source_dirs=None)
        cov_artifact = CoverageArtifact(
            format=emitter.format_id,
            path=emitter.artifact_path(coverage_dir),
            pack_id=first.runner_pack_id,
            invocation_id=f"batch_{len(targets)}",
        )
    safe_env = safe_ctx.prepare_environment(first.runner_pack_id)
    if exec_ctx:
        runtime_env = exec_ctx.build_env()
        safe_env.update(runtime_env)
    executable = cmd[0]
    resolved_executable = shutil.which(executable, path=safe_env.get("PATH"))
    if not resolved_executable:
        safe_ctx.cleanup()
        selectors = ", ".join(t.selector for t in targets)
        return ParsedTestSuite(
            name="batch",
            errors=len(targets),
            error_type="command_not_found",
            error_detail=f"Executable not found: {executable}",
            suggested_action=f"Install {executable} or activate the correct environment",
            execution=ExecutionContext(command=cmd),
            target_selector=selectors,
            workspace_root=first.workspace_root,
        ), None
    cwd = pack.get_cwd(first)
    stdout = ""
    stderr = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=safe_env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")
        exit_code = proc.returncode
        # Write artifacts
        stdout_path = artifact_dir / f"{batch_name}.stdout.txt"
        atomic_write_text(stdout_path, stdout)
        if stderr:
            stderr_path = artifact_dir / f"{batch_name}.stderr.txt"
            atomic_write_text(stderr_path, stderr)
        execution = ExecutionContext(
            command=cmd,
            working_directory=str(cwd),
            exit_code=exit_code,
            raw_stdout=stdout,
            raw_stderr=stderr if stderr else None,
        )
        result = pack.parse_output(output_path, stdout)
        selectors = ", ".join(t.selector for t in targets)
        result.target_selector = selectors
        result.workspace_root = first.workspace_root
        result.execution = execution
        if result.tests is not None:
            result.parsed_test_count = len(result.tests)
        _classify_result_error(result, output_path, stdout, exit_code)
        return result, cov_artifact
    except TimeoutError:
        safe_ctx.cleanup()
        return _batch_error_suite(
            targets, "timeout",
            f"Batch command timed out after {timeout_sec} seconds",
            cmd, cwd, stdout, stderr,
        ), None
    except OSError as e:
        safe_ctx.cleanup()
        return _batch_error_suite(
            targets, "command_failed",
            f"OS error executing batch command: {e}",
            cmd, cwd,
        ), None
    finally:
        safe_ctx.cleanup()


async def _setup_batch_coverage(
    coordinator: "IndexCoordinatorEngine",
    first: TestTarget,
    coverage_dir: Path | None,
) -> tuple[CoverageArtifact | None, bool, object | None]:
    """Resolve coverage emitter/capability for a batch run.

    Returns ``(cov_artifact, coverage_available, emitter)``.
    """
    cov_artifact: CoverageArtifact | None = None
    emitter = get_emitter(first.runner_pack_id) if coverage_dir else None
    coverage_available = False
    if emitter:
        coverage_tools = await coordinator.get_coverage_capability(
            first.workspace_root, first.runner_pack_id
        )
        runtime = PackRuntime(
            workspace_root=Path(first.workspace_root),
            runner_available=True,
            coverage_tools=coverage_tools,
        )
        capability = emitter.capability(runtime)
        coverage_available = capability == CoverageCapability.AVAILABLE
    return cov_artifact, coverage_available, emitter


def _batch_error_suite(
    targets: list[TestTarget],
    error_type: str,
    error_detail: str,
    cmd: list[str],
    cwd: str | Path,
    stdout: str | None = None,
    stderr: str | None = None,
) -> ParsedTestSuite:
    """Build a ``ParsedTestSuite`` for a batch execution error."""
    selectors = ", ".join(t.selector for t in targets)
    return ParsedTestSuite(
        name="batch",
        errors=len(targets),
        error_type=error_type,
        error_detail=error_detail,
        execution=ExecutionContext(
            command=cmd,
            working_directory=str(cwd),
            raw_stdout=stdout if stdout else None,
            raw_stderr=stderr if stderr else None,
        ),
        target_selector=selectors,
        workspace_root=targets[0].workspace_root if targets else "",
    )


async def _poll_child_rss(proc: asyncio.subprocess.Process) -> int:
    """Poll child process RSS until it exits, returning peak MB."""
    peak = 0
    pid = proc.pid
    while proc.returncode is None:
        rss = child_rss_mb(pid) if pid else 0
        if rss > peak:
            peak = rss
        await asyncio.sleep(2)
    rss = child_rss_mb(pid) if pid else 0
    if rss > peak:
        peak = rss
    return peak


def _single_error_suite(
    target: TestTarget,
    error_type: str,
    error_detail: str,
    suggested_action: str,
    cmd: list[str],
    cwd: str | Path,
    stdout: str | None = None,
    stderr: str | None = None,
) -> ParsedTestSuite:
    """Build a ``ParsedTestSuite`` for a single-target execution error."""
    return ParsedTestSuite(
        name=target.selector,
        errors=1,
        error_type=error_type,
        error_detail=error_detail,
        suggested_action=suggested_action,
        execution=ExecutionContext(
            command=cmd,
            working_directory=str(cwd),
            raw_stdout=stdout if stdout else None,
            raw_stderr=stderr if stderr else None,
        ),
        target_selector=target.selector,
        workspace_root=target.workspace_root,
    )


async def _exec_and_parse(
    cmd: list[str],
    cwd: str | Path,
    env: dict[str, str],
    timeout_sec: int,
    artifact_dir: Path,
    safe_name: str,
    output_path: Path,
    pack: object,
    target: TestTarget,
) -> tuple[ParsedTestSuite, int]:
    """Spawn subprocess, write artifacts, parse output. Returns (suite, peak_rss_mb)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    rss_task = asyncio.create_task(_poll_child_rss(proc))
    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        proc.communicate(), timeout=timeout_sec
    )
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    exit_code = proc.returncode
    peak_rss_mb = await rss_task
    # Write artifacts
    atomic_write_text(artifact_dir / f"{safe_name}.stdout.txt", stdout)
    if stderr:
        atomic_write_text(artifact_dir / f"{safe_name}.stderr.txt", stderr)
    execution = ExecutionContext(
        command=cmd,
        working_directory=str(cwd),
        exit_code=exit_code,
        raw_stdout=stdout,
        raw_stderr=stderr if stderr else None,
    )
    result = pack.parse_output(output_path, stdout)  # type: ignore[union-attr]
    result.target_selector = target.selector
    result.workspace_root = target.workspace_root
    result.execution = execution
    if result.tests is not None:
        result.parsed_test_count = len(result.tests)
    else:
        result.parsed_test_count = None
    _classify_result_error(result, output_path, stdout, exit_code)
    return result, peak_rss_mb
