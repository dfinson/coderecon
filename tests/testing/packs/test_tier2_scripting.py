"""Tests for tier-2 scripting language runner packs (Flutter, Bats, Pester, Busted, Mix)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coderecon.testing.models import ParsedTestSuite, TestTarget
from coderecon.testing.packs.tier2_scripting import (
    BatsPack,
    BustedPack,
    FlutterTestPack,
    MixTestPack,
    PesterPack,
)


def _make_target(
    selector: str = "test/widget_test.dart",
    kind: str = "file",
    language: str = "dart",
    pack_id: str = "dart.flutter_test",
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


# ── FlutterTestPack ──────────────────────────────────────────────


class TestFlutterTestPackDetect:
    def test_detect_flutter_pubspec(self, tmp_path: Path) -> None:
        (tmp_path / "pubspec.yaml").write_text("name: myapp\nflutter:\n  uses-material-design: true\n")
        assert FlutterTestPack().detect(tmp_path) == 1.0

    def test_detect_pubspec_without_flutter(self, tmp_path: Path) -> None:
        (tmp_path / "pubspec.yaml").write_text("name: myapp\ndependencies:\n  http: ^1.0.0\n")
        assert FlutterTestPack().detect(tmp_path) == 0.0

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert FlutterTestPack().detect(tmp_path) == 0.0


class TestFlutterTestPackBuildCommand:
    def test_basic_command(self) -> None:
        pack = FlutterTestPack()
        target = _make_target()
        cmd = pack.build_command(target, output_path=Path("/out"))
        assert cmd[:2] == ["flutter", "test"]
        assert "--machine" in cmd
        assert target.selector in cmd

    def test_with_pattern(self) -> None:
        pack = FlutterTestPack()
        target = _make_target()
        cmd = pack.build_command(target, output_path=Path("/out"), pattern="widget")
        assert "--name" in cmd
        assert "widget" in cmd

    def test_with_tags(self) -> None:
        pack = FlutterTestPack()
        target = _make_target()
        cmd = pack.build_command(target, output_path=Path("/out"), tags=["slow", "golden"])
        assert "--tags" in cmd
        assert "slow,golden" in cmd


# ── BatsPack ──────────────────────────────────────────────────────


class TestBatsPackDetect:
    def test_detect_test_dir(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        (test_dir / "setup.bats").touch()
        assert BatsPack().detect(tmp_path) == 1.0

    def test_detect_tests_dir(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "run.bats").touch()
        assert BatsPack().detect(tmp_path) == 1.0

    def test_detect_nested_bats(self, tmp_path: Path) -> None:
        sub = tmp_path / "scripts" / "test"
        sub.mkdir(parents=True)
        (sub / "check.bats").touch()
        assert BatsPack().detect(tmp_path) == 0.7

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert BatsPack().detect(tmp_path) == 0.0


class TestBatsPackBuildCommand:
    def test_basic_command(self) -> None:
        pack = BatsPack()
        target = _make_target(selector="test/foo.bats", language="bash", pack_id="bash.bats")
        cmd = pack.build_command(target, output_path=Path("/out.xml"))
        assert "bats" in cmd
        assert "--formatter" in cmd
        assert "junit" in cmd
        assert "test/foo.bats" in cmd

    def test_with_filter(self) -> None:
        pack = BatsPack()
        target = _make_target(selector="test/foo.bats", language="bash", pack_id="bash.bats")
        cmd = pack.build_command(target, output_path=Path("/out.xml"), pattern="test_setup")
        assert "--filter" in cmd
        assert "test_setup" in cmd


class TestBatsPackParseOutput:
    def test_parse_junit_from_file(self, tmp_path: Path) -> None:
        junit_file = tmp_path / "out.xml"
        junit_file.write_text(
            '<?xml version="1.0"?>'
            "<testsuites><testsuite name='bats' tests='2' failures='0'>"
            "<testcase name='test1'/><testcase name='test2'/>"
            "</testsuite></testsuites>"
        )
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="bats", total=2, passed=2)
            result = BatsPack().parse_output(junit_file, "")
            mock_parse.assert_called_once()
            assert result.total == 2

    def test_parse_fallback_to_tap(self) -> None:
        tap_output = "ok 1 test one\nnot ok 2 test two\n1..2\n"
        with patch("coderecon.testing.parsers.parse_tap") as mock_tap:
            mock_tap.return_value = ParsedTestSuite(name="bats", total=2, passed=1, failed=1)
            result = BatsPack().parse_output(Path("/nonexistent"), tap_output)
            mock_tap.assert_called_once_with(tap_output)
            assert result.total == 2


# ── PesterPack ────────────────────────────────────────────────────


class TestPesterPackDetect:
    def test_detect_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "Get-Something.Tests.ps1").touch()
        assert PesterPack().detect(tmp_path) == 1.0

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert PesterPack().detect(tmp_path) == 0.0


class TestPesterPackBuildCommand:
    def test_basic_command(self) -> None:
        pack = PesterPack()
        target = _make_target(
            selector="tests/Get-Foo.Tests.ps1",
            language="powershell",
            pack_id="powershell.pester",
        )
        cmd = pack.build_command(target, output_path=Path("/results.xml"))
        assert cmd[0] == "pwsh"
        assert "-NoProfile" in cmd
        assert "-Command" in cmd
        # The command string should contain the selector and output path
        pester_script = cmd[-1]
        assert target.selector in pester_script
        assert "JUnitXml" in pester_script

    def test_with_pattern(self) -> None:
        pack = PesterPack()
        target = _make_target(
            selector="t.Tests.ps1",
            language="powershell",
            pack_id="powershell.pester",
        )
        cmd = pack.build_command(target, output_path=Path("/r.xml"), pattern="Should-Work")
        pester_script = cmd[-1]
        assert "Should-Work" in pester_script

    def test_with_tags(self) -> None:
        pack = PesterPack()
        target = _make_target(
            selector="t.Tests.ps1",
            language="powershell",
            pack_id="powershell.pester",
        )
        cmd = pack.build_command(target, output_path=Path("/r.xml"), tags=["Unit", "Fast"])
        pester_script = cmd[-1]
        assert "Unit" in pester_script
        assert "Fast" in pester_script


class TestPesterPackParseOutput:
    def test_parse_missing_file(self) -> None:
        result = PesterPack().parse_output(Path("/nonexistent"), "")
        assert result.errors == 1


# ── BustedPack ────────────────────────────────────────────────────


class TestBustedPackDetect:
    def test_detect_busted_file(self, tmp_path: Path) -> None:
        (tmp_path / ".busted").touch()
        assert BustedPack().detect(tmp_path) == 1.0

    def test_detect_spec_files(self, tmp_path: Path) -> None:
        spec = tmp_path / "spec"
        spec.mkdir()
        (spec / "mylib_spec.lua").touch()
        assert BustedPack().detect(tmp_path) == 0.9

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert BustedPack().detect(tmp_path) == 0.0


class TestBustedPackBuildCommand:
    def test_basic_command(self) -> None:
        pack = BustedPack()
        target = _make_target(
            selector="spec/mylib_spec.lua",
            language="lua",
            pack_id="lua.busted",
        )
        cmd = pack.build_command(target, output_path=Path("/junit.xml"))
        assert "busted" in cmd
        assert "-o" in cmd
        assert "junit" in cmd
        assert target.selector in cmd

    def test_with_filter_and_tags(self) -> None:
        pack = BustedPack()
        target = _make_target(selector="s.lua", language="lua", pack_id="lua.busted")
        cmd = pack.build_command(
            target, output_path=Path("/j.xml"), pattern="my_test", tags=["unit"]
        )
        assert "--filter" in cmd
        assert "my_test" in cmd
        assert "--tags" in cmd
        assert "unit" in cmd


class TestBustedPackParseOutput:
    def test_parse_missing_file(self) -> None:
        result = BustedPack().parse_output(Path("/nonexistent"), "")
        assert result.errors == 1


# ── MixTestPack ───────────────────────────────────────────────────


class TestMixTestPackDetect:
    def test_detect_mix_exs_with_project(self, tmp_path: Path) -> None:
        (tmp_path / "mix.exs").write_text("defmodule MyApp.MixProject do\n  def project do\n  end\nend\n")
        assert MixTestPack().detect(tmp_path) == 1.0

    def test_detect_mix_exs_without_project(self, tmp_path: Path) -> None:
        (tmp_path / "mix.exs").write_text("defmodule MyApp do\nend\n")
        assert MixTestPack().detect(tmp_path) == 0.9

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert MixTestPack().detect(tmp_path) == 0.0


class TestMixTestPackBuildCommand:
    def test_basic_command(self) -> None:
        pack = MixTestPack()
        target = _make_target(
            selector="test/my_test.exs",
            language="elixir",
            pack_id="elixir.mix_test",
        )
        cmd = pack.build_command(target, output_path=Path("/out"))
        assert cmd[:2] == ["mix", "test"]
        assert target.selector in cmd

    def test_with_pattern(self) -> None:
        pack = MixTestPack()
        target = _make_target(selector="test/a.exs", language="elixir", pack_id="elixir.mix_test")
        cmd = pack.build_command(target, output_path=Path("/o"), pattern="my_test")
        assert "--only" in cmd
        assert "test:my_test" in cmd

    def test_with_tags(self) -> None:
        pack = MixTestPack()
        target = _make_target(selector="test/a.exs", language="elixir", pack_id="elixir.mix_test")
        cmd = pack.build_command(target, output_path=Path("/o"), tags=["wip", "integration"])
        only_indices = [i for i, x in enumerate(cmd) if x == "--only"]
        assert len(only_indices) == 2  # One --only per tag


class TestMixTestPackDiscover:
    @pytest.mark.asyncio
    async def test_discover_elixir_tests(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        (test_dir / "my_app_test.exs").touch()
        sub = test_dir / "sub"
        sub.mkdir()
        (sub / "helper_test.exs").touch()
        targets = await MixTestPack().discover(tmp_path)
        selectors = {t.selector for t in targets}
        assert "test/my_app_test.exs" in selectors
        assert "test/sub/helper_test.exs" in selectors

    @pytest.mark.asyncio
    async def test_discover_no_test_dir(self, tmp_path: Path) -> None:
        targets = await MixTestPack().discover(tmp_path)
        assert targets == []


class TestMixTestPackParseOutput:
    def test_parse_plural_format(self) -> None:
        stdout = "....\n\n4 tests, 1 failures\n"
        result = MixTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 4
        assert result.failed == 1

    def test_parse_singular_format(self) -> None:
        stdout = ".\n\n1 test, 0 failures\n"
        result = MixTestPack().parse_output(Path("/x"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_parse_empty_output(self) -> None:
        result = MixTestPack().parse_output(Path("/x"), "")
        assert result.total == 0
        assert result.failed == 0
