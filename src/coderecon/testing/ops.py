"""Test operations — TestOps class for discovery and execution.

Per SPEC.md §23.7 test tool specification.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from coderecon.adapters.files.ops import atomic_write_text

# Import packs to trigger registration
from coderecon.testing import packs as _packs  # noqa: F401
from coderecon.testing.models import (
    ExecutionDiagnostic,
    TargetProgress,
    TestCaseProgress,
    TestFailure,
    TestProgress,
    TestResult,
    TestRunStatus,
    TestTarget,
)
from coderecon.testing.ops_environment import _default_parallelism
from coderecon.testing.ops_executor import _execute_tests as _execute_tests_impl
from coderecon.testing.ops_workspaces import (
    DetectedWorkspace,
    _os_script_path,
    detect_workspaces,
)
from coderecon.testing.runner_pack import runner_registry

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)

# Re-exports for backward compatibility
from coderecon.testing.ops_environment import (  # noqa: E402, F401
    clear_coverage_tools_cache,
    detect_coverage_tools,
    detect_node_package_manager,
    detect_python_venv,
    get_python_executable,
)
from coderecon.testing.ops_workspaces import _is_prunable_path  # noqa: E402, F401


class TestOps:
    """Test discovery and execution operations.
    Uses runner packs for detection-driven execution.
    Leverages the index for context-aware workspace detection.
    """
    def __init__(
        self,
        repo_root: Path,
        coordinator: IndexCoordinatorEngine,
        *,
        memory_reserve_mb: int = 1024,
        subprocess_memory_limit_mb: int | None = None,
    ) -> None:
        """Initialize test ops."""
        self._repo_root = repo_root
        self._workspace_root = repo_root
        self._coordinator = coordinator
        self._artifacts_base = repo_root / ".recon" / "artifacts" / "tests"
        self._memory_reserve_mb = memory_reserve_mb
        self._subprocess_memory_limit_mb = subprocess_memory_limit_mb
    async def discover(
        self,
        paths: list[str] | None = None,
    ) -> TestResult:
        """Discover test targets in the repository.
        Index-first approach: Always queries the index. The index waits for
        freshness internally (via coordinator.wait_for_freshness). No filesystem
        fallback - if index isn't ready, we block until it is.
        """
        from typing import cast

        from coderecon.testing.models import TargetKind
        indexed_targets = await self._coordinator.get_test_targets()
        all_targets = [
            TestTarget(
                target_id=t.target_id,
                selector=t.selector,
                kind=cast(TargetKind, t.kind),
                language=t.language,
                runner_pack_id=t.runner_pack_id,
                workspace_root=t.workspace_root,
                estimated_cost=1.0,
                test_count=t.test_count,
            )
            for t in indexed_targets
        ]
        if paths:
            all_targets = [
                t
                for t in all_targets
                if any(t.selector.startswith(p) or p.startswith(t.selector) for p in paths)
            ]
        agentic_hint = None
        if not all_targets:
            agentic_hint = await self._generate_agentic_hint()
        return TestResult(action="discover", targets=all_targets, agentic_hint=agentic_hint)
    async def _discover_from_filesystem(
        self,
        paths: list[str] | None = None,
    ) -> TestResult:
        """Fallback filesystem-based discovery."""
        all_targets: list[TestTarget] = []
        workspaces = await self._detect_workspaces_from_index()
        if not workspaces:
            workspaces = detect_workspaces(self._repo_root)
        for ws in workspaces:
            try:
                targets = await ws.pack.discover(ws.root)
                if paths:
                    targets = [
                        t
                        for t in targets
                        if any(t.selector.startswith(p) or p.startswith(t.selector) for p in paths)
                    ]
                all_targets.extend(targets)
            except (OSError, RuntimeError, ValueError):
                log.debug("pack_discovery_failed", exc_info=True)
                continue
        seen: set[str] = set()
        unique_targets: list[TestTarget] = []
        for t in all_targets:
            if t.target_id not in seen:
                seen.add(t.target_id)
                unique_targets.append(t)
        agentic_hint = None
        if not unique_targets:
            agentic_hint = await self._generate_agentic_hint()
        return TestResult(action="discover", targets=unique_targets, agentic_hint=agentic_hint)
    async def _detect_workspaces_from_index(self) -> list[DetectedWorkspace]:
        """Detect workspaces using index contexts."""
        workspaces: list[DetectedWorkspace] = []
        try:
            contexts = await self._coordinator.get_contexts()
        except (OSError, RuntimeError, ValueError):
            log.debug("index_context_lookup_failed", exc_info=True)
            return []
        roots_seen: set[str] = set()
        for ctx in contexts:
            root_path = ctx.root_path or ""
            if root_path in roots_seen:
                continue
            roots_seen.add(root_path)
            ws_root = self._repo_root / root_path if root_path else self._repo_root
            for pack_class, confidence in runner_registry.detect_all(ws_root):
                workspaces.append(
                    DetectedWorkspace(
                        root=ws_root,
                        pack=pack_class(),
                        confidence=confidence,
                    )
                )
        seen: dict[tuple[Path, str], DetectedWorkspace] = {}
        for ws in workspaces:
            key = (ws.root, ws.pack.pack_id)
            if key not in seen or ws.confidence > seen[key].confidence:
                seen[key] = ws
        return list(seen.values())
    async def _get_targets_by_id(self, target_ids: list[str]) -> list[TestTarget]:
        """Get test targets by ID from the index."""
        return await self._get_targets_from_index(target_ids=target_ids)
    async def _get_all_targets_from_index(self) -> list[TestTarget]:
        """Get ALL test targets from the index."""
        return await self._get_targets_from_index()
    async def _get_targets_from_index(
        self, *, target_ids: list[str] | None = None,
    ) -> list[TestTarget]:
        """Get test targets from the index, optionally filtered by IDs."""
        from typing import cast

        from coderecon.testing.models import TargetKind
        indexed_targets = await self._coordinator.get_test_targets(target_ids=target_ids)
        return [
            TestTarget(
                target_id=t.target_id,
                selector=t.selector,
                kind=cast(TargetKind, t.kind),
                language=t.language,
                runner_pack_id=t.runner_pack_id,
                workspace_root=t.workspace_root,
                estimated_cost=1.0,
                test_count=t.test_count,
            )
            for t in indexed_targets
        ]
    async def _generate_agentic_hint(self) -> str:
        """Generate agentic hint for running tests when no targets detected."""
        hints: list[str] = []
        try:
            file_stats = await self._coordinator.get_file_stats()
            languages = set(file_stats.keys())
        except (OSError, RuntimeError, ValueError):
            log.debug("file_stats_lookup_failed", exc_info=True)
            languages = set()
        if "python" in languages:
            hints.append("Python: Run `pytest` or `python -m pytest`")
        if "javascript" in languages:
            hints.append("JavaScript: Run `npm test`, `yarn test`, or `jest`")
        if "go" in languages:
            hints.append("Go: Run `go test ./...`")
        if "rust" in languages:
            hints.append("Rust: Run `cargo test`")
        if "jvm" in languages:
            gradlew = _os_script_path("./gradlew")
            hints.append(f"Java/Kotlin: Run `{gradlew} test` or `mvn test`")
        if "ruby" in languages:
            hints.append("Ruby: Run `bundle exec rspec` or `rake test`")
        if "dotnet" in languages:
            hints.append("C#/.NET: Run `dotnet test`")
        if "php" in languages:
            phpunit = _os_script_path("./vendor/bin/phpunit")
            hints.append(f"PHP: Run `phpunit` or `{phpunit}`")
        if "elixir" in languages:
            hints.append("Elixir: Run `mix test`")
        if not hints:
            hints.append(
                "No test framework detected. Check for test files and install "
                "appropriate test runner (pytest, jest, go test, cargo test, etc.)"
            )
        return "No test targets detected automatically. Manual test commands:\n\n" + "\n".join(
            f"  - {h}" for h in hints
        )
    async def run(
        self,
        targets: list[str] | None = None,
        *,
        target_filter: str | None = None,
        test_filter: str | None = None,
        tags: list[str] | None = None,
        failed_only: bool = False,  # noqa: ARG002
        parallelism: int | None = None,
        timeout_sec: int | None = None,
        fail_fast: bool = False,
        coverage: bool = True,
        coverage_dir: str | None = None,
    ) -> TestResult:
        """Run tests using runner packs."""
        if targets is not None and len(targets) == 0:
            return TestResult(
                action="run",
                run_status=TestRunStatus(run_id="", status="failed"),
                agentic_hint="Empty targets list provided. Either omit targets "
                "to run all tests, or specify at least one target. "
                "Use verify to find available targets.",
            )
        run_id = str(uuid.uuid4())[:8]
        cancel_event = asyncio.Event()
        artifact_dir = self._artifacts_base / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        effective_coverage_dir: Path | None = None
        if coverage:
            if coverage_dir:
                effective_coverage_dir = Path(coverage_dir)
            else:
                effective_coverage_dir = artifact_dir / "coverage"
            effective_coverage_dir.mkdir(parents=True, exist_ok=True)
        progress = TestProgress(targets=TargetProgress(), cases=TestCaseProgress())
        failures: list[TestFailure] = []
        agentic_hint_for_empty: str | None = None
        if targets:
            resolved_targets = await self._get_targets_by_id(targets)
        else:
            resolved_targets = await self._get_all_targets_from_index()
        if target_filter and resolved_targets:
            before_count = len(resolved_targets)
            resolved_targets = [
                t
                for t in resolved_targets
                if target_filter in t.selector or target_filter in t.target_id
            ]
            if not resolved_targets:
                return TestResult(
                    action="run",
                    run_status=TestRunStatus(run_id=run_id, status="failed"),
                    agentic_hint=f"target_filter='{target_filter}' matched 0 of {before_count} targets. "
                    f"Use verify to see available target paths. "
                    f"To filter test NAMES within targets, use test_filter instead.",
                )
        if not resolved_targets:
            return TestResult(
                action="run",
                run_status=TestRunStatus(
                    run_id=run_id, status="completed",
                    progress=progress, artifact_dir=str(artifact_dir),
                ),
                agentic_hint="No test targets found to run. "
                + (agentic_hint_for_empty or "Use verify to check available targets."),
            )
        progress.targets.total = len(resolved_targets)
        task = asyncio.create_task(
            self._execute_tests(
                run_id=run_id, targets=resolved_targets, progress=progress,
                failures=failures, cancel_event=cancel_event,
                artifact_dir=artifact_dir, test_filter=test_filter, tags=tags,
                parallelism=parallelism or _default_parallelism(),
                timeout_sec=timeout_sec or 300, fail_fast=fail_fast,
                coverage=coverage, coverage_dir=effective_coverage_dir,
            )
        )
        start_time = time.time()
        try:
            run_status = await task
        except (OSError, RuntimeError, asyncio.CancelledError):
            log.warning("test_execution_failed", exc_info=True)
            run_status = TestRunStatus(
                run_id=run_id, status="failed", progress=progress,
                failures=failures, duration_seconds=time.time() - start_time,
                artifact_dir=str(artifact_dir),
            )
        return TestResult(action="run", run_status=run_status)
    async def _execute_tests(
        self, run_id: str, targets: list[TestTarget],
        progress: TestProgress, failures: list[TestFailure],
        cancel_event: asyncio.Event, artifact_dir: Path,
        test_filter: str | None, tags: list[str] | None,
        parallelism: int, timeout_sec: int, fail_fast: bool,
        coverage: bool, coverage_dir: Path | None,
    ) -> TestRunStatus:
        """Execute tests concurrently with semaphore-limited parallelism."""
        return await _execute_tests_impl(
            coordinator=self._coordinator,
            workspace_root=self._workspace_root,
            memory_reserve_mb=self._memory_reserve_mb,
            subprocess_memory_limit_mb=self._subprocess_memory_limit_mb,
            persist_callback=self._persist_result,
            run_id=run_id, targets=targets, progress=progress,
            failures=failures, cancel_event=cancel_event,
            artifact_dir=artifact_dir, test_filter=test_filter, tags=tags,
            parallelism=parallelism, timeout_sec=timeout_sec,
            fail_fast=fail_fast, coverage=coverage, coverage_dir=coverage_dir,
        )
    def _persist_result(self, artifact_dir: Path, status: TestRunStatus) -> None:
        """Persist test run result to artifact directory."""
        result_path = artifact_dir / "result.json"
        result_data = {
            "run_id": status.run_id,
            "status": status.status,
            "duration_seconds": status.duration_seconds,
            "artifact_dir": status.artifact_dir,
            "progress": {
                "targets": {
                    "total": status.progress.targets.total if status.progress else 0,
                    "completed": status.progress.targets.completed if status.progress else 0,
                    "running": status.progress.targets.running if status.progress else 0,
                    "failed": status.progress.targets.failed if status.progress else 0,
                },
                "cases": {
                    "total": status.progress.cases.total if status.progress else 0,
                    "passed": status.progress.cases.passed if status.progress else 0,
                    "failed": status.progress.cases.failed if status.progress else 0,
                    "skipped": status.progress.cases.skipped if status.progress else 0,
                    "errors": status.progress.cases.errors if status.progress else 0,
                },
            }
            if status.progress
            else None,
            "failures": [
                {
                    "name": f.name, "path": f.path, "line": f.line,
                    "message": f.message, "traceback": f.traceback,
                    "classname": f.classname,
                    "duration_seconds": f.duration_seconds,
                }
                for f in (status.failures or [])
            ],
            "diagnostics": [
                {
                    "target_id": d.target_id, "error_type": d.error_type,
                    "error_detail": d.error_detail,
                    "suggested_action": d.suggested_action,
                    "command": d.command,
                    "working_directory": d.working_directory,
                    "exit_code": d.exit_code, "raw_stderr": d.raw_stderr,
                    "parsed_test_count": d.parsed_test_count,
                }
                for d in (status.diagnostics or [])
            ],
            "coverage": status.coverage,
            "target_selectors": status.target_selectors,
        }
        atomic_write_text(result_path, json.dumps(result_data, indent=2))
    def _load_result(self, artifact_dir: Path) -> TestRunStatus | None:
        """Load test run result from artifact directory."""
        result_path = artifact_dir / "result.json"
        if not result_path.exists():
            return None
        try:
            data = json.loads(result_path.read_text())
            progress = None
            if data.get("progress"):
                p = data["progress"]
                progress = TestProgress(
                    targets=TargetProgress(
                        total=p["targets"]["total"],
                        completed=p["targets"]["completed"],
                        running=p["targets"]["running"],
                        failed=p["targets"]["failed"],
                    ),
                    cases=TestCaseProgress(
                        total=p["cases"]["total"],
                        passed=p["cases"]["passed"],
                        failed=p["cases"]["failed"],
                        skipped=p["cases"]["skipped"],
                        errors=p["cases"]["errors"],
                    ),
                )
            failures = [
                TestFailure(
                    name=f["name"], path=f["path"], line=f.get("line"),
                    message=f["message"], traceback=f.get("traceback"),
                    classname=f.get("classname"),
                    duration_seconds=f.get("duration_seconds"),
                )
                for f in data.get("failures", [])
            ]
            diagnostics = [
                ExecutionDiagnostic(
                    target_id=d["target_id"], error_type=d["error_type"],
                    error_detail=d.get("error_detail"),
                    suggested_action=d.get("suggested_action"),
                    command=d.get("command"),
                    working_directory=d.get("working_directory"),
                    exit_code=d.get("exit_code"),
                    raw_stderr=d.get("raw_stderr"),
                    parsed_test_count=d.get("parsed_test_count"),
                )
                for d in data.get("diagnostics", [])
            ]
            return TestRunStatus(
                run_id=data["run_id"], status=data["status"],
                progress=progress, failures=failures,
                diagnostics=diagnostics,
                duration_seconds=data.get("duration_seconds"),
                artifact_dir=data.get("artifact_dir"),
                coverage=data.get("coverage", []),
                target_selectors=data.get("target_selectors", []),
            )
        except (json.JSONDecodeError, KeyError):
            log.debug("test_run_status_parse_failed", exc_info=True)
            return None
