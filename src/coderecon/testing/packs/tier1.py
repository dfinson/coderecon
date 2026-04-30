"""Runner packs for Tier-1 languages (Python pytest, JavaScript jest/vitest, Go). """

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from coderecon.testing.models import ParsedTestCase, ParsedTestSuite, TestTarget
from coderecon.testing.runner_pack import (
    MarkerRule,
    OutputStrategy,
    RunnerCapabilities,
    RunnerPack,
    runner_registry,
)

if TYPE_CHECKING:
    from coderecon.testing.runtime import RuntimeExecutionContext

import structlog

from coderecon.config.constants import MS_PER_SEC
from coderecon.testing.packs import _is_prunable_path

log = structlog.get_logger(__name__)

@runner_registry.register
class PytestPack(RunnerPack):
    """Python pytest runner."""
    pack_id = "python.pytest"
    language = "python"
    runner_name = "pytest"
    markers = (
        MarkerRule("pytest.ini", confidence="high"),
        MarkerRule("conftest.py", confidence="medium"),
        MarkerRule("pyproject.toml", content_match="[tool.pytest", confidence="high"),
        MarkerRule("setup.cfg", content_match="[tool:pytest]", confidence="medium"),
    )
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file", "package"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "pytest.ini").exists():
            return 1.0
        if (workspace_root / "conftest.py").exists():
            return 0.8
        pyproject = workspace_root / "pyproject.toml"
        if pyproject.exists():
            try:
                if "[tool.pytest" in pyproject.read_text():
                    return 1.0
            except OSError:
                log.debug("pytest_config_read_failed", path="pyproject.toml", exc_info=True)
        setup_cfg = workspace_root / "setup.cfg"
        if setup_cfg.exists():
            try:
                if "[tool:pytest]" in setup_cfg.read_text():
                    return 0.9
            except OSError:
                log.debug("pytest_config_read_failed", path="setup.cfg", exc_info=True)
        # Check for any test files
        if list(workspace_root.glob("**/test_*.py")) or list(workspace_root.glob("**/*_test.py")):
            return 0.5
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        patterns = ["**/test_*.py", "**/*_test.py"]
        seen: set[str] = set()
        for pattern in patterns:
            for path in workspace_root.glob(pattern):
                # Skip prunable directories (.venv, __pycache__, etc.)
                if _is_prunable_path(path, workspace_root):
                    continue
                if path.name.startswith("_") or path.name == "conftest.py":
                    continue
                rel = str(path.relative_to(workspace_root))
                if rel in seen:
                    continue
                seen.add(rel)
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language="python",
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available, otherwise fall back to bare pytest
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                # Use configured executable (e.g., /repo/.venv/bin/python -m pytest)
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                # ExecutionContext exists but pytest not available via it
                cmd = ["pytest"]
        else:
            # No execution context, use bare pytest (legacy behavior)
            cmd = ["pytest"]
        cmd.extend([target.selector, f"--junitxml={output_path}", "--tb=short", "-q"])
        if pattern:
            cmd.extend(["-k", pattern])
        if tags:
            cmd.extend(["-m", " or ".join(tags)])
        return cmd
    def build_batch_command(
        self,
        targets: list[TestTarget],
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str] | None:
        """Run multiple test files in a single pytest invocation."""
        if not targets:
            return None
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["pytest"]
        else:
            cmd = ["pytest"]
        # Add all selectors (file paths) as positional args
        for t in targets:
            cmd.append(t.selector)
        cmd.extend([f"--junitxml={output_path}", "--tb=short", "-q"])
        if pattern:
            cmd.extend(["-k", pattern])
        if tags:
            cmd.extend(["-m", " or ".join(tags)])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="pytest", errors=1)

# JavaScript/TypeScript - Jest

@runner_registry.register
class JestPack(RunnerPack):
    """JavaScript/TypeScript Jest runner."""
    pack_id = "js.jest"
    language = "javascript"
    runner_name = "jest"
    markers = (
        MarkerRule("jest.config.js", confidence="high"),
        MarkerRule("jest.config.ts", confidence="high"),
        MarkerRule("jest.config.json", confidence="high"),
        MarkerRule("jest.config.mjs", confidence="high"),
        MarkerRule("package.json", content_match='"jest"', confidence="medium"),
    )
    output_strategy = OutputStrategy(format="json", file_based=True, file_pattern="results.json")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,  # Via jest-junit reporter
    )
    def detect(self, workspace_root: Path) -> float:
        for config in ["jest.config.js", "jest.config.ts", "jest.config.json", "jest.config.mjs"]:
            if (workspace_root / config).exists():
                return 1.0
        pkg = workspace_root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                if not isinstance(data, dict):
                    return 0.0
                if "jest" in data:
                    return 0.9
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "jest" in deps:
                    return 0.8
            except (OSError, json.JSONDecodeError, KeyError):
                log.debug("jest_config_read_failed", exc_info=True)
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        patterns = [
            "**/*.test.js",
            "**/*.test.ts",
            "**/*.test.jsx",
            "**/*.test.tsx",
            "**/*.spec.js",
            "**/*.spec.ts",
        ]
        seen: set[str] = set()
        for pattern in patterns:
            for path in workspace_root.glob(pattern):
                if _is_prunable_path(path, workspace_root):
                    continue
                rel = str(path.relative_to(workspace_root))
                if rel in seen:
                    continue
                seen.add(rel)
                lang = "typescript" if path.suffix in (".ts", ".tsx") else "javascript"
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language=lang,
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                pm = self._detect_package_manager(Path(target.workspace_root))
                cmd = [pm, "jest"] if pm == "npx" else [pm, "run", "jest", "--"]
        else:
            pm = self._detect_package_manager(Path(target.workspace_root))
            cmd = [pm, "jest"] if pm == "npx" else [pm, "run", "jest", "--"]
        cmd.extend([target.selector, "--json", f"--outputFile={output_path}"])
        if pattern:
            cmd.extend(["--testNamePattern", pattern])
        return cmd
    def _detect_package_manager(self, root: Path) -> str:
        if (root / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (root / "yarn.lock").exists():
            return "yarn"
        if (root / "bun.lockb").exists():
            return "bun"
        return "npx"
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        if output_path.exists():
            return self._parse_jest_json(output_path.read_text())
        return ParsedTestSuite(name="jest", errors=1)
    def _parse_jest_json(self, content: str) -> ParsedTestSuite:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return ParsedTestSuite(name="jest", errors=1)
        tests: list[ParsedTestCase] = []
        for result in data.get("testResults", []):
            for assertion in result.get("assertionResults", []):
                status_map: dict[str, Literal["passed", "failed", "skipped", "error"]] = {
                    "passed": "passed",
                    "failed": "failed",
                    "pending": "skipped",
                    "skipped": "skipped",
                }
                status = status_map.get(assertion.get("status", ""), "error")
                tests.append(
                    ParsedTestCase(
                        name=assertion.get("title", "unknown"),
                        classname=" > ".join(assertion.get("ancestorTitles", [])),
                        status=status,
                        duration_seconds=assertion.get("duration", 0) / MS_PER_SEC,
                        message="\n".join(assertion.get("failureMessages", [])) or None,
                    )
                )
        return ParsedTestSuite(
            name="jest",
            tests=tests,
            total=len(tests),
            passed=sum(1 for t in tests if t.status == "passed"),
            failed=sum(1 for t in tests if t.status == "failed"),
            skipped=sum(1 for t in tests if t.status == "skipped"),
            errors=sum(1 for t in tests if t.status == "error"),
            duration_seconds=data.get("testResults", [{}])[0].get("endTime", 0) / MS_PER_SEC
            if data.get("testResults")
            else 0,
        )

# JavaScript/TypeScript - Vitest

@runner_registry.register
class VitestPack(RunnerPack):
    """JavaScript/TypeScript Vitest runner."""
    pack_id = "js.vitest"
    language = "javascript"
    runner_name = "vitest"
    markers = (
        MarkerRule("vitest.config.js", confidence="high"),
        MarkerRule("vitest.config.ts", confidence="high"),
        MarkerRule("vite.config.js", content_match="vitest", confidence="medium"),
        MarkerRule("vite.config.ts", content_match="vitest", confidence="medium"),
    )
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,
    )
    def detect(self, workspace_root: Path) -> float:
        for config in ["vitest.config.js", "vitest.config.ts"]:
            if (workspace_root / config).exists():
                return 1.0
        for vite in ["vite.config.js", "vite.config.ts"]:
            if (workspace_root / vite).exists():
                try:
                    if "vitest" in (workspace_root / vite).read_text():
                        return 0.9
                except OSError:
                    log.debug("vitest_config_read_failed", path=vite, exc_info=True)
        pkg = workspace_root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                if not isinstance(data, dict):
                    return 0.0
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "vitest" in deps:
                    return 0.7
            except (OSError, json.JSONDecodeError):
                log.debug("vitest_deps_read_failed", exc_info=True)
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Same patterns as Jest
        targets: list[TestTarget] = []
        patterns = ["**/*.test.js", "**/*.test.ts", "**/*.spec.js", "**/*.spec.ts"]
        seen: set[str] = set()
        for pattern in patterns:
            for path in workspace_root.glob(pattern):
                if _is_prunable_path(path, workspace_root):
                    continue
                rel = str(path.relative_to(workspace_root))
                if rel in seen:
                    continue
                seen.add(rel)
                lang = "typescript" if path.suffix == ".ts" else "javascript"
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language=lang,
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["npx", "vitest", "run"]
        else:
            cmd = ["npx", "vitest", "run"]
        cmd.extend(
            [
                target.selector,
                "--reporter=junit",
                f"--outputFile={output_path}",
            ]
        )
        if pattern:
            cmd.extend(["--testNamePattern", pattern])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="vitest", errors=1)

# Go - go test

@runner_registry.register
class GoTestPack(RunnerPack):
    """Go test runner."""
    pack_id = "go.gotest"
    language = "go"
    runner_name = "go test"
    markers = (
        MarkerRule("go.mod", confidence="high"),
        MarkerRule("go.sum", confidence="high"),
    )
    output_strategy = OutputStrategy(
        format="ndjson", file_based=False
    )  # go test -json writes to stdout
    capabilities = RunnerCapabilities(
        supported_kinds=["package"],  # Go tests run at package level
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Build tags
        supports_parallel=True,
        supports_junit_output=False,  # Requires external tool like gotestsum
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "go.mod").exists():
            return 1.0
        if (workspace_root / "go.sum").exists():
            return 0.9
        if list(workspace_root.glob("**/*.go")):
            return 0.3
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        seen_packages: set[str] = set()
        # Go standard directories that may be in PRUNABLE_DIRS (e.g., 'pkg' for Ruby)
        go_allowed = frozenset({"pkg", "internal", "cmd"})
        for path in workspace_root.glob("**/*_test.go"):
            if _is_prunable_path(path, workspace_root, allowed_dirs=go_allowed):
                continue
            pkg_dir = path.parent
            rel_pkg = str(pkg_dir.relative_to(workspace_root))
            if rel_pkg in seen_packages:
                continue
            seen_packages.add(rel_pkg)
            selector = f"./{rel_pkg}" if rel_pkg != "." else "./..."
            targets.append(
                TestTarget(
                    target_id=f"test:{selector}",
                    selector=selector,
                    kind="package",
                    language="go",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["go", "test", "-json"]
        else:
            cmd = ["go", "test", "-json"]
        cmd.append(target.selector)
        if pattern:
            cmd.extend(["-run", pattern])
        if tags:
            cmd.extend(["-tags", ",".join(tags)])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_go_test_json
        return parse_go_test_json(stdout)
