"""Execution context builder for assembling runtime execution contexts.

Bridges the index layer (Context, ContextRuntime) and the execution layer
(RuntimeExecutionContext, ToolConfig) by building tool configurations for
each supported language family.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from coderecon.testing.runtime import (
    ContextRuntime,
    RuntimeExecutionContext,
    ToolConfig,
)

if TYPE_CHECKING:
    from coderecon.index.models import Context

log = structlog.get_logger(__name__)

# Execution Context Builder

class ExecutionContextBuilder:
    """Builds RuntimeExecutionContext from Context and ContextRuntime.
    This is the bridge between the index layer (Context, ContextRuntime)
    and the execution layer (RuntimeExecutionContext, ToolConfig).
    """
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
    @staticmethod
    def build(
        context_root: Path,
        runtime: ContextRuntime,
        language_family: str | None = None,
    ) -> RuntimeExecutionContext:
        """Build RuntimeExecutionContext from workspace root and runtime.
        This is a convenience method for execution-time context building
        when we don't have a persisted Context object.
        Args:
            context_root: Absolute path to workspace/context root
            runtime: ContextRuntime with detected executables
            language_family: Optional language family (auto-detected if not provided)
        Returns:
            RuntimeExecutionContext ready for execution
        """
        # Infer language family from runtime if not provided
        if language_family is None:
            if runtime.python_executable:
                language_family = "python"
            elif runtime.node_executable:
                language_family = "javascript"
            elif runtime.go_executable:
                language_family = "go"
            elif runtime.cargo_executable:
                language_family = "rust"
            elif runtime.java_executable:
                language_family = "java"
            elif runtime.dotnet_executable:
                language_family = "csharp"
            elif runtime.ruby_executable:
                language_family = "ruby"
            else:
                language_family = "unknown"
        exec_ctx = RuntimeExecutionContext(
            context_id=runtime.context_id,
            language_family=language_family,
            root_path=context_root,
            runtime=runtime,
            env_vars=runtime.get_env_vars(),
            working_directory=context_root,
        )
        # Build tool configs
        builder = ExecutionContextBuilder(context_root)
        builder._build_tool_configs_for_runtime(exec_ctx, language_family, runtime)
        return exec_ctx
    def build_from_context(
        self, context: Context, runtime: ContextRuntime
    ) -> RuntimeExecutionContext:
        """Build RuntimeExecutionContext from Context and ContextRuntime."""
        root_path = self.repo_root / context.root_path if context.root_path else self.repo_root
        exec_ctx = RuntimeExecutionContext(
            context_id=context.id or 0,
            language_family=context.language_family,
            root_path=root_path,
            runtime=runtime,
            env_vars=runtime.get_env_vars(),
            working_directory=root_path,
        )
        # Build tool configs based on language
        self._build_tool_configs_for_runtime(exec_ctx, context.language_family, runtime)
        return exec_ctx
    def _build_tool_configs_for_runtime(
        self, exec_ctx: RuntimeExecutionContext, language_family: str, runtime: ContextRuntime
    ) -> None:
        """Populate tool configurations for the execution context."""
        if language_family == "python":
            self._build_python_tools(exec_ctx, runtime)
        elif language_family == "javascript":
            self._build_javascript_tools(exec_ctx, runtime)
        elif language_family == "go":
            self._build_go_tools(exec_ctx, runtime)
        elif language_family == "rust":
            self._build_rust_tools(exec_ctx, runtime)
        elif language_family in ("java", "kotlin", "scala", "groovy"):
            self._build_jvm_tools(exec_ctx, runtime)
        elif language_family in ("csharp", "fsharp", "vbnet"):
            self._build_dotnet_tools(exec_ctx, runtime)
        elif language_family == "ruby":
            self._build_ruby_tools(exec_ctx, runtime)
    def _build_python_tools(
        self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime
    ) -> None:
        """Build Python tool configs."""
        python_exe = runtime.python_executable or "python"
        # pytest - invoke as module to ensure correct interpreter
        exec_ctx.test_runners["python.pytest"] = ToolConfig(
            tool_id="python.pytest",
            executable=python_exe,
            base_args=["-m", "pytest"],
            available=self._check_python_package(python_exe, "pytest"),
        )
        # unittest
        exec_ctx.test_runners["python.unittest"] = ToolConfig(
            tool_id="python.unittest",
            executable=python_exe,
            base_args=["-m", "unittest"],
            available=True,  # Built-in
        )
        # ruff linter
        ruff_available = self._check_python_package(python_exe, "ruff")
        exec_ctx.linters["python.ruff"] = ToolConfig(
            tool_id="python.ruff",
            executable=python_exe,
            base_args=["-m", "ruff", "check"],
            available=ruff_available,
        )
        # mypy type checker
        mypy_available = self._check_python_package(python_exe, "mypy")
        exec_ctx.linters["python.mypy"] = ToolConfig(
            tool_id="python.mypy",
            executable=python_exe,
            base_args=["-m", "mypy"],
            available=mypy_available,
        )
        # black formatter
        black_available = self._check_python_package(python_exe, "black")
        exec_ctx.formatters["python.black"] = ToolConfig(
            tool_id="python.black",
            executable=python_exe,
            base_args=["-m", "black"],
            available=black_available,
        )
    def _check_python_package(self, python_exe: str, package: str) -> bool:
        """Check if a Python package is installed."""
        try:
            result = subprocess.run(
                [python_exe, "-c", f"import {package}"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except FileNotFoundError:
            log.debug("package_check_exe_not_found", exc_info=True)
            return False
        except subprocess.TimeoutExpired:
            log.debug("package_check_timed_out", exc_info=True)
            return False
        except subprocess.SubprocessError:
            log.debug("package_check_subprocess_error", exc_info=True)
            return False
    def _build_javascript_tools(
        self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime
    ) -> None:
        """Build JavaScript tool configs."""
        pm = runtime.package_manager or "npm"
        pm_exe = runtime.package_manager_executable or pm
        # Determine how to run packages
        if pm == "pnpm":
            run_prefix = [pm_exe, "exec"]
        elif pm == "yarn":
            run_prefix = [pm_exe]
        elif pm == "bun":
            run_prefix = [pm_exe, "run"]
        else:  # npm
            run_prefix = ["npx"]
        # jest
        exec_ctx.test_runners["js.jest"] = ToolConfig(
            tool_id="js.jest",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["jest"] if len(run_prefix) > 1 else ["jest"],
            available=True,  # Assume available if package.json exists
        )
        # vitest
        exec_ctx.test_runners["js.vitest"] = ToolConfig(
            tool_id="js.vitest",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["vitest", "run"]
            if len(run_prefix) > 1
            else ["vitest", "run"],
            available=True,
        )
        # eslint
        exec_ctx.linters["js.eslint"] = ToolConfig(
            tool_id="js.eslint",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["eslint"] if len(run_prefix) > 1 else ["eslint"],
            available=True,
        )
        # prettier
        exec_ctx.formatters["js.prettier"] = ToolConfig(
            tool_id="js.prettier",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["prettier"] if len(run_prefix) > 1 else ["prettier"],
            available=True,
        )
    def _build_go_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build Go tool configs."""
        go_exe = runtime.go_executable or "go"
        # go test
        exec_ctx.test_runners["go.gotest"] = ToolConfig(
            tool_id="go.gotest",
            executable=go_exe,
            base_args=["test", "-json"],
            available=bool(runtime.go_executable),
        )
        # golangci-lint
        golint = shutil.which("golangci-lint")
        exec_ctx.linters["go.golangci-lint"] = ToolConfig(
            tool_id="go.golangci-lint",
            executable=golint or "golangci-lint",
            base_args=["run"],
            available=bool(golint),
        )
        # gofmt
        exec_ctx.formatters["go.gofmt"] = ToolConfig(
            tool_id="go.gofmt",
            executable=go_exe,
            base_args=["fmt"],
            available=bool(runtime.go_executable),
        )
    def _build_rust_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build Rust tool configs."""
        cargo_exe = runtime.cargo_executable or "cargo"
        # cargo test
        exec_ctx.test_runners["rust.cargo_test"] = ToolConfig(
            tool_id="rust.cargo_test",
            executable=cargo_exe,
            base_args=["test"],
            available=bool(runtime.cargo_executable),
        )
        # cargo-nextest (if available)
        nextest = shutil.which("cargo-nextest")
        exec_ctx.test_runners["rust.nextest"] = ToolConfig(
            tool_id="rust.nextest",
            executable=cargo_exe,
            base_args=["nextest", "run"],
            available=bool(nextest),
        )
        # clippy
        exec_ctx.linters["rust.clippy"] = ToolConfig(
            tool_id="rust.clippy",
            executable=cargo_exe,
            base_args=["clippy"],
            available=bool(runtime.cargo_executable),
        )
        # rustfmt
        exec_ctx.formatters["rust.rustfmt"] = ToolConfig(
            tool_id="rust.rustfmt",
            executable=cargo_exe,
            base_args=["fmt"],
            available=bool(runtime.cargo_executable),
        )
    def _build_jvm_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build JVM tool configs."""
        # Maven
        if runtime.maven_executable:
            exec_ctx.test_runners["java.maven"] = ToolConfig(
                tool_id="java.maven",
                executable=runtime.maven_executable,
                base_args=["test"],
                available=True,
            )
        # Gradle
        if runtime.gradle_executable:
            exec_ctx.test_runners["java.gradle"] = ToolConfig(
                tool_id="java.gradle",
                executable=runtime.gradle_executable,
                base_args=["test"],
                available=True,
            )
    def _build_dotnet_tools(
        self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime
    ) -> None:
        """Build .NET tool configs."""
        dotnet_exe = runtime.dotnet_executable or "dotnet"
        exec_ctx.test_runners["csharp.dotnet"] = ToolConfig(
            tool_id="csharp.dotnet",
            executable=dotnet_exe,
            base_args=["test"],
            available=bool(runtime.dotnet_executable),
        )
    def _build_ruby_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build Ruby tool configs."""
        bundle_exe = runtime.bundle_executable
        if bundle_exe:
            exec_ctx.test_runners["ruby.rspec"] = ToolConfig(
                tool_id="ruby.rspec",
                executable=bundle_exe,
                base_args=["exec", "rspec"],
                available=True,
            )
        else:
            rspec = shutil.which("rspec")
            exec_ctx.test_runners["ruby.rspec"] = ToolConfig(
                tool_id="ruby.rspec",
                executable=rspec or "rspec",
                base_args=[],
                available=bool(rspec),
            )
        # rubocop
        rubocop = shutil.which("rubocop")
        exec_ctx.linters["ruby.rubocop"] = ToolConfig(
            tool_id="ruby.rubocop",
            executable=rubocop or "rubocop",
            base_args=[],
            available=bool(rubocop),
        )
