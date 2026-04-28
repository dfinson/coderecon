"""Tests for tier-1 runner packs (pytest, jest, vitest, go test)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.testing.models import ParsedTestSuite, TestTarget
from coderecon.testing.packs.tier1 import (
    GoTestPack,
    JestPack,
    PytestPack,
    VitestPack,
)


def _make_target(
    selector: str = "tests/test_foo.py",
    kind: str = "file",
    language: str = "python",
    pack_id: str = "python.pytest",
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


# ── PytestPack ────────────────────────────────────────────────────


class TestPytestPackDetect:
    def test_detect_pytest_ini(self, tmp_path: Path) -> None:
        (tmp_path / "pytest.ini").touch()
        assert PytestPack().detect(tmp_path) == 1.0

    def test_detect_conftest(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").touch()
        assert PytestPack().detect(tmp_path) == 0.8

    def test_detect_pyproject_with_pytest_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        assert PytestPack().detect(tmp_path) == 1.0

    def test_detect_setup_cfg_with_pytest(self, tmp_path: Path) -> None:
        (tmp_path / "setup.cfg").write_text("[tool:pytest]\n")
        assert PytestPack().detect(tmp_path) == 0.9

    def test_detect_test_files_only(self, tmp_path: Path) -> None:
        (tmp_path / "test_something.py").touch()
        assert PytestPack().detect(tmp_path) == 0.5

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert PytestPack().detect(tmp_path) == 0.0


class TestPytestPackBuildCommand:
    def test_bare_command(self) -> None:
        pack = PytestPack()
        target = _make_target()
        output = Path("/tmp/junit.xml")
        cmd = pack.build_command(target, output_path=output)
        assert cmd[:1] == ["pytest"]
        assert target.selector in cmd
        assert f"--junitxml={output}" in cmd
        assert "--tb=short" in cmd
        assert "-q" in cmd

    def test_with_pattern(self) -> None:
        pack = PytestPack()
        target = _make_target()
        cmd = pack.build_command(target, output_path=Path("/out.xml"), pattern="test_foo")
        assert "-k" in cmd
        assert "test_foo" in cmd

    def test_with_tags(self) -> None:
        pack = PytestPack()
        target = _make_target()
        cmd = pack.build_command(target, output_path=Path("/out.xml"), tags=["slow", "integration"])
        assert "-m" in cmd
        assert "slow or integration" in cmd

    def test_with_exec_ctx_available(self) -> None:
        pack = PytestPack()
        target = _make_target()
        exec_ctx = MagicMock()
        tool_config = MagicMock()
        tool_config.available = True
        tool_config.executable = "/repo/.venv/bin/python"
        tool_config.base_args = ["-m", "pytest"]
        exec_ctx.get_test_runner.return_value = tool_config

        cmd = pack.build_command(target, output_path=Path("/out.xml"), exec_ctx=exec_ctx)
        assert cmd[0] == "/repo/.venv/bin/python"
        assert "-m" in cmd
        assert "pytest" in cmd

    def test_with_exec_ctx_unavailable(self) -> None:
        pack = PytestPack()
        target = _make_target()
        exec_ctx = MagicMock()
        tool_config = MagicMock()
        tool_config.available = False
        exec_ctx.get_test_runner.return_value = tool_config

        cmd = pack.build_command(target, output_path=Path("/out.xml"), exec_ctx=exec_ctx)
        assert cmd[0] == "pytest"


class TestPytestPackBuildBatchCommand:
    def test_batch_empty_targets(self) -> None:
        pack = PytestPack()
        assert pack.build_batch_command([], output_path=Path("/out.xml")) is None

    def test_batch_multiple_targets(self) -> None:
        pack = PytestPack()
        targets = [
            _make_target(selector="tests/test_a.py"),
            _make_target(selector="tests/test_b.py"),
        ]
        cmd = pack.build_batch_command(targets, output_path=Path("/out.xml"))
        assert cmd is not None
        assert "tests/test_a.py" in cmd
        assert "tests/test_b.py" in cmd

    def test_batch_with_pattern_and_tags(self) -> None:
        pack = PytestPack()
        targets = [_make_target()]
        cmd = pack.build_batch_command(
            targets,
            output_path=Path("/out.xml"),
            pattern="test_x",
            tags=["fast"],
        )
        assert cmd is not None
        assert "-k" in cmd
        assert "test_x" in cmd
        assert "-m" in cmd
        assert "fast" in cmd


class TestPytestPackDiscover:
    @pytest.mark.asyncio
    async def test_discover_finds_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "test_foo.py").touch()
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "test_bar.py").touch()
        targets = await PytestPack().discover(tmp_path)
        selectors = {t.selector for t in targets}
        assert "test_foo.py" in selectors
        assert "sub/test_bar.py" in selectors

    @pytest.mark.asyncio
    async def test_discover_skips_conftest(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").touch()
        (tmp_path / "test_real.py").touch()
        targets = await PytestPack().discover(tmp_path)
        selectors = {t.selector for t in targets}
        assert "conftest.py" not in selectors
        assert "test_real.py" in selectors

    @pytest.mark.asyncio
    async def test_discover_skips_underscore_prefixed(self, tmp_path: Path) -> None:
        (tmp_path / "_test_internal.py").touch()
        targets = await PytestPack().discover(tmp_path)
        assert len(targets) == 0


class TestPytestPackParseOutput:
    def test_parse_missing_file(self) -> None:
        pack = PytestPack()
        result = pack.parse_output(Path("/nonexistent/junit.xml"), "")
        assert result.errors == 1

    def test_parse_existing_file(self, tmp_path: Path) -> None:
        junit_file = tmp_path / "junit.xml"
        junit_file.write_text(
            '<?xml version="1.0"?>'
            "<testsuites><testsuite name='s' tests='1' failures='0'>"
            "<testcase name='test_x' classname='c' time='0.1'/>"
            "</testsuite></testsuites>"
        )
        with patch("coderecon.testing.parsers.parse_junit_xml") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="pytest", total=1, passed=1)
            result = PytestPack().parse_output(junit_file, "")
            mock_parse.assert_called_once()
            assert result.total == 1


# ── JestPack ──────────────────────────────────────────────────────


class TestJestPackDetect:
    def test_detect_config_js(self, tmp_path: Path) -> None:
        (tmp_path / "jest.config.js").touch()
        assert JestPack().detect(tmp_path) == 1.0

    def test_detect_config_ts(self, tmp_path: Path) -> None:
        (tmp_path / "jest.config.ts").touch()
        assert JestPack().detect(tmp_path) == 1.0

    def test_detect_package_json_jest_key(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"jest": {}}))
        assert JestPack().detect(tmp_path) == 0.9

    def test_detect_package_json_jest_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"jest": "^29.0.0"}})
        )
        assert JestPack().detect(tmp_path) == 0.8

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert JestPack().detect(tmp_path) == 0.0


class TestJestPackBuildCommand:
    def test_npx_default(self) -> None:
        pack = JestPack()
        target = _make_target(
            selector="src/foo.test.ts",
            language="typescript",
            pack_id="js.jest",
        )
        cmd = pack.build_command(target, output_path=Path("/out.json"))
        assert cmd[0] == "npx"
        assert "jest" in cmd
        assert "--json" in cmd

    def test_pnpm_detected(self, tmp_path: Path) -> None:
        (tmp_path / "pnpm-lock.yaml").touch()
        pack = JestPack()
        target = _make_target(
            selector="test.spec.js",
            pack_id="js.jest",
            workspace_root=str(tmp_path),
        )
        cmd = pack.build_command(target, output_path=Path("/out.json"))
        assert cmd[0] == "pnpm"

    def test_yarn_detected(self, tmp_path: Path) -> None:
        (tmp_path / "yarn.lock").touch()
        pack = JestPack()
        target = _make_target(
            selector="test.spec.js",
            pack_id="js.jest",
            workspace_root=str(tmp_path),
        )
        cmd = pack.build_command(target, output_path=Path("/out.json"))
        assert cmd[0] == "yarn"

    def test_with_pattern(self) -> None:
        pack = JestPack()
        target = _make_target(selector="a.test.js", pack_id="js.jest")
        cmd = pack.build_command(target, output_path=Path("/o.json"), pattern="my_test")
        assert "--testNamePattern" in cmd
        assert "my_test" in cmd


class TestJestPackParseJson:
    def test_parse_valid_json(self) -> None:
        data = {
            "testResults": [
                {
                    "assertionResults": [
                        {
                            "title": "should add",
                            "ancestorTitles": ["math"],
                            "status": "passed",
                            "duration": 5,
                            "failureMessages": [],
                        },
                        {
                            "title": "should fail",
                            "ancestorTitles": ["math"],
                            "status": "failed",
                            "duration": 10,
                            "failureMessages": ["Expected 1 to be 2"],
                        },
                    ],
                    "endTime": 1500,
                }
            ]
        }
        result = JestPack()._parse_jest_json(json.dumps(data))
        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 1
        assert result.tests[0].name == "should add"
        assert result.tests[0].classname == "math"
        # Duration: 5ms / 1000 = 0.005s
        assert result.tests[0].duration_seconds == pytest.approx(0.005)

    def test_parse_invalid_json(self) -> None:
        result = JestPack()._parse_jest_json("not json")
        assert result.errors == 1

    def test_parse_skipped_tests(self) -> None:
        data = {
            "testResults": [
                {
                    "assertionResults": [
                        {
                            "title": "pending test",
                            "ancestorTitles": [],
                            "status": "pending",
                            "duration": 0,
                            "failureMessages": [],
                        },
                    ],
                    "endTime": 0,
                }
            ]
        }
        result = JestPack()._parse_jest_json(json.dumps(data))
        assert result.skipped == 1

    def test_parse_output_missing_file(self) -> None:
        result = JestPack().parse_output(Path("/nonexistent"), "")
        assert result.errors == 1


class TestJestPackDiscover:
    @pytest.mark.asyncio
    async def test_discover_js_ts_files(self, tmp_path: Path) -> None:
        (tmp_path / "foo.test.js").touch()
        (tmp_path / "bar.test.ts").touch()
        (tmp_path / "baz.spec.ts").touch()
        targets = await JestPack().discover(tmp_path)
        selectors = {t.selector for t in targets}
        assert "foo.test.js" in selectors
        assert "bar.test.ts" in selectors
        assert "baz.spec.ts" in selectors
        # Check language assignment
        lang_map = {t.selector: t.language for t in targets}
        assert lang_map["foo.test.js"] == "javascript"
        assert lang_map["bar.test.ts"] == "typescript"


# ── VitestPack ────────────────────────────────────────────────────


class TestVitestPackDetect:
    def test_detect_vitest_config(self, tmp_path: Path) -> None:
        (tmp_path / "vitest.config.ts").touch()
        assert VitestPack().detect(tmp_path) == 1.0

    def test_detect_vite_config_with_vitest(self, tmp_path: Path) -> None:
        (tmp_path / "vite.config.ts").write_text("import { defineConfig } from 'vitest/config'")
        assert VitestPack().detect(tmp_path) == 0.9

    def test_detect_vite_config_without_vitest(self, tmp_path: Path) -> None:
        (tmp_path / "vite.config.ts").write_text("import { defineConfig } from 'vite'")
        assert VitestPack().detect(tmp_path) == 0.0

    def test_detect_package_json_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"vitest": "^1.0.0"}})
        )
        assert VitestPack().detect(tmp_path) == 0.7

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert VitestPack().detect(tmp_path) == 0.0


class TestVitestPackBuildCommand:
    def test_default_command(self) -> None:
        pack = VitestPack()
        target = _make_target(selector="src/foo.test.ts", pack_id="js.vitest")
        cmd = pack.build_command(target, output_path=Path("/out.xml"))
        assert "npx" in cmd
        assert "vitest" in cmd
        assert "run" in cmd
        assert "--reporter=junit" in cmd

    def test_with_pattern(self) -> None:
        pack = VitestPack()
        target = _make_target(selector="foo.test.ts", pack_id="js.vitest")
        cmd = pack.build_command(target, output_path=Path("/o.xml"), pattern="my pattern")
        assert "--testNamePattern" in cmd
        assert "my pattern" in cmd


# ── GoTestPack ────────────────────────────────────────────────────


class TestGoTestPackDetect:
    def test_detect_go_mod(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").touch()
        assert GoTestPack().detect(tmp_path) == 1.0

    def test_detect_go_sum(self, tmp_path: Path) -> None:
        (tmp_path / "go.sum").touch()
        assert GoTestPack().detect(tmp_path) == 0.9

    def test_detect_go_files_only(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").touch()
        assert GoTestPack().detect(tmp_path) == 0.3

    def test_detect_nothing(self, tmp_path: Path) -> None:
        assert GoTestPack().detect(tmp_path) == 0.0


class TestGoTestPackBuildCommand:
    def test_bare_command(self) -> None:
        pack = GoTestPack()
        target = _make_target(
            selector="./pkg/foo",
            kind="package",
            language="go",
            pack_id="go.gotest",
        )
        cmd = pack.build_command(target, output_path=Path("/unused"))
        assert cmd[:3] == ["go", "test", "-json"]
        assert "./pkg/foo" in cmd

    def test_with_pattern(self) -> None:
        pack = GoTestPack()
        target = _make_target(selector="./...", kind="package", language="go", pack_id="go.gotest")
        cmd = pack.build_command(target, output_path=Path("/x"), pattern="TestFoo")
        assert "-run" in cmd
        assert "TestFoo" in cmd

    def test_with_tags(self) -> None:
        pack = GoTestPack()
        target = _make_target(selector="./...", kind="package", language="go", pack_id="go.gotest")
        cmd = pack.build_command(target, output_path=Path("/x"), tags=["integration", "e2e"])
        assert "-tags" in cmd
        assert "integration,e2e" in cmd


class TestGoTestPackDiscover:
    @pytest.mark.asyncio
    async def test_discover_go_packages(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg" / "foo"
        pkg.mkdir(parents=True)
        (pkg / "foo_test.go").touch()
        (tmp_path / "go.mod").touch()
        targets = await GoTestPack().discover(tmp_path)
        assert len(targets) == 1
        assert targets[0].kind == "package"
        assert targets[0].selector == "./pkg/foo"

    @pytest.mark.asyncio
    async def test_discover_deduplicates_packages(self, tmp_path: Path) -> None:
        pkg = tmp_path / "internal" / "bar"
        pkg.mkdir(parents=True)
        (pkg / "a_test.go").touch()
        (pkg / "b_test.go").touch()
        targets = await GoTestPack().discover(tmp_path)
        # Two test files in same package → one target
        assert len(targets) == 1

    def test_parse_output_delegates(self) -> None:
        pack = GoTestPack()
        with patch("coderecon.testing.parsers.parse_go_test_json") as mock_parse:
            mock_parse.return_value = ParsedTestSuite(name="go", total=3, passed=3)
            result = pack.parse_output(Path("/unused"), '{"Action":"pass"}')
            mock_parse.assert_called_once_with('{"Action":"pass"}')
            assert result.total == 3
