"""Tests for tier-1 compiled runner packs (Rust cargo, Java Maven/Gradle)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.testing.models import ParsedTestSuite, TestTarget
from coderecon.testing.packs.tier1_compiled import (
    CargoNextestPack,
    CargoTestPack,
    GradlePack,
    MavenSurefirePack,
)


def _make_target(
    selector: str = ".",
    kind: str = "package",
    language: str = "rust",
    pack_id: str = "rust.nextest",
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


# ── CargoNextestPack ──────────────────────────────────────────────


class TestCargoNextestDetect:
    def test_no_cargo_toml(self, tmp_path: Path) -> None:
        assert CargoNextestPack().detect(tmp_path) == 0.0

    def test_cargo_toml_with_nextest(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").touch()
        with patch("shutil.which", return_value="/usr/bin/cargo-nextest"):
            assert CargoNextestPack().detect(tmp_path) == 0.95

    def test_cargo_toml_without_nextest(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").touch()
        with patch("shutil.which", return_value=None):
            assert CargoNextestPack().detect(tmp_path) == 0.0


class TestCargoNextestDiscover:
    @pytest.mark.anyio
    async def test_no_cargo_toml(self, tmp_path: Path) -> None:
        targets = await CargoNextestPack().discover(tmp_path)
        assert targets == []

    @pytest.mark.anyio
    async def test_single_package(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\nname = \"mypkg\"\n")
        targets = await CargoNextestPack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].selector == "."
        assert targets[0].kind == "package"

    @pytest.mark.anyio
    async def test_workspace_with_members(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers = [\"crate_a\"]\n")
        crate_a = tmp_path / "crate_a"
        crate_a.mkdir()
        (crate_a / "Cargo.toml").touch()
        (crate_a / "tests").mkdir()
        targets = await CargoNextestPack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].selector == "crate_a"

    @pytest.mark.anyio
    async def test_workspace_with_root_package(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[workspace]\n[package]\nname=\"root\"\n")
        (tmp_path / "tests").mkdir()
        targets = await CargoNextestPack().discover(tmp_path)
        # root package detected
        assert any(t.selector == "." for t in targets)


class TestCargoNextestBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        pack = CargoNextestPack()
        target = _make_target(workspace_root=str(tmp_path))
        out = tmp_path / "junit.xml"
        cmd = pack.build_command(target, output_path=out)
        assert cmd[:3] == ["cargo", "nextest", "run"]
        assert f"--junit-path={out}" in cmd

    def test_with_package_selector(self, tmp_path: Path) -> None:
        pack = CargoNextestPack()
        target = _make_target(selector="my_crate", workspace_root=str(tmp_path))
        cmd = pack.build_command(target, output_path=tmp_path / "out.xml")
        assert "-p" in cmd
        assert "my_crate" in cmd

    def test_with_pattern_filter(self, tmp_path: Path) -> None:
        pack = CargoNextestPack()
        target = _make_target(workspace_root=str(tmp_path))
        cmd = pack.build_command(target, output_path=tmp_path / "out.xml", pattern="test_foo")
        assert "--" in cmd
        assert "test_foo" in cmd

    def test_with_exec_ctx(self, tmp_path: Path) -> None:
        pack = CargoNextestPack()
        target = _make_target(workspace_root=str(tmp_path))
        exec_ctx = MagicMock()
        tool_config = MagicMock()
        tool_config.available = True
        tool_config.executable = "/custom/nextest"
        tool_config.base_args = ["run", "--workspace"]
        exec_ctx.get_test_runner.return_value = tool_config
        cmd = pack.build_command(target, output_path=tmp_path / "out.xml", exec_ctx=exec_ctx)
        assert cmd[0] == "/custom/nextest"

    def test_with_exec_ctx_unavailable(self, tmp_path: Path) -> None:
        pack = CargoNextestPack()
        target = _make_target(workspace_root=str(tmp_path))
        exec_ctx = MagicMock()
        exec_ctx.get_test_runner.return_value = MagicMock(available=False)
        cmd = pack.build_command(target, output_path=tmp_path / "out.xml", exec_ctx=exec_ctx)
        assert cmd[:3] == ["cargo", "nextest", "run"]


class TestCargoNextestParseOutput:
    def test_parse_existing_junit(self, tmp_path: Path) -> None:
        xml = tmp_path / "junit.xml"
        xml.write_text('<?xml version="1.0"?><testsuites><testsuite name="t" tests="1"><testcase name="c"/></testsuite></testsuites>')
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="cargo-nextest", total=1, passed=1)
            result = CargoNextestPack().parse_output(xml, "")
            assert result.total == 1

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        result = CargoNextestPack().parse_output(tmp_path / "missing.xml", "")
        assert result.errors == 1


# ── CargoTestPack ─────────────────────────────────────────────────


class TestCargoTestDetect:
    def test_no_cargo_toml(self, tmp_path: Path) -> None:
        assert CargoTestPack().detect(tmp_path) == 0.0

    def test_cargo_toml_nextest_available(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").touch()
        with patch("shutil.which", return_value="/usr/bin/cargo-nextest"):
            assert CargoTestPack().detect(tmp_path) == 0.5

    def test_cargo_toml_nextest_unavailable(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").touch()
        with patch("shutil.which", return_value=None):
            assert CargoTestPack().detect(tmp_path) == 0.9


class TestCargoTestDiscover:
    @pytest.mark.anyio
    async def test_delegates_to_nextest_and_updates_pack_id(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\nname = \"mypkg\"\n")
        targets = await CargoTestPack().discover(tmp_path)
        assert all(t.runner_pack_id == "rust.cargo_test" for t in targets)


class TestCargoTestBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        target = _make_target(pack_id="rust.cargo_test", workspace_root=str(tmp_path))
        cmd = CargoTestPack().build_command(target, output_path=tmp_path / "out.xml")
        assert cmd[:2] == ["cargo", "test"]

    def test_with_package_selector(self, tmp_path: Path) -> None:
        target = _make_target(selector="crate_b", pack_id="rust.cargo_test", workspace_root=str(tmp_path))
        cmd = CargoTestPack().build_command(target, output_path=tmp_path / "out.xml")
        assert "-p" in cmd
        assert "crate_b" in cmd


class TestCargoTestParseOutput:
    def test_parse_pass_line(self) -> None:
        stdout = "test result: ok. 5 passed; 0 failed; 0 ignored; finished in 1.2s\n"
        result = CargoTestPack().parse_output(Path("/unused"), stdout)
        assert result.passed == 5
        assert result.failed == 0
        assert result.total == 5

    def test_parse_fail_line(self) -> None:
        stdout = "test result: FAILED. 3 passed; 2 failed; 0 ignored;\n"
        result = CargoTestPack().parse_output(Path("/unused"), stdout)
        assert result.passed == 3
        assert result.failed == 2
        assert result.total == 5

    def test_parse_no_result_line(self) -> None:
        result = CargoTestPack().parse_output(Path("/unused"), "compiling...\n")
        assert result.total == 0


# ── MavenSurefirePack ────────────────────────────────────────────


class TestMavenSurefireDetect:
    def test_pom_xml(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").touch()
        assert MavenSurefirePack().detect(tmp_path) == 1.0

    def test_mvnw_only(self, tmp_path: Path) -> None:
        (tmp_path / "mvnw").touch()
        assert MavenSurefirePack().detect(tmp_path) == 0.95

    def test_no_markers(self, tmp_path: Path) -> None:
        assert MavenSurefirePack().detect(tmp_path) == 0.0


class TestMavenSurefireDiscover:
    @pytest.mark.anyio
    async def test_no_pom(self, tmp_path: Path) -> None:
        targets = await MavenSurefirePack().discover(tmp_path)
        assert targets == []

    @pytest.mark.anyio
    async def test_single_project(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text("<project/>")
        (tmp_path / "src" / "test").mkdir(parents=True)
        targets = await MavenSurefirePack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].selector == "."

    @pytest.mark.anyio
    async def test_multi_module(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text("<project><modules><module>mod_a</module></modules></project>")
        mod_a = tmp_path / "mod_a"
        mod_a.mkdir()
        (mod_a / "pom.xml").touch()
        (mod_a / "src" / "test").mkdir(parents=True)
        targets = await MavenSurefirePack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].selector == "mod_a"

    @pytest.mark.anyio
    async def test_single_project_no_test_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text("<project/>")
        targets = await MavenSurefirePack().discover(tmp_path)
        assert targets == []


class TestMavenSurefireBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        target = _make_target(language="java", pack_id="java.maven", workspace_root=str(tmp_path))
        cmd = MavenSurefirePack().build_command(target, output_path=tmp_path / "out.xml")
        assert cmd[:2] == ["mvn", "test"]

    def test_with_mvnw(self, tmp_path: Path) -> None:
        (tmp_path / "mvnw").touch()
        target = _make_target(language="java", pack_id="java.maven", workspace_root=str(tmp_path))
        cmd = MavenSurefirePack().build_command(target, output_path=tmp_path / "out.xml")
        assert cmd[0] == "./mvnw"

    def test_with_selector_and_pattern(self, tmp_path: Path) -> None:
        target = _make_target(selector="submod", language="java", pack_id="java.maven", workspace_root=str(tmp_path))
        cmd = MavenSurefirePack().build_command(target, output_path=tmp_path / "out.xml", pattern="MyTest")
        assert "-pl" in cmd
        assert "submod" in cmd
        assert "-Dtest=MyTest" in cmd


class TestMavenSurefireParseOutput:
    def test_parse_no_reports_dir(self, tmp_path: Path) -> None:
        result = MavenSurefirePack().parse_output(tmp_path / "out.xml", "")
        assert result.errors == 1

    def test_parse_surefire_reports(self, tmp_path: Path) -> None:
        reports = tmp_path / "target" / "surefire-reports"
        reports.mkdir(parents=True)
        xml_content = '<?xml version="1.0"?><testsuite name="s" tests="2"><testcase name="a"/><testcase name="b"/></testsuite>'
        (reports / "TEST-com.example.MyTest.xml").write_text(xml_content)
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="maven", total=2, passed=2, tests=[
                MagicMock(status="passed"), MagicMock(status="passed"),
            ])
            # output_path.parent is tmp_path, so reports_dir = tmp_path/target/surefire-reports
            result = MavenSurefirePack().parse_output(tmp_path / "out.xml", "")
            assert result.passed == 2


# ── GradlePack ────────────────────────────────────────────────────


class TestGradleDetect:
    def test_build_gradle(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").touch()
        assert GradlePack().detect(tmp_path) == 1.0

    def test_build_gradle_kts(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle.kts").touch()
        assert GradlePack().detect(tmp_path) == 1.0

    def test_gradlew_only(self, tmp_path: Path) -> None:
        (tmp_path / "gradlew").touch()
        assert GradlePack().detect(tmp_path) == 0.95

    def test_no_markers(self, tmp_path: Path) -> None:
        assert GradlePack().detect(tmp_path) == 0.0


class TestGradleDiscover:
    @pytest.mark.anyio
    async def test_single_project(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").touch()
        (tmp_path / "src" / "test").mkdir(parents=True)
        targets = await GradlePack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].selector == "."

    @pytest.mark.anyio
    async def test_multi_project(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").touch()
        (tmp_path / "settings.gradle").write_text("include ':app'\n")
        app = tmp_path / "app"
        app.mkdir()
        (app / "build.gradle").touch()
        (app / "src" / "test").mkdir(parents=True)
        targets = await GradlePack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].selector == "app"

    @pytest.mark.anyio
    async def test_single_project_no_test_dir(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").touch()
        targets = await GradlePack().discover(tmp_path)
        assert targets == []


class TestGradleBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        target = _make_target(language="java", pack_id="java.gradle", workspace_root=str(tmp_path))
        cmd = GradlePack().build_command(target, output_path=tmp_path / "out.xml")
        assert cmd == ["gradle", "test"]

    def test_with_gradlew(self, tmp_path: Path) -> None:
        (tmp_path / "gradlew").touch()
        target = _make_target(language="java", pack_id="java.gradle", workspace_root=str(tmp_path))
        cmd = GradlePack().build_command(target, output_path=tmp_path / "out.xml")
        assert cmd[0] == "./gradlew"

    def test_subproject_selector(self, tmp_path: Path) -> None:
        target = _make_target(selector="lib", language="java", pack_id="java.gradle", workspace_root=str(tmp_path))
        cmd = GradlePack().build_command(target, output_path=tmp_path / "out.xml")
        assert ":lib:test" in cmd

    def test_with_pattern(self, tmp_path: Path) -> None:
        target = _make_target(language="java", pack_id="java.gradle", workspace_root=str(tmp_path))
        cmd = GradlePack().build_command(target, output_path=tmp_path / "out.xml", pattern="com.Foo")
        assert "--tests" in cmd
        assert "com.Foo" in cmd

    def test_with_exec_ctx(self, tmp_path: Path) -> None:
        pack = GradlePack()
        target = _make_target(selector="lib", language="java", pack_id="java.gradle", workspace_root=str(tmp_path))
        exec_ctx = MagicMock()
        tool_config = MagicMock()
        tool_config.available = True
        tool_config.executable = "/custom/gradle"
        tool_config.base_args = ["test"]
        exec_ctx.get_test_runner.return_value = tool_config
        cmd = pack.build_command(target, output_path=tmp_path / "out.xml", exec_ctx=exec_ctx)
        assert cmd[0] == "/custom/gradle"
        assert ":lib:test" in cmd


class TestGradleParseOutput:
    def test_delegates_to_parse_gradle(self, tmp_path: Path) -> None:
        with patch("coderecon.testing.packs._parse_gradle_test_results") as mock:
            mock.return_value = ParsedTestSuite(name="gradle", total=3, passed=3)
            result = GradlePack().parse_output(tmp_path / "out.xml", "")
            mock.assert_called_once_with(tmp_path / "out.xml", "gradle")
            assert result.total == 3


# ── Pack Identity / Metadata ──────────────────────────────────────


class TestPackMetadata:
    def test_cargo_nextest_identity(self) -> None:
        p = CargoNextestPack()
        assert p.pack_id == "rust.nextest"
        assert p.language == "rust"
        assert p.capabilities.supports_junit_output is True

    def test_cargo_test_identity(self) -> None:
        p = CargoTestPack()
        assert p.pack_id == "rust.cargo_test"
        assert p.capabilities.supports_junit_output is False

    def test_maven_identity(self) -> None:
        p = MavenSurefirePack()
        assert p.pack_id == "java.maven"
        assert p.language == "java"
        assert p.output_strategy.format == "junit_xml"

    def test_gradle_identity(self) -> None:
        p = GradlePack()
        assert p.pack_id == "java.gradle"
        assert p.language == "java"
        assert p.output_strategy.format == "junit_xml"
