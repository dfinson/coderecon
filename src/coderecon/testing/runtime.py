"""Context runtime and execution context models.

This module implements Design A (Capture Runtime at Discovery Time) with elements
of Design D (Unified Execution Context) from the CodeRecon architecture.

The key insight is that contexts (per SPEC.md §8.4) are the authority for file
membership, and should also own execution environment information. This ensures:

1. Runtime is captured once at discovery time, not detected per-execution
2. Test/lint targets inherit their execution context from their owning context
3. Deterministic execution: same context → same runtime → same behavior

Architecture:
- ContextRuntime: Captured execution environment (Python executable, Node, etc.)
- ToolConfig: Configuration for a specific tool (pytest, ruff, jest, etc.)
- ExecutionContext: Unified context + runtime + tool config for execution
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel

if TYPE_CHECKING:
    from coderecon.index.models import Context

# Database Model: ContextRuntime

class ContextRuntime(SQLModel, table=True):
    """Execution environment captured at context discovery time.
    Persisted to SQLite so runtime info survives server restarts.
    Re-resolved when context markers change or on explicit refresh.
    """
    __tablename__ = "context_runtimes"
    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), unique=True, index=True)
    )
    # Python runtime
    python_executable: str | None = None  # Full path: /repo/.venv/bin/python
    python_version: str | None = None  # "3.12.1"
    python_venv_path: str | None = None  # /repo/.venv (if detected)
    # JavaScript/TypeScript runtime
    node_executable: str | None = None  # /usr/local/bin/node
    node_version: str | None = None  # "20.10.0"
    package_manager: str | None = None  # "npm" | "pnpm" | "yarn" | "bun"
    package_manager_executable: str | None = None  # Full path to pm
    # Go runtime
    go_executable: str | None = None  # /usr/local/go/bin/go
    go_version: str | None = None  # "1.21.5"
    go_mod_path: str | None = None  # Path to go.mod
    # Rust runtime
    cargo_executable: str | None = None  # /home/user/.cargo/bin/cargo
    rust_version: str | None = None  # "1.75.0"
    # Java/JVM runtime
    java_executable: str | None = None
    java_version: str | None = None
    gradle_executable: str | None = None
    maven_executable: str | None = None
    # .NET runtime
    dotnet_executable: str | None = None
    dotnet_version: str | None = None
    # Ruby runtime
    ruby_executable: str | None = None
    ruby_version: str | None = None
    bundle_executable: str | None = None
    # Generic environment overrides (JSON)
    env_vars_json: str | None = None  # {"VIRTUAL_ENV": "/path", ...}
    # Metadata
    resolved_at: float | None = None  # Unix timestamp
    resolution_method: str | None = None  # "venv_detected", "path_fallback", etc.
    def get_env_vars(self) -> dict[str, str]:
        """Parse env_vars_json to dict."""
        if self.env_vars_json is None:
            return {}
        result: dict[str, str] = json.loads(self.env_vars_json)
        return result
    def set_env_vars(self, env_vars: dict[str, str]) -> None:
        """Set env_vars_json from dict."""
        self.env_vars_json = json.dumps(env_vars) if env_vars else None

# Non-Table Models: Tool Configuration

@dataclass
class ToolConfig:
    """Configuration for a specific tool within an execution context.
    Examples:
    - pytest: executable="/repo/.venv/bin/python", base_args=["-m", "pytest"]
    - ruff: executable="/repo/.venv/bin/ruff", base_args=["check"]
    - jest: executable="npx", base_args=["jest"]
    """
    tool_id: str  # "python.pytest", "python.ruff", "js.jest"
    executable: str  # Full path or command name
    base_args: list[str] = field(default_factory=list)  # Default arguments
    env_overrides: dict[str, str] = field(default_factory=dict)  # Tool-specific env
    available: bool = True  # False if tool not installed
    version: str | None = None  # Tool version if detected
@dataclass
class RuntimeExecutionContext:
    """Unified context for executing operations (tests, lints, etc.).
    This is the primary interface for execution - it combines:
    - Context identity and boundaries
    - Runtime environment (executables, versions)
    - Tool configurations
    - Execution constraints
    Passed to runner packs and lint tools at execution time.
    Note: Named RuntimeExecutionContext to distinguish from testing.models.ExecutionContext
    which captures command execution results (command, exit_code, stdout, etc.).
    """
    # Identity (from Context)
    context_id: int
    language_family: str
    root_path: Path  # Absolute path
    # Runtime (from ContextRuntime)
    runtime: ContextRuntime
    # Tool configurations (resolved at context load time)
    test_runners: dict[str, ToolConfig] = field(default_factory=dict)  # pack_id -> config
    linters: dict[str, ToolConfig] = field(default_factory=dict)  # tool_id -> config
    formatters: dict[str, ToolConfig] = field(default_factory=dict)  # tool_id -> config
    # Environment (merged from runtime + context-specific)
    env_vars: dict[str, str] = field(default_factory=dict)
    working_directory: Path | None = None
    # Execution constraints
    timeout_sec: int = 300
    memory_limit_mb: int | None = None
    def get_test_runner(self, pack_id: str) -> ToolConfig | None:
        """Get tool config for a test runner pack."""
        return self.test_runners.get(pack_id)
    def get_linter(self, tool_id: str) -> ToolConfig | None:
        """Get tool config for a linter."""
        return self.linters.get(tool_id)
    def build_env(self, tool_config: ToolConfig | None = None) -> dict[str, str]:
        """Build complete environment for execution.
        Merges in order (later wins):
        1. Current process environment
        2. Runtime env_vars
        3. Context env_vars
        4. Tool-specific env_overrides
        Additionally prepends ``_VENV_BIN`` (captured at runtime resolution)
        to *PATH* so that venv-installed executables (pytest, ruff, …) are
        found even when the daemon process itself was not launched from an
        activated virtualenv.
        """
        import os
        env = dict(os.environ)
        runtime_vars = self.runtime.get_env_vars()
        env.update(runtime_vars)
        env.update(self.env_vars)
        if tool_config:
            env.update(tool_config.env_overrides)
        # Prepend venv bin directory to PATH so shutil.which / subprocess
        # can discover venv-installed tools.
        venv_bin = runtime_vars.get("_VENV_BIN") or self.env_vars.get("_VENV_BIN")
        if venv_bin:
            current_path = env.get("PATH", "")
            if venv_bin not in current_path.split(os.pathsep):
                env["PATH"] = venv_bin + os.pathsep + current_path
        return env

# Runtime Resolution

RuntimeResolutionMethod = Literal[
    "venv_detected",  # Found .venv or similar
    "poetry_detected",  # Found poetry.lock, using poetry run
    "conda_detected",  # Found conda env
    "nvm_detected",  # Found .nvmrc
    "path_detected",  # Found executable in PATH
    "not_found",  # Could not find runtime
]

@dataclass
class RuntimeResolutionResult:
    """Result of resolving runtime for a context."""
    runtime: ContextRuntime
    method: RuntimeResolutionMethod
    warnings: list[str] = field(default_factory=list)
class RuntimeResolver:
    """Resolves execution runtime for a context.
    Called during context discovery/validation to capture the execution
    environment. Results are persisted to ContextRuntime table.
    """
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
    @staticmethod
    def resolve(workspace_root: Path) -> ContextRuntime:
        """Convenience method to resolve runtime for a workspace root.
        This is used at execution time when we need a ContextRuntime but
        don't have a persisted context. For the full context-aware resolution,
        use the instance method resolve_for_context().
        Args:
            workspace_root: Absolute path to workspace root
        Returns:
            ContextRuntime with detected executables
        """
        import time
        runtime = ContextRuntime(context_id=0)  # Dummy ID for non-persisted runtime
        runtime.resolved_at = time.time()
        # Resolve all language runtimes (best-effort)
        resolver = RuntimeResolver(workspace_root)
        warnings: list[str] = []
        # Try Python
        method = resolver._resolve_python(workspace_root, runtime, warnings)
        if method != "not_found":
            runtime.resolution_method = method
        # Try JavaScript
        resolver._resolve_javascript(workspace_root, runtime, warnings)
        # Try Go
        resolver._resolve_go(workspace_root, runtime, warnings)
        # Try Rust
        resolver._resolve_rust(workspace_root, runtime, warnings)
        # Try JVM
        resolver._resolve_jvm(workspace_root, runtime, warnings)
        # Try .NET
        resolver._resolve_dotnet(workspace_root, runtime, warnings)
        # Try Ruby
        resolver._resolve_ruby(workspace_root, runtime, warnings)
        return runtime
    def resolve_for_context(
        self, context_id: int, language_family: str, root_path: str
    ) -> RuntimeResolutionResult:
        """Resolve runtime for a context.
        Args:
            context_id: Database ID of the context
            language_family: Language family string (e.g., "python", "javascript")
            root_path: Relative path to context root ("" for repo root)
        Returns:
            RuntimeResolutionResult with populated ContextRuntime
        """
        import time
        runtime = ContextRuntime(context_id=context_id)
        runtime.resolved_at = time.time()
        warnings: list[str] = []
        method: RuntimeResolutionMethod = "not_found"
        context_root = self.repo_root / root_path if root_path else self.repo_root
        # Dispatch to language-specific resolver
        if language_family == "python":
            method = self._resolve_python(context_root, runtime, warnings)
        elif language_family == "javascript":
            method = self._resolve_javascript(context_root, runtime, warnings)
        elif language_family == "go":
            method = self._resolve_go(context_root, runtime, warnings)
        elif language_family == "rust":
            method = self._resolve_rust(context_root, runtime, warnings)
        elif language_family in ("java", "kotlin", "scala", "groovy"):
            method = self._resolve_jvm(context_root, runtime, warnings)
        elif language_family in ("csharp", "fsharp", "vbnet"):
            method = self._resolve_dotnet(context_root, runtime, warnings)
        elif language_family == "ruby":
            method = self._resolve_ruby(context_root, runtime, warnings)
        else:
            # No specific runtime for this language
            method = "not_found"
        runtime.resolution_method = method
        return RuntimeResolutionResult(runtime=runtime, method=method, warnings=warnings)

    # -- Language-specific resolvers (delegated to runtime_resolvers) --------

    def _resolve_python(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str],
    ) -> RuntimeResolutionMethod:
        return _resolvers._resolve_python(self, context_root, runtime, warnings)

    def _find_python_in_venv(self, venv_path: Path) -> Path | None:
        return _resolvers._find_python_in_venv(venv_path)

    @staticmethod
    def _run_version_check(
        args: list[str], parser: object, timeout: int = 5,
    ) -> str | None:
        return _resolvers._run_version_check(args, parser, timeout)

    def _get_python_version(self, python_exe: Path) -> str | None:
        return _resolvers._get_python_version(python_exe)

    def _get_poetry_python(self, context_root: Path) -> str | None:
        return _resolvers._get_poetry_python(context_root)

    def _set_python_env_vars(self, runtime: ContextRuntime, venv_path: Path) -> None:
        _resolvers._set_python_env_vars(runtime, venv_path)

    def _resolve_javascript(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str],
    ) -> RuntimeResolutionMethod:
        return _resolvers._resolve_javascript(self, context_root, runtime, warnings)

    def _detect_package_manager(self, context_root: Path) -> tuple[str, str | None]:
        return _resolvers._detect_package_manager(self, context_root)

    def _get_node_version(self, node_exe: str) -> str | None:
        return _resolvers._get_node_version(node_exe)

    def _resolve_go(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str],
    ) -> RuntimeResolutionMethod:
        return _resolvers._resolve_go(self, context_root, runtime, warnings)

    def _get_go_version(self, go_exe: str) -> str | None:
        return _resolvers._get_go_version(go_exe)

    def _resolve_rust(
        self, _context_root: Path, runtime: ContextRuntime, warnings: list[str],
    ) -> RuntimeResolutionMethod:
        return _resolvers._resolve_rust(_context_root, runtime, warnings)

    def _get_rust_version(self, cargo_exe: str) -> str | None:
        return _resolvers._get_rust_version(cargo_exe)

    def _resolve_jvm(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str],
    ) -> RuntimeResolutionMethod:
        return _resolvers._resolve_jvm(self, context_root, runtime, warnings)

    def _get_java_version(self, java_exe: str) -> str | None:
        return _resolvers._get_java_version(java_exe)

    def _resolve_dotnet(
        self, _context_root: Path, runtime: ContextRuntime, warnings: list[str],
    ) -> RuntimeResolutionMethod:
        return _resolvers._resolve_dotnet(_context_root, runtime, warnings)

    def _get_dotnet_version(self, dotnet_exe: str) -> str | None:
        return _resolvers._get_dotnet_version(dotnet_exe)

    def _resolve_ruby(
        self, _context_root: Path, runtime: ContextRuntime, warnings: list[str],
    ) -> RuntimeResolutionMethod:
        return _resolvers._resolve_ruby(_context_root, runtime, warnings)

    def _get_ruby_version(self, ruby_exe: str) -> str | None:
        return _resolvers._get_ruby_version(ruby_exe)

from coderecon.testing import runtime_resolvers as _resolvers  # noqa: E402
from coderecon.testing.runtime_builder import ExecutionContextBuilder  # noqa: E402, F401
