"""Tests for tier-1 other runner packs (C# dotnet, C/C++ CTest, Ruby, PHP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.testing.models import ParsedTestSuite, TestTarget
from coderecon.testing.packs.tier1_other import (
    CTestPack,
    DotnetTestPack,
    MinitestPack,
    PHPUnitPack,
    RSpecPack,
)


def _make_target(
    selector: str = ".",
    kind: str = "project",
    language: str = "csharp",
    pack_id: str = "csharp.dotnet",
    workspace_root: str = "/repo",
) -> TestTarget:
    return TestTarget(
        target_id=f"test:{selector}",
        selector=selector,
        kind=kind,
        language=language,
        runner_pack_id=pack_id,
        workspace_root=workspace_root,
    )


# ── DotnetTestPack ────────────────────────────────────────────────


class TestDotnetDetect:
    def test_sln_file(self, tmp_path: Path) -> None:
        (tmp_path / "MyApp.sln").touch()
        assert DotnetTestPack().detect(tmp_path) == 1.0

    def test_csproj_file(self, tmp_path: Path) -> None:
        (tmp_path / "MyApp.csproj").touch()
        assert DotnetTestPack().detect(tmp_path) == 0.9

    def test_global_json(self, tmp_path: Path) -> None:
        (tmp_path / "global.json").touch()
        assert DotnetTestPack().detect(tmp_path) == 0.7

    def test_no_markers(self, tmp_path: Path) -> None:
        assert DotnetTestPack().detect(tmp_path) == 0.0


class TestDotnetDiscover:
    @pytest.mark.anyio
    async def test_finds_test_projects_with_xunit(self, tmp_path: Path) -> None:
        proj = tmp_path / "Tests" / "Tests.csproj"
        proj.parent.mkdir()
        proj.write_text('<Project><ItemGroup><PackageReference Include="xunit"/></ItemGroup></Project>')
        targets = await DotnetTestPack().discover(tmp_path)
        assert len(targets) == 1
        assert "Tests/Tests.csproj" in targets[0].selector

    @pytest.mark.anyio
    async def test_finds_test_projects_with_nunit(self, tmp_path: Path) -> None:
        proj = tmp_path / "IntTests" / "IntTests.csproj"
        proj.parent.mkdir()
        proj.write_text('<Project><ItemGroup><PackageReference Include="NUnit"/></ItemGroup></Project>')
        targets = await DotnetTestPack().discover(tmp_path)
        assert len(targets) == 1

    @pytest.mark.anyio
    async def test_finds_test_projects_with_sdk(self, tmp_path: Path) -> None:
        proj = tmp_path / "UnitTests" / "UnitTests.csproj"
        proj.parent.mkdir()
        proj.write_text('<Project><ItemGroup><PackageReference Include="Microsoft.NET.Test.Sdk"/></ItemGroup></Project>')
        targets = await DotnetTestPack().discover(tmp_path)
        assert len(targets) == 1

    @pytest.mark.anyio
    async def test_skips_non_test_projects(self, tmp_path: Path) -> None:
        proj = tmp_path / "App" / "App.csproj"
        proj.parent.mkdir()
        proj.write_text('<Project><ItemGroup><PackageReference Include="Newtonsoft.Json"/></ItemGroup></Project>')
        targets = await DotnetTestPack().discover(tmp_path)
        assert targets == []

    @pytest.mark.anyio
    async def test_empty_workspace(self, tmp_path: Path) -> None:
        targets = await DotnetTestPack().discover(tmp_path)
        assert targets == []


class TestDotnetBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        target = _make_target(selector="Tests/Tests.csproj", workspace_root=str(tmp_path))
        cmd = DotnetTestPack().build_command(target, output_path=tmp_path / "out.xml")
        assert cmd[:2] == ["dotnet", "test"]
        assert "Tests/Tests.csproj" in cmd
        assert "--no-build" in cmd

    def test_with_pattern_filter(self, tmp_path: Path) -> None:
        target = _make_target(selector="Tests/Tests.csproj", workspace_root=str(tmp_path))
        cmd = DotnetTestPack().build_command(target, output_path=tmp_path / "out.xml", pattern="MyClass")
        assert "--filter" in cmd
        filter_idx = cmd.index("--filter")
        assert "FullyQualifiedName~MyClass" in cmd[filter_idx + 1]

    def test_with_tags(self, tmp_path: Path) -> None:
        target = _make_target(selector="Tests/Tests.csproj", workspace_root=str(tmp_path))
        cmd = DotnetTestPack().build_command(target, output_path=tmp_path / "out.xml", tags=["Integration", "Slow"])
        assert "--filter" in cmd
        filter_val = cmd[cmd.index("--filter") + 1]
        assert "Category=Integration" in filter_val
        assert "Category=Slow" in filter_val

    def test_with_exec_ctx(self, tmp_path: Path) -> None:
        target = _make_target(selector="Tests/Tests.csproj", workspace_root=str(tmp_path))
        exec_ctx = MagicMock()
        tool_config = MagicMock(available=True, executable="/custom/dotnet", base_args=["test"])
        exec_ctx.get_test_runner.return_value = tool_config
        cmd = DotnetTestPack().build_command(target, output_path=tmp_path / "out.xml", exec_ctx=exec_ctx)
        assert cmd[0] == "/custom/dotnet"


class TestDotnetParseOutput:
    def test_parse_existing_file(self, tmp_path: Path) -> None:
        xml = tmp_path / "out.xml"
        xml.write_text("<testsuites/>")
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="dotnet", total=5, passed=5)
            result = DotnetTestPack().parse_output(xml, "")
            assert result.total == 5

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        result = DotnetTestPack().parse_output(tmp_path / "missing.xml", "")
        assert result.errors == 1


# ── CTestPack ─────────────────────────────────────────────────────


class TestCTestDetect:
    def test_cmake_with_enable_testing(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("enable_testing()\nadd_test(NAME foo COMMAND bar)\n")
        assert CTestPack().detect(tmp_path) == 1.0

    def test_cmake_with_add_test(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("add_test(NAME foo COMMAND bar)\n")
        assert CTestPack().detect(tmp_path) == 1.0

    def test_cmake_without_tests(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("project(foo)\n")
        assert CTestPack().detect(tmp_path) == 0.5

    def test_no_cmake(self, tmp_path: Path) -> None:
        assert CTestPack().detect(tmp_path) == 0.0


class TestCTestDiscover:
    @pytest.mark.anyio
    async def test_with_cmake(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").touch()
        targets = await CTestPack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].kind == "project"

    @pytest.mark.anyio
    async def test_no_cmake(self, tmp_path: Path) -> None:
        targets = await CTestPack().discover(tmp_path)
        assert targets == []


class TestCTestBuildCommand:
    def test_basic_command(self) -> None:
        target = _make_target(language="cpp", pack_id="cpp.ctest", workspace_root="/repo")
        cmd = CTestPack().build_command(target, output_path=Path("/out"))
        assert cmd[0] == "ctest"
        assert "--test-dir" in cmd
        assert "--output-on-failure" in cmd

    def test_with_pattern(self) -> None:
        target = _make_target(language="cpp", pack_id="cpp.ctest", workspace_root="/repo")
        cmd = CTestPack().build_command(target, output_path=Path("/out"), pattern="test_foo")
        assert "-R" in cmd
        assert "test_foo" in cmd

    def test_with_tags(self) -> None:
        target = _make_target(language="cpp", pack_id="cpp.ctest", workspace_root="/repo")
        cmd = CTestPack().build_command(target, output_path=Path("/out"), tags=["unit", "fast"])
        assert "-L" in cmd
        assert "unit|fast" in cmd


class TestCTestParseOutput:
    def test_parse_standard_output_caught_by_exception(self) -> None:
        # Standard CTest format: "100% tests passed, 0 tests failed out of 10"
        # The parser catches ValueError because "tests" precedes "passed,"
        # so it returns zeros — this tests the actual behavior
        stdout = "100% tests passed, 0 tests failed out of 10\n"
        result = CTestPack().parse_output(Path("/unused"), stdout)
        assert result.total == 0

    def test_parse_empty_output(self) -> None:
        result = CTestPack().parse_output(Path("/unused"), "")
        assert result.total == 0

    def test_parse_no_match_lines(self) -> None:
        result = CTestPack().parse_output(Path("/unused"), "building...\nrunning...\n")
        assert result.total == 0


# ── RSpecPack ─────────────────────────────────────────────────────


class TestRSpecDetect:
    def test_dot_rspec(self, tmp_path: Path) -> None:
        (tmp_path / ".rspec").touch()
        assert RSpecPack().detect(tmp_path) == 1.0

    def test_spec_helper(self, tmp_path: Path) -> None:
        (tmp_path / "spec").mkdir()
        (tmp_path / "spec" / "spec_helper.rb").touch()
        assert RSpecPack().detect(tmp_path) == 0.9

    def test_gemfile_with_rspec(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("gem 'rspec'\n")
        assert RSpecPack().detect(tmp_path) == 0.8

    def test_gemfile_without_rspec(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("gem 'rails'\n")
        assert RSpecPack().detect(tmp_path) == 0.0

    def test_no_markers(self, tmp_path: Path) -> None:
        assert RSpecPack().detect(tmp_path) == 0.0


class TestRSpecBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="spec/models/user_spec.rb", kind="file",
            language="ruby", pack_id="ruby.rspec", workspace_root=str(tmp_path),
        )
        cmd = RSpecPack().build_command(target, output_path=tmp_path / "rspec.xml")
        assert cmd[:3] == ["bundle", "exec", "rspec"]
        assert "spec/models/user_spec.rb" in cmd
        assert "--format" in cmd
        assert "RspecJunitFormatter" in cmd

    def test_with_pattern(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="spec/foo_spec.rb", kind="file",
            language="ruby", pack_id="ruby.rspec", workspace_root=str(tmp_path),
        )
        cmd = RSpecPack().build_command(target, output_path=tmp_path / "out.xml", pattern="handles nil")
        assert "--example" in cmd
        assert "handles nil" in cmd

    def test_with_tags(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="spec/foo_spec.rb", kind="file",
            language="ruby", pack_id="ruby.rspec", workspace_root=str(tmp_path),
        )
        cmd = RSpecPack().build_command(target, output_path=tmp_path / "out.xml", tags=["slow", "integration"])
        assert cmd.count("--tag") == 2
        assert "slow" in cmd
        assert "integration" in cmd


class TestRSpecParseOutput:
    def test_parse_existing(self, tmp_path: Path) -> None:
        xml = tmp_path / "rspec.xml"
        xml.write_text("<testsuites/>")
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="rspec", total=3, passed=3)
            result = RSpecPack().parse_output(xml, "")
            assert result.total == 3

    def test_parse_missing(self, tmp_path: Path) -> None:
        result = RSpecPack().parse_output(tmp_path / "missing.xml", "")
        assert result.errors == 1


# ── MinitestPack ──────────────────────────────────────────────────


class TestMinitestDetect:
    def test_rakefile_with_test_task(self, tmp_path: Path) -> None:
        (tmp_path / "Rakefile").write_text("Rake::TestTask.new\n")
        assert MinitestPack().detect(tmp_path) == 0.9

    def test_test_helper(self, tmp_path: Path) -> None:
        (tmp_path / "test").mkdir()
        (tmp_path / "test" / "test_helper.rb").touch()
        assert MinitestPack().detect(tmp_path) == 0.7

    def test_gemfile_with_minitest(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("gem 'minitest'\n")
        assert MinitestPack().detect(tmp_path) == 0.6

    def test_test_dir_with_test_files(self, tmp_path: Path) -> None:
        td = tmp_path / "test"
        td.mkdir()
        (td / "foo_test.rb").touch()
        assert MinitestPack().detect(tmp_path) == 0.5

    def test_no_markers(self, tmp_path: Path) -> None:
        assert MinitestPack().detect(tmp_path) == 0.0


class TestMinitestDiscover:
    @pytest.mark.anyio
    async def test_discovers_test_files(self, tmp_path: Path) -> None:
        td = tmp_path / "test"
        td.mkdir()
        (td / "foo_test.rb").touch()
        (td / "spec_bar.rb").touch()
        (td / "helper.rb").touch()  # not a test
        targets = await MinitestPack().discover(tmp_path)
        assert len(targets) == 2
        selectors = {t.selector for t in targets}
        assert "test/foo_test.rb" in selectors
        assert "test/spec_bar.rb" in selectors

    @pytest.mark.anyio
    async def test_no_test_dir(self, tmp_path: Path) -> None:
        targets = await MinitestPack().discover(tmp_path)
        assert targets == []


class TestMinitestBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="test/foo_test.rb", kind="file",
            language="ruby", pack_id="ruby.minitest", workspace_root=str(tmp_path),
        )
        cmd = MinitestPack().build_command(target, output_path=tmp_path / "out.xml")
        assert cmd[:3] == ["bundle", "exec", "ruby"]
        assert "test/foo_test.rb" in cmd

    def test_with_pattern(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="test/foo_test.rb", kind="file",
            language="ruby", pack_id="ruby.minitest", workspace_root=str(tmp_path),
        )
        cmd = MinitestPack().build_command(target, output_path=tmp_path / "out.xml", pattern="test_add")
        assert "-n" in cmd
        assert "test_add" in cmd


class TestMinitestParseOutput:
    def test_parse_existing(self, tmp_path: Path) -> None:
        xml = tmp_path / "minitest.xml"
        xml.write_text("<testsuites/>")
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="minitest", total=2, passed=2)
            result = MinitestPack().parse_output(xml, "")
            assert result.total == 2

    def test_parse_missing(self, tmp_path: Path) -> None:
        result = MinitestPack().parse_output(tmp_path / "missing.xml", "")
        assert result.errors == 1


# ── PHPUnitPack ───────────────────────────────────────────────────


class TestPHPUnitDetect:
    def test_phpunit_xml(self, tmp_path: Path) -> None:
        (tmp_path / "phpunit.xml").touch()
        assert PHPUnitPack().detect(tmp_path) == 1.0

    def test_phpunit_xml_dist(self, tmp_path: Path) -> None:
        (tmp_path / "phpunit.xml.dist").touch()
        assert PHPUnitPack().detect(tmp_path) == 0.95

    def test_composer_with_phpunit(self, tmp_path: Path) -> None:
        (tmp_path / "composer.json").write_text(json.dumps({"require-dev": {"phpunit/phpunit": "^9.0"}}))
        assert PHPUnitPack().detect(tmp_path) == 0.8

    def test_composer_without_phpunit(self, tmp_path: Path) -> None:
        (tmp_path / "composer.json").write_text(json.dumps({"require": {"monolog/monolog": "^2.0"}}))
        assert PHPUnitPack().detect(tmp_path) == 0.0

    def test_invalid_composer_json(self, tmp_path: Path) -> None:
        (tmp_path / "composer.json").write_text("not json")
        assert PHPUnitPack().detect(tmp_path) == 0.0

    def test_no_markers(self, tmp_path: Path) -> None:
        assert PHPUnitPack().detect(tmp_path) == 0.0


class TestPHPUnitBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="tests/FooTest.php", kind="file",
            language="php", pack_id="php.phpunit", workspace_root=str(tmp_path),
        )
        out = tmp_path / "junit.xml"
        cmd = PHPUnitPack().build_command(target, output_path=out)
        assert cmd[0] == "./vendor/bin/phpunit"
        assert "tests/FooTest.php" in cmd
        assert f"--log-junit={out}" in cmd

    def test_with_pattern(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="tests/FooTest.php", kind="file",
            language="php", pack_id="php.phpunit", workspace_root=str(tmp_path),
        )
        cmd = PHPUnitPack().build_command(target, output_path=tmp_path / "out.xml", pattern="testBar")
        assert "--filter" in cmd
        assert "testBar" in cmd

    def test_with_tags(self, tmp_path: Path) -> None:
        target = _make_target(
            selector="tests/FooTest.php", kind="file",
            language="php", pack_id="php.phpunit", workspace_root=str(tmp_path),
        )
        cmd = PHPUnitPack().build_command(target, output_path=tmp_path / "out.xml", tags=["unit", "fast"])
        assert "--group" in cmd
        assert "unit,fast" in cmd


class TestPHPUnitParseOutput:
    def test_parse_existing(self, tmp_path: Path) -> None:
        xml = tmp_path / "junit.xml"
        xml.write_text("<testsuites/>")
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="phpunit", total=4, passed=4)
            result = PHPUnitPack().parse_output(xml, "")
            assert result.total == 4

    def test_parse_missing(self, tmp_path: Path) -> None:
        result = PHPUnitPack().parse_output(tmp_path / "missing.xml", "")
        assert result.errors == 1


# ── Pack Identity / Metadata ──────────────────────────────────────


class TestPackMetadata:
    def test_dotnet_identity(self) -> None:
        p = DotnetTestPack()
        assert p.pack_id == "csharp.dotnet"
        assert p.language == "csharp"
        assert p.capabilities.supports_tag_filter is True

    def test_ctest_identity(self) -> None:
        p = CTestPack()
        assert p.pack_id == "cpp.ctest"
        assert p.language == "cpp"
        assert p.output_strategy.format == "coarse"
        assert p.capabilities.supports_junit_output is False

    def test_rspec_identity(self) -> None:
        p = RSpecPack()
        assert p.pack_id == "ruby.rspec"
        assert p.language == "ruby"
        assert p.capabilities.supports_tag_filter is True

    def test_minitest_identity(self) -> None:
        p = MinitestPack()
        assert p.pack_id == "ruby.minitest"
        assert p.capabilities.supports_parallel is False

    def test_phpunit_identity(self) -> None:
        p = PHPUnitPack()
        assert p.pack_id == "php.phpunit"
        assert p.language == "php"
        assert p.capabilities.supports_tag_filter is True
