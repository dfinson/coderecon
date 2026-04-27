"""Runner packs for scripting languages (Tier 2).

Flutter, Bats (Bash), Pester (PowerShell), Busted (Lua), Mix (Elixir).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coderecon.testing.models import ParsedTestSuite, TestTarget
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

from coderecon.testing.packs import _is_prunable_path

log = structlog.get_logger(__name__)

# Kotlin - Gradle with Kotlin DSL


@runner_registry.register
class FlutterTestPack(RunnerPack):
    """Flutter test runner."""
    pack_id = "dart.flutter_test"
    language = "dart"
    runner_name = "flutter test"
    markers = (
        MarkerRule("pubspec.yaml", content_match="flutter:", confidence="high"),
    )
    output_strategy = OutputStrategy(format="json", file_based=False)
    capabilities = RunnerCapabilities(
        supported_kinds=["file", "package"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=False,
    )
    def detect(self, workspace_root: Path) -> float:
        pubspec = workspace_root / "pubspec.yaml"
        if pubspec.exists():
            try:
                content = pubspec.read_text()
                if "flutter:" in content:
                    return 1.0
            except OSError:
                log.debug("flutter_config_read_failed", exc_info=True)
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        from coderecon.testing.packs import _discover_dart_tests
        return await _discover_dart_tests(self, workspace_root)
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - Flutter uses standard command
    ) -> list[str]:
        cmd = ["flutter", "test", "--machine", target.selector]
        if pattern:
            cmd.extend(["--name", pattern])
        if tags:
            cmd.extend(["--tags", ",".join(tags)])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:
        # Flutter uses same JSON format as dart test
        dart_pack = DartTestPack()
        result = dart_pack.parse_output(output_path, stdout)
        result.name = "flutter test"
        return result

# Bash - bats (Bash Automated Testing System)

@runner_registry.register
class BatsPack(RunnerPack):
    """Bash bats test runner."""
    pack_id = "bash.bats"
    language = "bash"
    runner_name = "bats"
    markers = (
        MarkerRule("test/*.bats", confidence="high"),
        MarkerRule("tests/*.bats", confidence="high"),
        MarkerRule(".bats", confidence="medium"),
    )
    output_strategy = OutputStrategy(format="tap", file_based=False)  # bats outputs TAP by default
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,  # bats --formatter junit
    )
    def detect(self, workspace_root: Path) -> float:
        if list(workspace_root.glob("test/*.bats")) or list(workspace_root.glob("tests/*.bats")):
            return 1.0
        if list(workspace_root.glob("**/*.bats")):
            return 0.7
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        from coderecon.testing.packs import _discover_by_glob
        return _discover_by_glob(self, workspace_root, "**/*.bats", "bash")
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - Bats uses standard command
    ) -> list[str]:
        cmd = ["bats", "--formatter", "junit", target.selector]
        if pattern:
            cmd.extend(["--filter", pattern])
        # Redirect to file
        return cmd + [">", str(output_path)]
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:
        from coderecon.testing.parsers import parse_junit_xml, parse_tap
        # Try JUnit first (if formatter was used)
        if output_path.exists():
            content = output_path.read_text()
            if content.strip().startswith("<"):
                return parse_junit_xml(content)
        # Fall back to TAP
        return parse_tap(stdout)

# PowerShell - Pester

@runner_registry.register
class PesterPack(RunnerPack):
    """PowerShell Pester test runner."""
    pack_id = "powershell.pester"
    language = "powershell"
    runner_name = "Pester"
    markers = (
        MarkerRule("*.Tests.ps1", confidence="high"),
        MarkerRule("tests/*.Tests.ps1", confidence="high"),
    )
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="testResults.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,  # Via -OutputFormat JUnitXml
    )
    def detect(self, workspace_root: Path) -> float:
        if list(workspace_root.glob("**/*.Tests.ps1")):
            return 1.0
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        from coderecon.testing.packs import _discover_by_glob
        return _discover_by_glob(self, workspace_root, "**/*.Tests.ps1", "powershell")
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - Pester uses pwsh command
    ) -> list[str]:
        # Build Pester invocation
        pester_config = f"""
$config = New-PesterConfiguration
$config.Run.Path = '{target.selector}'
$config.TestResult.Enabled = $true
$config.TestResult.OutputPath = '{output_path}'
$config.TestResult.OutputFormat = 'JUnitXml'
"""
        if pattern:
            pester_config += f"$config.Filter.FullName = '*{pattern}*'\n"
        if tags:
            pester_config += f"$config.Filter.Tag = @({', '.join(repr(t) for t in tags)})\n"
        pester_config += "Invoke-Pester -Configuration $config"
        return ["pwsh", "-NoProfile", "-Command", pester_config]
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="pester", errors=1)

# Lua - busted

@runner_registry.register
class BustedPack(RunnerPack):
    """Lua busted test runner."""
    pack_id = "lua.busted"
    language = "lua"
    runner_name = "busted"
    markers = (
        MarkerRule(".busted", confidence="high"),
        MarkerRule("spec/*_spec.lua", confidence="high"),
    )
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,  # busted -o junit
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / ".busted").exists():
            return 1.0
        if list(workspace_root.glob("spec/*_spec.lua")):
            return 0.9
        if list(workspace_root.glob("**/*_spec.lua")):
            return 0.7
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        from coderecon.testing.packs import _discover_by_glob
        return _discover_by_glob(self, workspace_root, "**/*_spec.lua", "lua")
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - busted uses standard command
    ) -> list[str]:
        cmd = ["busted", "-o", "junit", target.selector, ">", str(output_path)]
        if pattern:
            cmd.extend(["--filter", pattern])
        if tags:
            cmd.extend(["--tags", ",".join(tags)])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="busted", errors=1)

# Elixir - Mix Test (ExUnit)

@runner_registry.register
class MixTestPack(RunnerPack):
    """Elixir ExUnit runner (mix test)."""
    pack_id = "elixir.mix_test"
    language = "elixir"
    runner_name = "mix_test"
    markers = (
        MarkerRule("mix.exs", confidence="high"),
    )
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="_build/test/lib/**/test-junit-report.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Via --only
        supports_parallel=False,
        supports_junit_output=True,
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "mix.exs").exists():
            # Check it's not a Phoenix umbrella or deps-only project
            mix_exs = workspace_root / "mix.exs"
            try:
                content = mix_exs.read_text()
                if "def project" in content:
                    return 1.0
            except OSError:
                log.debug("elixir_config_read_failed", exc_info=True)
            return 0.9
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        test_dir = workspace_root / "test"
        if not test_dir.is_dir():
            return targets
        for path in test_dir.rglob("*_test.exs"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="elixir",
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
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002
    ) -> list[str]:
        cmd = ["mix", "test", target.selector]
        if pattern:
            cmd.extend(["--only", f"test:{pattern}"])
        if tags:
            for tag in tags:
                cmd.extend(["--only", tag])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        # ExUnit prints results to stdout; parse from stdout lines
        suite = ParsedTestSuite(name="mix_test")
        for line in stdout.splitlines():
            line = line.strip()
            # ExUnit format: "  1) test description (Module)\n     test/path:line"
            if line.startswith("0 failures"):
                break
            # Count pass/fail from summary line: "X tests, Y failures" or "1 test, 0 failures"
            if "test" in line and "failure" in line:
                parts = line.split(",")
                for part in parts:
                    part = part.strip()
                    # Match both "tests" and "test" (singular)
                    if part.endswith("tests") or part.endswith("test"):
                        try:
                            total = int(part.split()[0])
                            suite.total = total
                        except (ValueError, IndexError):
                            log.debug("elixir_output_parse_failed", exc_info=True)
                    elif "failure" in part:
                        try:
                            failures = int(part.split()[0])
                            suite.failed = failures
                        except (ValueError, IndexError):
                            log.debug("elixir_output_parse_failed", exc_info=True)
        return suite

# Haskell - Cabal Test

