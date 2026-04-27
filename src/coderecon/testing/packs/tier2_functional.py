"""Runner packs for functional/compiled languages (Tier 2).

Cabal (Haskell), Julia Pkg.test, Dune (OCaml).
"""

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

from coderecon.testing.packs import _is_prunable_path
from coderecon.config.constants import MS_PER_SEC

log = structlog.get_logger(__name__)

# Kotlin - Gradle with Kotlin DSL


@runner_registry.register
class CabalTestPack(RunnerPack):
    """Haskell cabal test runner."""
    pack_id = "haskell.cabal_test"
    language = "haskell"
    runner_name = "cabal_test"
    markers = [
        MarkerRule("*.cabal", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="coarse", file_based=False)
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=False,
        supports_tag_filter=False,
        supports_parallel=False,
        supports_junit_output=False,
    )
    def detect(self, workspace_root: Path) -> float:
        cabal_files = list(workspace_root.glob("*.cabal"))
        if cabal_files:
            # Check for test-suite stanza
            for cf in cabal_files:
                try:
                    content = cf.read_text()
                    if "test-suite" in content.lower():
                        return 1.0
                except OSError:
                    log.debug("haskell_config_read_failed", exc_info=True)
            return 0.5  # Cabal file exists but no test-suite stanza
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Project-level target for cabal test
        return [
            TestTarget(
                target_id="test:.",
                selector=".",
                kind="project",
                language="haskell",
                runner_pack_id=self.pack_id,
                workspace_root=str(workspace_root),
            )
        ]
    def build_command(
        self,
        target: TestTarget,  # noqa: ARG002
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,  # noqa: ARG002
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002
    ) -> list[str]:
        return ["cabal", "test", "--test-show-details=streaming"]
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        import re
        suite = ParsedTestSuite(name="cabal_test")
        # Cabal test output formats:
        # - "Test Cases: 10  Tried: 10  Errors: 0  Failures: 2"
        # - "Cases: 15  Tried: 15  Errors: 0  Failures: 3"
        # - "1 of 1 test suites (1 of 1 test cases) passed."
        # - "Running X test suites..." then "PASS" or "FAIL" per suite
        for line in stdout.splitlines():
            # Parse "Cases: N  Tried: N  Errors: N  Failures: N" format
            cases_match = re.search(
                r"(?:Test\s+)?Cases:\s*(\d+).*?Failures:\s*(\d+)", line, re.IGNORECASE
            )
            if cases_match:
                suite.total = int(cases_match.group(1))
                suite.failed = int(cases_match.group(2))
                continue
            # Parse "X of Y test suites (A of B test cases) passed" format
            suites_match = re.search(
                r"(\d+)\s+of\s+(\d+)\s+test\s+(?:suites|cases).*passed", line, re.IGNORECASE
            )
            if suites_match:
                passed = int(suites_match.group(1))
                total = int(suites_match.group(2))
                suite.total = total
                suite.failed = total - passed
        # Fallback: if no counts found but pass/fail detected
        if suite.total == 0:
            passed_suites = stdout.lower().count("test suite") and "pass" in stdout.lower()
            failed_suites = "fail" in stdout.lower()
            if passed_suites or failed_suites:
                suite.total = 1
                suite.failed = 1 if failed_suites else 0
        return suite

# Julia - Pkg.test (Julia standard package testing)

@runner_registry.register
class JuliaPkgTestPack(RunnerPack):
    """Julia Pkg.test() runner."""
    pack_id = "julia.pkg_test"
    language = "julia"
    runner_name = "pkg_test"
    markers = [
        MarkerRule("Project.toml", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="coarse", file_based=False)
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=False,
        supports_tag_filter=False,
        supports_parallel=False,
        supports_junit_output=False,
    )
    def detect(self, workspace_root: Path) -> float:
        project_toml = workspace_root / "Project.toml"
        if project_toml.exists():
            test_dir = workspace_root / "test"
            if test_dir.is_dir() and (test_dir / "runtests.jl").exists():
                return 1.0
            return 0.5
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Project-level target
        return [
            TestTarget(
                target_id="test:.",
                selector=".",
                kind="project",
                language="julia",
                runner_pack_id=self.pack_id,
                workspace_root=str(workspace_root),
            )
        ]
    def build_command(
        self,
        target: TestTarget,  # noqa: ARG002
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,  # noqa: ARG002
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002
    ) -> list[str]:
        return ["julia", "--project=.", "-e", "using Pkg; Pkg.test()"]
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        import re
        suite = ParsedTestSuite(name="pkg_test")
        # Julia Test Summary format:
        # "Test Summary: | Pass  Fail  Total"
        # "  MyModule    |    5     2      7"
        # Look for summary lines with numeric columns
        for line in stdout.splitlines():
            # Parse totals from summary table rows (lines after "Test Summary:")
            # Format: "  name  |  pass  fail  total" where numbers are space-separated
            if "|" in line and re.search(r"\d", line):
                # Extract all numbers from the line after the pipe
                parts = line.split("|")
                if len(parts) >= 2:
                    nums = re.findall(r"\d+", parts[1])
                    # Expect [pass, fail, total] or [pass, total] or [total]
                    if len(nums) >= 3:
                        # pass, fail, total format
                        suite.total = int(nums[-1])  # last is total
                        suite.failed = int(nums[-2])  # second-to-last is fail
                    elif len(nums) == 2:
                        suite.total = int(nums[1])
                        suite.failed = 0
        # Fallback: detect pass/fail from text if no table found
        if suite.total == 0:
            if "testing" in stdout.lower() and "passed" in stdout.lower():
                suite.total = 1
                suite.failed = 0
            elif "fail" in stdout.lower() or "error" in stdout.lower():
                suite.total = 1
                suite.failed = 1
        return suite

# OCaml - Dune Test

@runner_registry.register
class DuneTestPack(RunnerPack):
    """OCaml Dune test runner."""
    pack_id = "ocaml.dune_test"
    language = "ocaml"
    runner_name = "dune_test"
    markers = [
        MarkerRule("dune-project", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="coarse", file_based=False)
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=False,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=False,
    )
    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "dune-project").exists():
            # Check for test directory or inline_tests
            test_dir = workspace_root / "test"
            if test_dir.is_dir():
                return 1.0
            # Check for inline_tests in dune files
            for dune_file in workspace_root.rglob("dune"):
                try:
                    content = dune_file.read_text()
                    if "inline_tests" in content or "(test" in content:
                        return 0.9
                except OSError:
                    log.debug("ocaml_config_read_failed", exc_info=True)
            return 0.5
        return 0.0
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Project-level target for dune test
        return [
            TestTarget(
                target_id="test:.",
                selector=".",
                kind="project",
                language="ocaml",
                runner_pack_id=self.pack_id,
                workspace_root=str(workspace_root),
            )
        ]
    def build_command(
        self,
        target: TestTarget,  # noqa: ARG002
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,  # noqa: ARG002
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002
    ) -> list[str]:
        return ["dune", "test"]
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        import re
        suite = ParsedTestSuite(name="dune_test")
        # Dune outputs "PASS" or "FAIL" as whole words in test output.
        # Use word-boundary regex to avoid matching substrings like "passed", "password".
        pass_count = len(re.findall(r"\bPASS\b", stdout, re.IGNORECASE))
        fail_count = len(re.findall(r"\bFAIL\b", stdout, re.IGNORECASE))
        if pass_count > 0 or fail_count > 0:
            suite.total = pass_count + fail_count
            suite.failed = fail_count
        return suite
