"""Environment detection — Python venv, coverage tools, Node package managers."""

from __future__ import annotations

import os
import shutil
import structlog
from pathlib import Path

from coderecon.testing.runtime import RuntimeExecutionContext

log = structlog.get_logger(__name__)

# Cache for coverage tool detection - keyed by (workspace_root, runner_pack_id)
_coverage_tools_cache: dict[tuple[Path, str], dict[str, bool]] = {}


def detect_python_venv(workspace_root: Path) -> Path | None:
    """Detect Python virtual environment in workspace."""
    # Check common venv locations
    for venv_name in [".venv", "venv", ".env", "env"]:
        venv_path = workspace_root / venv_name
        if venv_path.is_dir():
            # Verify it's a venv by checking for pyvenv.cfg or activate script
            if (venv_path / "pyvenv.cfg").exists():
                return venv_path
            # Windows style
            if (venv_path / "Scripts" / "activate").exists():
                return venv_path
            # Unix style
            if (venv_path / "bin" / "activate").exists():
                return venv_path
    return None

def get_python_executable(workspace_root: Path) -> str:
    """Get Python executable, preferring venv if present."""
    venv = detect_python_venv(workspace_root)
    if venv:
        # Check for Windows first
        win_python = venv / "Scripts" / "python.exe"
        if win_python.exists():
            return str(win_python)
        # Unix
        unix_python = venv / "bin" / "python"
        if unix_python.exists():
            return str(unix_python)
    return "python"

def clear_coverage_tools_cache() -> None:
    """Clear the coverage tools cache. Useful for testing."""
    _coverage_tools_cache.clear()

def detect_coverage_tools(
    workspace_root: Path,
    runner_pack_id: str,
    exec_ctx: RuntimeExecutionContext | None = None,
) -> dict[str, bool]:
    """Detect available coverage tools for a runner pack.
    Returns a dict of tool_name -> is_available.
    Results are cached per (workspace_root, runner_pack_id) to avoid
    spawning subprocess for every test target.
    """
    cache_key = (workspace_root, runner_pack_id)
    if cache_key in _coverage_tools_cache:
        return _coverage_tools_cache[cache_key]
    tools: dict[str, bool] = {}
    if runner_pack_id == "python.pytest":
        # Check if pytest-cov is installed
        # Use RuntimeExecutionContext if available, otherwise fallback to venv detection
        if exec_ctx and exec_ctx.runtime.python_executable:
            python_exe = exec_ctx.runtime.python_executable
        else:
            python_exe = get_python_executable(workspace_root)
        try:
            import subprocess
            result = subprocess.run(
                [python_exe, "-c", "import pytest_cov"],
                capture_output=True,
                timeout=5,
                cwd=workspace_root,
            )
            tools["pytest-cov"] = result.returncode == 0
        except FileNotFoundError:
            # Python executable not found
            tools["pytest-cov"] = False
        except subprocess.TimeoutExpired:
            # Import check timed out
            tools["pytest-cov"] = False
        except subprocess.SubprocessError:
            # Other subprocess errors
            tools["pytest-cov"] = False
    elif runner_pack_id in ("js.jest", "js.vitest"):
        # Jest and Vitest have built-in coverage
        tools["built-in"] = True
    elif runner_pack_id == "go.gotest":
        # Go has built-in coverage
        tools["built-in"] = True
    elif runner_pack_id in ("rust.nextest", "rust.cargotest"):
        # Check for cargo-llvm-cov
        tools["cargo-llvm-cov"] = shutil.which("cargo-llvm-cov") is not None
    elif runner_pack_id == "ruby.rspec":
        # Check for simplecov in Gemfile
        gemfile = workspace_root / "Gemfile"
        if gemfile.exists():
            tools["simplecov"] = "simplecov" in gemfile.read_text()
    elif runner_pack_id == "php.phpunit":
        # Check for xdebug or pcov
        tools["xdebug"] = shutil.which("php") is not None  # Simplified check
        tools["pcov"] = False  # Would need PHP extension check
    # Cache the result
    _coverage_tools_cache[cache_key] = tools
    return tools

def detect_node_package_manager(workspace_root: Path) -> str:
    """Detect which Node package manager to use."""
    if (workspace_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (workspace_root / "yarn.lock").exists():
        return "yarn"
    if (workspace_root / "bun.lockb").exists():
        return "bun"
    return "npm"

def _default_parallelism() -> int:
    """Compute default parallelism based on CPU count."""
    cpu_count = os.cpu_count() or 4
    # Use 2x CPU count for I/O-bound test execution, capped at reasonable max
    return min(cpu_count * 2, 16)
