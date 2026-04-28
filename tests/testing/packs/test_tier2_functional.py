"""Tests for tier-2 functional language runner packs (Cabal, Julia, Dune)."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.testing.models import TestTarget
from coderecon.testing.packs.tier2_functional import (
    CabalTestPack,
    DuneTestPack,
    JuliaPkgTestPack,
)


# ── CabalTestPack ────────────────────────────────────────────────


class TestCabalTestPackDetect:
    def test_detect_cabal_with_test_suite(self, tmp_path: Path) -> None:
        (tmp_path / "mylib.cabal").write_text(
            "name: mylib\ntest-suite mylib-test\n  type: exitcode-stdio-1.0\n"
        )
        assert CabalTestPack().detect(tmp_path) == 1.0

    def test_detect_cabal_without_test_suite(self, tmp_path: Path) -> None:
        (tmp_path / "mylib.cabal").write_text("name: mylib\nlibrary\n")
        assert CabalTestPack().detect(tmp_path) == 0.5

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert CabalTestPack().detect(tmp_path) == 0.0


class TestCabalTestPackBuildCommand:
    def test_build_command(self) -> None:
        pack = CabalTestPack()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="haskell",
            runner_pack_id="haskell.cabal_test",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out"))
        assert cmd == ["cabal", "test", "--test-show-details=streaming"]


class TestCabalTestPackDiscover:
    @pytest.mark.asyncio
    async def test_discover_returns_project_target(self, tmp_path: Path) -> None:
        targets = await CabalTestPack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].kind == "project"
        assert targets[0].language == "haskell"


class TestCabalTestPackParseOutput:
    def test_parse_cases_format(self) -> None:
        stdout = "Test Cases: 10  Tried: 10  Errors: 0  Failures: 2\n"
        result = CabalTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 10
        assert result.failed == 2

    def test_parse_suites_passed_format(self) -> None:
        # Regex matches first "N of M test suites|cases" pattern
        stdout = "1 of 1 test suites (5 of 7 test cases) passed.\n"
        result = CabalTestPack().parse_output(Path("/x"), stdout)
        # First match: "1 of 1 test suites" → total=1, failed=0
        assert result.total == 1
        assert result.failed == 0

    def test_parse_test_cases_passed_format(self) -> None:
        stdout = "5 of 7 test cases passed.\n"
        result = CabalTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 7
        assert result.failed == 2

    def test_parse_empty_output(self) -> None:
        result = CabalTestPack().parse_output(Path("/x"), "")
        assert result.total == 0

    def test_parse_pass_fail_fallback(self) -> None:
        stdout = "Running test suite...\nTest suite FAIL\n"
        result = CabalTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 1
        assert result.failed == 1

    def test_parse_pass_only_fallback(self) -> None:
        stdout = "Running test suite...\nTest suite passed.\n"
        result = CabalTestPack().parse_output(Path("/x"), stdout)
        # "pass" is in stdout and "test suite" is present
        assert result.total == 1
        assert result.failed == 0


# ── JuliaPkgTestPack ─────────────────────────────────────────────


class TestJuliaPkgTestPackDetect:
    def test_detect_with_runtests(self, tmp_path: Path) -> None:
        (tmp_path / "Project.toml").touch()
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        (test_dir / "runtests.jl").touch()
        assert JuliaPkgTestPack().detect(tmp_path) == 1.0

    def test_detect_project_toml_only(self, tmp_path: Path) -> None:
        (tmp_path / "Project.toml").touch()
        assert JuliaPkgTestPack().detect(tmp_path) == 0.5

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert JuliaPkgTestPack().detect(tmp_path) == 0.0


class TestJuliaPkgTestPackBuildCommand:
    def test_build_command(self) -> None:
        pack = JuliaPkgTestPack()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="julia",
            runner_pack_id="julia.pkg_test",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out"))
        assert cmd == ["julia", "--project=.", "-e", "using Pkg; Pkg.test()"]


class TestJuliaPkgTestPackDiscover:
    @pytest.mark.asyncio
    async def test_discover_returns_project_target(self, tmp_path: Path) -> None:
        targets = await JuliaPkgTestPack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].kind == "project"
        assert targets[0].language == "julia"


class TestJuliaPkgTestPackParseOutput:
    def test_parse_summary_table(self) -> None:
        stdout = (
            "Test Summary: | Pass  Fail  Total\n"
            "  MyModule    |    5     2      7\n"
        )
        result = JuliaPkgTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 7
        assert result.failed == 2

    def test_parse_pass_only_table(self) -> None:
        stdout = (
            "Test Summary: | Pass  Total\n"
            "  MyModule    |    3      3\n"
        )
        result = JuliaPkgTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 3
        assert result.failed == 0

    def test_parse_fallback_passed(self) -> None:
        stdout = "Testing MyPackage\n  All tests passed.\n"
        result = JuliaPkgTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_parse_fallback_failed(self) -> None:
        stdout = "Testing MyPackage\n  Some tests failed or had errors\n"
        result = JuliaPkgTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 1
        assert result.failed == 1

    def test_parse_empty_output(self) -> None:
        result = JuliaPkgTestPack().parse_output(Path("/x"), "")
        assert result.total == 0


# ── DuneTestPack ──────────────────────────────────────────────────


class TestDuneTestPackDetect:
    def test_detect_dune_project_with_test_dir(self, tmp_path: Path) -> None:
        (tmp_path / "dune-project").touch()
        (tmp_path / "test").mkdir()
        assert DuneTestPack().detect(tmp_path) == 1.0

    def test_detect_dune_project_with_inline_tests(self, tmp_path: Path) -> None:
        (tmp_path / "dune-project").touch()
        dune_file = tmp_path / "lib" / "dune"
        dune_file.parent.mkdir()
        dune_file.write_text("(library (name mylib) (inline_tests))")
        assert DuneTestPack().detect(tmp_path) == 0.9

    def test_detect_dune_project_bare(self, tmp_path: Path) -> None:
        (tmp_path / "dune-project").touch()
        assert DuneTestPack().detect(tmp_path) == 0.5

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert DuneTestPack().detect(tmp_path) == 0.0


class TestDuneTestPackBuildCommand:
    def test_build_command(self) -> None:
        pack = DuneTestPack()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="ocaml",
            runner_pack_id="ocaml.dune_test",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out"))
        assert cmd == ["dune", "test"]


class TestDuneTestPackDiscover:
    @pytest.mark.asyncio
    async def test_discover_returns_project_target(self, tmp_path: Path) -> None:
        targets = await DuneTestPack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].kind == "project"
        assert targets[0].language == "ocaml"


class TestDuneTestPackParseOutput:
    def test_parse_pass_and_fail(self) -> None:
        stdout = "PASS test_add\nPASS test_sub\nFAIL test_mul\n"
        result = DuneTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 3
        assert result.failed == 1

    def test_parse_all_pass(self) -> None:
        stdout = "PASS test_a\nPASS test_b\n"
        result = DuneTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 2
        assert result.failed == 0

    def test_parse_empty_output(self) -> None:
        result = DuneTestPack().parse_output(Path("/x"), "")
        assert result.total == 0

    def test_does_not_match_substrings(self) -> None:
        """Ensure 'passed' or 'password' don't match the PASS pattern."""
        stdout = "All tests passed successfully\npassword is set\n"
        result = DuneTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 0
