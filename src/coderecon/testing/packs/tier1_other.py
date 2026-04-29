"""Runner packs for Tier-1 languages (C# dotnet, C/C++ CTest, Ruby, PHP). """

from __future__ import annotations

import json
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

@runner_registry.register
class DotnetTestPack(RunnerPack):
    """C# dotnet test runner."""
    pack_id = "csharp.dotnet"
    language = "csharp"
    runner_name = "dotnet test"
    markers = (
        MarkerRule("*.csproj", confidence="high"),
        MarkerRule("*.sln", confidence="high"),
        MarkerRule("global.json", confidence="medium"),
    )
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="TestResults/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Via --filter
        supports_parallel=True,
        supports_junit_output=True,  # Via JunitXml.TestLogger nuget
    )
    def detect(self, workspace_root: Path) -> float:
        if list(workspace_root.glob("*.sln")):
            return 1.0
        if list(workspace_root.glob("*.csproj")):
            return 0.9
        if (workspace_root / "global.json").exists():
            return 0.7
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Find test projects
        for csproj in workspace_root.glob("**/*.csproj"):
            if _is_prunable_path(csproj, workspace_root):
                continue
            try:
                content = csproj.read_text()
                # Check if it's a test project
                if (
                    "Microsoft.NET.Test.Sdk" in content
                    or "xunit" in content.lower()
                    or "nunit" in content.lower()
                ):
                    rel = str(csproj.relative_to(workspace_root))
                    targets.append(
                        TestTarget(
                            target_id=f"test:{rel}",
                            selector=rel,
                            kind="project",
                            language="csharp",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
            except OSError:
                log.debug("dotnet_test_discovery_failed", exc_info=True)
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
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["dotnet", "test"]
        else:
            cmd = ["dotnet", "test"]
        cmd.extend(
            [
                target.selector,
                f"--logger:junit;LogFilePath={output_path}",
                "--no-build",  # Assume already built
            ]
        )
        if pattern:
            cmd.extend(["--filter", f"FullyQualifiedName~{pattern}"])
        if tags:
            cmd.extend(["--filter", " | ".join(f"Category={t}" for t in tags)])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="dotnet", errors=1)

# C/C++ - CTest

@runner_registry.register
class CTestPack(RunnerPack):
    """C/C++ CTest runner."""
    pack_id = "cpp.ctest"
    language = "cpp"
    runner_name = "ctest"
    markers = (
        MarkerRule("CMakeLists.txt", content_match="enable_testing", confidence="high"),
        MarkerRule("CMakeLists.txt", confidence="medium"),
    )
    output_strategy = OutputStrategy(format="coarse", file_based=False)  # CTest output is limited
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Via -L (labels)
        supports_parallel=True,
        supports_junit_output=False,  # Limited support
    )
    def detect(self, workspace_root: Path) -> float:
        cmake = workspace_root / "CMakeLists.txt"
        if cmake.exists():
            try:
                content = cmake.read_text()
                if "enable_testing" in content or "add_test" in content:
                    return 1.0
                return 0.5
            except OSError:
                log.debug("cmake_config_read_failed", exc_info=True)
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        cmake = workspace_root / "CMakeLists.txt"
        if cmake.exists():
            targets.append(
                TestTarget(
                    target_id="test:.",
                    selector=".",
                    kind="project",
                    language="cpp",
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
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - CTest doesn't use execution context
    ) -> list[str]:
        build_dir = Path(target.workspace_root) / "build"
        cmd = ["ctest", "--test-dir", str(build_dir), "--output-on-failure"]
        if pattern:
            cmd.extend(["-R", pattern])
        if tags:
            cmd.extend(["-L", "|".join(tags)])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        # Parse coarse CTest output
        lines = stdout.split("\n")
        passed = 0
        failed = 0
        for line in lines:
            if "tests passed" in line.lower():
                try:
                    # e.g., "100% tests passed, 0 tests failed out of 10"
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == "passed,":
                            passed = int(parts[i - 1])
                        if part == "failed":
                            failed = int(parts[i - 1])
                except (ValueError, IndexError):
                    log.debug("ctest_output_parse_failed", exc_info=True)
        return ParsedTestSuite(
            name="ctest",
            total=passed + failed,
            passed=passed,
            failed=failed,
        )

# Ruby - RSpec

@runner_registry.register
class RSpecPack(RunnerPack):
    """Ruby RSpec runner."""
    pack_id = "ruby.rspec"
    language = "ruby"
    runner_name = "rspec"
    markers = (
        MarkerRule(".rspec", confidence="high"),
        MarkerRule("spec/spec_helper.rb", confidence="high"),
        MarkerRule("Gemfile", content_match="rspec", confidence="medium"),
    )
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="rspec.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,  # Via rspec_junit_formatter
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / ".rspec").exists():
            return 1.0
        if (workspace_root / "spec" / "spec_helper.rb").exists():
            return 0.9
        gemfile = workspace_root / "Gemfile"
        if gemfile.exists():
            try:
                if "rspec" in gemfile.read_text():
                    return 0.8
            except OSError:
                log.debug("rspec_config_read_failed", exc_info=True)
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        from coderecon.testing.packs import _discover_by_glob
        return _discover_by_glob(self, workspace_root, "spec/**/*_spec.rb", "ruby")
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
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
                cmd = ["bundle", "exec", "rspec"]
        else:
            cmd = ["bundle", "exec", "rspec"]
        cmd.extend(
            [
                target.selector,
                "--format",
                "RspecJunitFormatter",
                "--out",
                str(output_path),
            ]
        )
        if pattern:
            cmd.extend(["--example", pattern])
        if tags:
            for tag in tags:
                cmd.extend(["--tag", tag])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="rspec", errors=1)
@runner_registry.register
class MinitestPack(RunnerPack):
    """Ruby Minitest runner."""
    pack_id = "ruby.minitest"
    language = "ruby"
    runner_name = "minitest"
    markers = (
        MarkerRule("Rakefile", content_match="Rake::TestTask", confidence="high"),
        MarkerRule("Gemfile", content_match="minitest", confidence="medium"),
        MarkerRule("test/test_helper.rb", confidence="high"),
    )
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="minitest.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=False,
        supports_junit_output=True,  # Via minitest-reporters
    )
    def detect(self, workspace_root: Path) -> float:
        # High confidence: Rakefile with Rake::TestTask
        rakefile = workspace_root / "Rakefile"
        if rakefile.exists():
            try:
                content = rakefile.read_text()
                if "Rake::TestTask" in content:
                    return 0.9
            except OSError:
                log.debug("minitest_config_read_failed", path="Rakefile", exc_info=True)
        # Medium confidence: test_helper.rb exists
        if (workspace_root / "test" / "test_helper.rb").exists():
            return 0.7
        # Medium: Gemfile mentions minitest
        gemfile = workspace_root / "Gemfile"
        if gemfile.exists():
            try:
                if "minitest" in gemfile.read_text():
                    return 0.6
            except OSError:
                log.debug("minitest_config_read_failed", path="Gemfile", exc_info=True)
        # Low: test/ directory with *_test.rb or spec_*.rb files
        test_dir = workspace_root / "test"
        if test_dir.is_dir():
            for p in test_dir.iterdir():
                if p.suffix == ".rb" and (p.stem.endswith("_test") or p.stem.startswith("spec_")):
                    return 0.5
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        test_dir = workspace_root / "test"
        if not test_dir.is_dir():
            return targets
        for path in test_dir.rglob("*.rb"):
            if _is_prunable_path(path, workspace_root):
                continue
            stem = path.stem
            if stem.endswith("_test") or stem.startswith("spec_"):
                rel = str(path.relative_to(workspace_root))
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language="ruby",
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
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["bundle", "exec", "ruby"]
        else:
            cmd = ["bundle", "exec", "ruby"]
        cmd.append(target.selector)
        if pattern:
            cmd.extend(["-n", pattern])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="minitest", errors=1)

# PHP - PHPUnit

@runner_registry.register
class PHPUnitPack(RunnerPack):
    """PHP PHPUnit runner."""
    pack_id = "php.phpunit"
    language = "php"
    runner_name = "phpunit"
    markers = (
        MarkerRule("phpunit.xml", confidence="high"),
        MarkerRule("phpunit.xml.dist", confidence="high"),
        MarkerRule("composer.json", content_match="phpunit", confidence="medium"),
    )
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Via --group
        supports_parallel=True,
        supports_junit_output=True,
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "phpunit.xml").exists():
            return 1.0
        if (workspace_root / "phpunit.xml.dist").exists():
            return 0.95
        composer = workspace_root / "composer.json"
        if composer.exists():
            try:
                data = json.loads(composer.read_text())
                deps = {**data.get("require", {}), **data.get("require-dev", {})}
                if "phpunit/phpunit" in deps:
                    return 0.8
            except (OSError, json.JSONDecodeError):
                log.debug("phpunit_config_read_failed", exc_info=True)
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        from coderecon.testing.packs import _discover_by_glob
        return _discover_by_glob(self, workspace_root, "tests/**/*Test.php", "php")
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - PHPUnit doesn't use execution context currently
    ) -> list[str]:
        cmd = ["./vendor/bin/phpunit", target.selector, f"--log-junit={output_path}"]
        if pattern:
            cmd.extend(["--filter", pattern])
        if tags:
            cmd.extend(["--group", ",".join(tags)])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="phpunit", errors=1)
