"""Tests for coderecon.testing.runtime_builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.testing.runtime import ContextRuntime, RuntimeExecutionContext
from coderecon.testing.runtime_builder import ExecutionContextBuilder


def _make_runtime(**overrides: str | int | None) -> ContextRuntime:
    """Build a ContextRuntime with defaults. Override any field via kwargs."""
    defaults: dict[str, str | int | None] = {
        "context_id": 1,
        "python_executable": None,
        "node_executable": None,
        "go_executable": None,
        "cargo_executable": None,
        "java_executable": None,
        "dotnet_executable": None,
        "ruby_executable": None,
        "package_manager": None,
        "package_manager_executable": None,
        "maven_executable": None,
        "gradle_executable": None,
        "bundle_executable": None,
        "env_vars_json": None,
    }
    defaults.update(overrides)
    return ContextRuntime(**defaults)  # type: ignore[arg-type]


class TestLanguageFamilyInference:
    """build() infers language_family from the runtime when not provided."""

    @pytest.mark.parametrize(
        "field, expected",
        [
            ("python_executable", "python"),
            ("node_executable", "javascript"),
            ("go_executable", "go"),
            ("cargo_executable", "rust"),
            ("java_executable", "java"),
            ("dotnet_executable", "csharp"),
            ("ruby_executable", "ruby"),
        ],
    )
    def test_auto_detect_language(self, field: str, expected: str) -> None:
        runtime = _make_runtime(**{field: "/usr/bin/fake"})
        with patch.object(ExecutionContextBuilder, "_build_tool_configs_for_runtime"):
            ctx = ExecutionContextBuilder.build(Path("/repo"), runtime)
        assert ctx.language_family == expected

    def test_fallback_to_unknown(self) -> None:
        runtime = _make_runtime()
        with patch.object(ExecutionContextBuilder, "_build_tool_configs_for_runtime"):
            ctx = ExecutionContextBuilder.build(Path("/repo"), runtime)
        assert ctx.language_family == "unknown"

    def test_explicit_language_not_overridden(self) -> None:
        runtime = _make_runtime(python_executable="/usr/bin/python3")
        with patch.object(ExecutionContextBuilder, "_build_tool_configs_for_runtime"):
            ctx = ExecutionContextBuilder.build(
                Path("/repo"), runtime, language_family="custom"
            )
        assert ctx.language_family == "custom"


class TestBuildStaticMethod:
    """ExecutionContextBuilder.build() static factory."""

    def test_returns_runtime_execution_context(self) -> None:
        runtime = _make_runtime(python_executable="/usr/bin/python3")
        with patch.object(ExecutionContextBuilder, "_build_tool_configs_for_runtime"):
            ctx = ExecutionContextBuilder.build(Path("/repo"), runtime)
        assert isinstance(ctx, RuntimeExecutionContext)
        assert ctx.root_path == Path("/repo")
        assert ctx.working_directory == Path("/repo")
        assert ctx.runtime is runtime

    def test_env_vars_from_runtime(self) -> None:
        import json

        env = {"MY_VAR": "hello"}
        runtime = _make_runtime(
            python_executable="/usr/bin/python3",
            env_vars_json=json.dumps(env),
        )
        with patch.object(ExecutionContextBuilder, "_build_tool_configs_for_runtime"):
            ctx = ExecutionContextBuilder.build(Path("/repo"), runtime)
        assert ctx.env_vars == env


class TestBuildFromContext:
    """ExecutionContextBuilder.build_from_context() instance method."""

    def test_uses_context_root_path(self) -> None:
        context = MagicMock()
        context.id = 42
        context.root_path = "subdir"
        context.language_family = "python"
        runtime = _make_runtime(python_executable="/usr/bin/python3")
        builder = ExecutionContextBuilder(Path("/repo"))
        with patch.object(builder, "_build_tool_configs_for_runtime"):
            ctx = builder.build_from_context(context, runtime)
        assert ctx.root_path == Path("/repo/subdir")
        assert ctx.context_id == 42

    def test_empty_root_path_uses_repo_root(self) -> None:
        context = MagicMock()
        context.id = 1
        context.root_path = ""
        context.language_family = "go"
        runtime = _make_runtime(go_executable="/usr/bin/go")
        builder = ExecutionContextBuilder(Path("/repo"))
        with patch.object(builder, "_build_tool_configs_for_runtime"):
            ctx = builder.build_from_context(context, runtime)
        assert ctx.root_path == Path("/repo")


class TestBuildToolConfigsRouting:
    """_build_tool_configs_for_runtime dispatches to the right builder."""

    @pytest.mark.parametrize(
        "lang, expected_method",
        [
            ("python", "_build_python_tools"),
            ("javascript", "_build_javascript_tools"),
            ("go", "_build_go_tools"),
            ("rust", "_build_rust_tools"),
            ("java", "_build_jvm_tools"),
            ("kotlin", "_build_jvm_tools"),
            ("csharp", "_build_dotnet_tools"),
            ("fsharp", "_build_dotnet_tools"),
            ("ruby", "_build_ruby_tools"),
        ],
    )
    def test_dispatches_to_correct_builder(
        self, lang: str, expected_method: str
    ) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        exec_ctx = MagicMock()
        runtime = _make_runtime()
        with patch.object(builder, expected_method) as mock_build:
            builder._build_tool_configs_for_runtime(exec_ctx, lang, runtime)
            mock_build.assert_called_once_with(exec_ctx, runtime)

    def test_unknown_language_is_noop(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        exec_ctx = MagicMock(spec=RuntimeExecutionContext)
        exec_ctx.test_runners = {}
        exec_ctx.linters = {}
        exec_ctx.formatters = {}
        runtime = _make_runtime()
        # Should not raise
        builder._build_tool_configs_for_runtime(exec_ctx, "unknown", runtime)


class TestBuildPythonTools:
    """_build_python_tools populates pytest, unittest, ruff, mypy, black."""

    def test_all_tools_populated(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="python",
            root_path=Path("/repo"),
            runtime=_make_runtime(python_executable="/usr/bin/python3"),
            env_vars={},
        )
        runtime = _make_runtime(python_executable="/usr/bin/python3")
        with patch.object(builder, "_check_python_package", return_value=True):
            builder._build_python_tools(exec_ctx, runtime)

        assert "python.pytest" in exec_ctx.test_runners
        assert "python.unittest" in exec_ctx.test_runners
        assert "python.ruff" in exec_ctx.linters
        assert "python.mypy" in exec_ctx.linters
        assert "python.black" in exec_ctx.formatters

    def test_pytest_uses_module_invocation(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="python",
            root_path=Path("/repo"),
            runtime=_make_runtime(python_executable="/venv/bin/python"),
            env_vars={},
        )
        runtime = _make_runtime(python_executable="/venv/bin/python")
        with patch.object(builder, "_check_python_package", return_value=True):
            builder._build_python_tools(exec_ctx, runtime)

        cfg = exec_ctx.test_runners["python.pytest"]
        assert cfg.executable == "/venv/bin/python"
        assert cfg.base_args == ["-m", "pytest"]

    def test_unittest_always_available(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="python",
            root_path=Path("/repo"),
            runtime=_make_runtime(python_executable="python"),
            env_vars={},
        )
        runtime = _make_runtime(python_executable="python")
        with patch.object(builder, "_check_python_package", return_value=False):
            builder._build_python_tools(exec_ctx, runtime)

        assert exec_ctx.test_runners["python.unittest"].available is True


class TestCheckPythonPackage:
    """_check_python_package probes for importable packages."""

    def test_returns_true_on_success(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        with patch("coderecon.testing.runtime_builder.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert builder._check_python_package("python", "pytest") is True

    def test_returns_false_on_failure(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        with patch("coderecon.testing.runtime_builder.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert builder._check_python_package("python", "missing") is False

    def test_returns_false_on_file_not_found(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        with patch("coderecon.testing.runtime_builder.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            assert builder._check_python_package("missing_python", "pytest") is False

    def test_returns_false_on_timeout(self) -> None:
        import subprocess

        builder = ExecutionContextBuilder(Path("/repo"))
        with patch("coderecon.testing.runtime_builder.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=5)
            assert builder._check_python_package("python", "pytest") is False

    def test_returns_false_on_subprocess_error(self) -> None:
        import subprocess

        builder = ExecutionContextBuilder(Path("/repo"))
        with patch("coderecon.testing.runtime_builder.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("fail")
            assert builder._check_python_package("python", "pytest") is False


class TestBuildJavascriptTools:
    """_build_javascript_tools adapts to package manager."""

    @pytest.mark.parametrize(
        "pm, pm_exe, expected_exec",
        [
            ("npm", "npm", "npx"),
            ("pnpm", "/usr/bin/pnpm", "/usr/bin/pnpm"),
            ("yarn", "/usr/bin/yarn", "/usr/bin/yarn"),
            ("bun", "/usr/bin/bun", "/usr/bin/bun"),
        ],
    )
    def test_package_manager_variants(
        self, pm: str, pm_exe: str, expected_exec: str
    ) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(
            node_executable="/usr/bin/node",
            package_manager=pm,
            package_manager_executable=pm_exe,
        )
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="javascript",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        builder._build_javascript_tools(exec_ctx, runtime)

        jest = exec_ctx.test_runners["js.jest"]
        assert jest.executable == expected_exec
        assert "js.vitest" in exec_ctx.test_runners
        assert "js.eslint" in exec_ctx.linters
        assert "js.prettier" in exec_ctx.formatters


class TestBuildGoTools:
    """_build_go_tools populates go test, golangci-lint, gofmt."""

    def test_go_tools_populated(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(go_executable="/usr/bin/go")
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="go",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        with patch("coderecon.testing.runtime_builder.shutil.which", return_value="/usr/bin/golangci-lint"):
            builder._build_go_tools(exec_ctx, runtime)

        assert exec_ctx.test_runners["go.gotest"].available is True
        assert exec_ctx.test_runners["go.gotest"].base_args == ["test", "-json"]
        assert exec_ctx.linters["go.golangci-lint"].available is True
        assert exec_ctx.formatters["go.gofmt"].available is True


class TestBuildRustTools:
    """_build_rust_tools populates cargo test, nextest, clippy, rustfmt."""

    def test_rust_tools_populated(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(cargo_executable="/usr/bin/cargo")
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="rust",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        with patch("coderecon.testing.runtime_builder.shutil.which", return_value="/usr/bin/cargo-nextest"):
            builder._build_rust_tools(exec_ctx, runtime)

        assert exec_ctx.test_runners["rust.cargo_test"].available is True
        assert exec_ctx.test_runners["rust.nextest"].available is True
        assert exec_ctx.linters["rust.clippy"].available is True
        assert exec_ctx.formatters["rust.rustfmt"].available is True

    def test_nextest_unavailable_when_not_installed(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(cargo_executable="/usr/bin/cargo")
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="rust",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        with patch("coderecon.testing.runtime_builder.shutil.which", return_value=None):
            builder._build_rust_tools(exec_ctx, runtime)

        assert exec_ctx.test_runners["rust.nextest"].available is False


class TestBuildJvmTools:
    """_build_jvm_tools populates maven/gradle runners."""

    def test_maven_runner(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(
            java_executable="/usr/bin/java",
            maven_executable="/usr/bin/mvn",
        )
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="java",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        builder._build_jvm_tools(exec_ctx, runtime)
        assert "java.maven" in exec_ctx.test_runners

    def test_gradle_runner(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(
            java_executable="/usr/bin/java",
            gradle_executable="/usr/bin/gradle",
        )
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="java",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        builder._build_jvm_tools(exec_ctx, runtime)
        assert "java.gradle" in exec_ctx.test_runners

    def test_no_build_tools_means_no_runners(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(java_executable="/usr/bin/java")
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="java",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        builder._build_jvm_tools(exec_ctx, runtime)
        assert len(exec_ctx.test_runners) == 0


class TestBuildDotnetTools:
    """_build_dotnet_tools populates dotnet test."""

    def test_dotnet_tool_available(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(dotnet_executable="/usr/bin/dotnet")
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="csharp",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        builder._build_dotnet_tools(exec_ctx, runtime)
        assert exec_ctx.test_runners["csharp.dotnet"].available is True

    def test_dotnet_not_installed(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime()  # no dotnet_executable
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="csharp",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        builder._build_dotnet_tools(exec_ctx, runtime)
        assert exec_ctx.test_runners["csharp.dotnet"].available is False


class TestBuildRubyTools:
    """_build_ruby_tools populates rspec and rubocop."""

    def test_with_bundler(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(
            ruby_executable="/usr/bin/ruby",
            bundle_executable="/usr/bin/bundle",
        )
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="ruby",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        with patch("coderecon.testing.runtime_builder.shutil.which", return_value=None):
            builder._build_ruby_tools(exec_ctx, runtime)

        rspec = exec_ctx.test_runners["ruby.rspec"]
        assert rspec.executable == "/usr/bin/bundle"
        assert rspec.base_args == ["exec", "rspec"]

    def test_without_bundler_rspec_in_path(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(ruby_executable="/usr/bin/ruby")
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="ruby",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )

        def which_side(cmd: str) -> str | None:
            return "/usr/bin/rspec" if cmd == "rspec" else "/usr/bin/rubocop" if cmd == "rubocop" else None

        with patch("coderecon.testing.runtime_builder.shutil.which", side_effect=which_side):
            builder._build_ruby_tools(exec_ctx, runtime)

        assert exec_ctx.test_runners["ruby.rspec"].available is True
        assert exec_ctx.linters["ruby.rubocop"].available is True

    def test_without_bundler_no_rspec(self) -> None:
        builder = ExecutionContextBuilder(Path("/repo"))
        runtime = _make_runtime(ruby_executable="/usr/bin/ruby")
        exec_ctx = RuntimeExecutionContext(
            context_id=1,
            language_family="ruby",
            root_path=Path("/repo"),
            runtime=runtime,
            env_vars={},
        )
        with patch("coderecon.testing.runtime_builder.shutil.which", return_value=None):
            builder._build_ruby_tools(exec_ctx, runtime)

        assert exec_ctx.test_runners["ruby.rspec"].available is False
        assert exec_ctx.linters["ruby.rubocop"].available is False
