"""Runner packs for Tier-1 languages (Rust cargo, Java Maven/Gradle). """

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

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



log = structlog.get_logger(__name__)

@runner_registry.register
class CargoNextestPack(RunnerPack):
    """Rust cargo-nextest runner (preferred for JUnit output)."""
    pack_id = "rust.nextest"
    language = "rust"
    runner_name = "cargo-nextest"
    markers = [
        MarkerRule("Cargo.toml", confidence="high"),
        MarkerRule(".config/nextest.toml", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["package"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,
    )
    def detect(self, workspace_root: Path) -> float:
        if not (workspace_root / "Cargo.toml").exists():
            return 0.0
        # Prefer nextest if available
        if shutil.which("cargo-nextest"):
            return 0.95
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Find workspace members or single package
        cargo_toml = workspace_root / "Cargo.toml"
        if not cargo_toml.exists():
            return targets
        try:
            content = cargo_toml.read_text()
            if "[workspace]" in content:
                # Workspace - find all members
                for member_toml in workspace_root.glob("*/Cargo.toml"):
                    member_dir = member_toml.parent
                    if list(member_dir.glob("**/*_test.rs")) or (member_dir / "tests").exists():
                        rel = str(member_dir.relative_to(workspace_root))
                        targets.append(
                            TestTarget(
                                target_id=f"test:{rel}",
                                selector=rel,
                                kind="package",
                                language="rust",
                                runner_pack_id=self.pack_id,
                                workspace_root=str(workspace_root),
                            )
                        )
                # Root is also a package (has both [workspace] and [package])
                if "[package]" in content and (
                    list(workspace_root.glob("src/**/*_test.rs"))
                    or (workspace_root / "tests").exists()
                ):
                    targets.append(
                        TestTarget(
                            target_id="test:.",
                            selector=".",
                            kind="package",
                            language="rust",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
            else:
                # Single package
                targets.append(
                    TestTarget(
                        target_id="test:.",
                        selector=".",
                        kind="package",
                        language="rust",
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        except OSError:
            log.debug("rust_test_discovery_failed", exc_info=True)
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
                cmd = ["cargo", "nextest", "run"]
        else:
            cmd = ["cargo", "nextest", "run"]
        cmd.extend(
            [
                "--message-format",
                "junit",
                f"--junit-path={output_path}",
            ]
        )
        if target.selector != ".":
            cmd.extend(["-p", target.selector])
        if pattern:
            cmd.extend(["--", pattern])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="cargo-nextest", errors=1)
@runner_registry.register
class CargoTestPack(RunnerPack):
    """Rust cargo test runner (fallback, limited output format)."""
    pack_id = "rust.cargo_test"
    language = "rust"
    runner_name = "cargo test"
    markers = [
        MarkerRule("Cargo.toml", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="coarse", file_based=False
    )  # cargo test has no configurable output
    capabilities = RunnerCapabilities(
        supported_kinds=["package"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=False,  # This is why nextest is preferred
    )
    def detect(self, workspace_root: Path) -> float:
        if not (workspace_root / "Cargo.toml").exists():
            return 0.0
        # Lower priority than nextest
        if shutil.which("cargo-nextest"):
            return 0.5
        return 0.9
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Same as nextest
        nextest = CargoNextestPack()
        targets = await nextest.discover(workspace_root)
        # Update pack_id
        for t in targets:
            t.runner_pack_id = self.pack_id
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
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["cargo", "test"]
        else:
            cmd = ["cargo", "test"]
        if target.selector != ".":
            cmd.extend(["-p", target.selector])
        if pattern:
            cmd.extend(["--", pattern])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        # Parse coarse output - just count pass/fail from stdout
        lines = stdout.split("\n")
        passed = 0
        failed = 0
        for line in lines:
            if "test result:" in line:
                # e.g., "test result: ok. 10 passed; 0 failed;"
                parts = line.split()
                for i, part in enumerate(parts):
                    if "passed" in part and i > 0 and parts[i - 1].isdigit():
                        passed = int(parts[i - 1])
                    if "failed" in part and i > 0 and parts[i - 1].isdigit():
                        failed = int(parts[i - 1])
        return ParsedTestSuite(
            name="cargo test",
            total=passed + failed,
            passed=passed,
            failed=failed,
        )

# Java - Maven Surefire

@runner_registry.register
class MavenSurefirePack(RunnerPack):
    """Java Maven Surefire runner."""
    pack_id = "java.maven"
    language = "java"
    runner_name = "mvn test"
    markers = [
        MarkerRule("pom.xml", confidence="high"),
        MarkerRule("mvnw", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="target/surefire-reports/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project", "file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,  # Native surefire output
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "pom.xml").exists():
            return 1.0
        if (workspace_root / "mvnw").exists():
            return 0.95
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Check for multi-module project
        pom = workspace_root / "pom.xml"
        if not pom.exists():
            return targets
        try:
            content = pom.read_text()
            if "<modules>" in content:
                # Multi-module - find module poms
                for module_pom in workspace_root.glob("*/pom.xml"):
                    module_dir = module_pom.parent
                    if (module_dir / "src" / "test").exists():
                        rel = str(module_dir.relative_to(workspace_root))
                        targets.append(
                            TestTarget(
                                target_id=f"test:{rel}",
                                selector=rel,
                                kind="project",
                                language="java",
                                runner_pack_id=self.pack_id,
                                workspace_root=str(workspace_root),
                            )
                        )
            else:
                # Single project
                if (workspace_root / "src" / "test").exists():
                    targets.append(
                        TestTarget(
                            target_id="test:.",
                            selector=".",
                            kind="project",
                            language="java",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
        except OSError:
            log.debug("java_test_discovery_failed", exc_info=True)
        return targets
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002 - Maven writes to target/surefire-reports
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
                mvn = "./mvnw" if (Path(target.workspace_root) / "mvnw").exists() else "mvn"
                cmd = [mvn, "test"]
        else:
            mvn = "./mvnw" if (Path(target.workspace_root) / "mvnw").exists() else "mvn"
            cmd = [mvn, "test"]
        if target.selector != ".":
            cmd.extend(["-pl", target.selector])
        if pattern:
            cmd.extend(["-Dtest=" + pattern])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        # Surefire writes to target/surefire-reports/*.xml
        reports_dir = output_path.parent / "target" / "surefire-reports"
        if not reports_dir.exists():
            return ParsedTestSuite(name="maven", errors=1)
        all_tests: list[ParsedTestCase] = []
        total_duration = 0.0
        for xml_file in reports_dir.glob("TEST-*.xml"):
            suite = parse_junit_xml(xml_file.read_text())
            all_tests.extend(suite.tests)
            total_duration += suite.duration_seconds
        return ParsedTestSuite(
            name="maven",
            tests=all_tests,
            total=len(all_tests),
            passed=sum(1 for t in all_tests if t.status == "passed"),
            failed=sum(1 for t in all_tests if t.status == "failed"),
            skipped=sum(1 for t in all_tests if t.status == "skipped"),
            errors=sum(1 for t in all_tests if t.status == "error"),
            duration_seconds=total_duration,
        )

# Java - Gradle

@runner_registry.register
class GradlePack(RunnerPack):
    """Java Gradle runner."""
    pack_id = "java.gradle"
    language = "java"
    runner_name = "gradle test"
    markers = [
        MarkerRule("build.gradle", confidence="high"),
        MarkerRule("build.gradle.kts", confidence="high"),
        MarkerRule("gradlew", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="build/test-results/test/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project", "file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "build.gradle").exists():
            return 1.0
        if (workspace_root / "build.gradle.kts").exists():
            return 1.0
        if (workspace_root / "gradlew").exists():
            return 0.95
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Check for multi-project build
        settings = workspace_root / "settings.gradle"
        settings_kts = workspace_root / "settings.gradle.kts"
        subprojects: list[Path] = []
        for settings_file in [settings, settings_kts]:
            if settings_file.exists():
                try:
                    content = settings_file.read_text()
                    if "include" in content:
                        # Has subprojects - look for build files
                        for build in workspace_root.glob("*/build.gradle*"):
                            subprojects.append(build.parent)
                except OSError:
                    log.debug("gradle_subproject_read_failed", exc_info=True)
        if subprojects:
            for project_dir in subprojects:
                if (project_dir / "src" / "test").exists():
                    rel = str(project_dir.relative_to(workspace_root))
                    targets.append(
                        TestTarget(
                            target_id=f"test:{rel}",
                            selector=rel,
                            kind="project",
                            language="java",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
        else:
            # Single project
            if (workspace_root / "src" / "test").exists():
                targets.append(
                    TestTarget(
                        target_id="test:.",
                        selector=".",
                        kind="project",
                        language="java",
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
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
                if target.selector != "." and "test" in tool_config.base_args:
                    # Replace 'test' with ':selector:test'
                    cmd = [tool_config.executable, f":{target.selector}:test"]
            else:
                gradle = (
                    "./gradlew" if (Path(target.workspace_root) / "gradlew").exists() else "gradle"
                )
                cmd = (
                    [gradle, "test"]
                    if target.selector == "."
                    else [gradle, f":{target.selector}:test"]
                )
        else:
            gradle = "./gradlew" if (Path(target.workspace_root) / "gradlew").exists() else "gradle"
            cmd = [gradle, "test"]
            if target.selector != ".":
                cmd = [gradle, f":{target.selector}:test"]
        if pattern:
            cmd.extend(["--tests", pattern])
        return cmd
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from coderecon.testing.parsers import parse_junit_xml
        # Gradle writes to build/test-results/test/*.xml
        reports_dir = output_path.parent / "build" / "test-results" / "test"
        if not reports_dir.exists():
            return ParsedTestSuite(name="gradle", errors=1)
        all_tests: list[ParsedTestCase] = []
        total_duration = 0.0
        for xml_file in reports_dir.glob("TEST-*.xml"):
            suite = parse_junit_xml(xml_file.read_text())
            all_tests.extend(suite.tests)
            total_duration += suite.duration_seconds
        return ParsedTestSuite(
            name="gradle",
            tests=all_tests,
            total=len(all_tests),
            passed=sum(1 for t in all_tests if t.status == "passed"),
            failed=sum(1 for t in all_tests if t.status == "failed"),
            skipped=sum(1 for t in all_tests if t.status == "skipped"),
            errors=sum(1 for t in all_tests if t.status == "error"),
            duration_seconds=total_duration,
        )
