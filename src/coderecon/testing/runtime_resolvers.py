"""Language-specific runtime resolver functions.

Extracted from RuntimeResolver to keep runtime.py under 500 LOC.
Each ``_resolve_<language>`` function encapsulates discovery of executables,
versions, and environment variables for a single language ecosystem.
"""

from __future__ import annotations

import shutil
import subprocess
import structlog
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from coderecon.testing.runtime import ContextRuntime, RuntimeResolutionMethod

if TYPE_CHECKING:
    from coderecon.testing.runtime import RuntimeResolver

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

def _resolve_python(
    resolver: RuntimeResolver, context_root: Path, runtime: ContextRuntime, warnings: list[str],
) -> RuntimeResolutionMethod:
    """Resolve Python runtime.

    Resolution order:
    1. Context-local venv (.venv, venv, .env, env)
    2. Workspace-level venv (check parent directories up to repo root)
    3. Poetry environment (if poetry.lock exists)
    4. System Python via PATH
    """
    venv_names = [".venv", "venv", ".env", "env"]
    for venv_name in venv_names:
        venv_path = context_root / venv_name
        python_exe = _find_python_in_venv(venv_path)
        if python_exe:
            runtime.python_executable = str(python_exe)
            runtime.python_venv_path = str(venv_path)
            runtime.python_version = _get_python_version(python_exe)
            _set_python_env_vars(runtime, venv_path)
            return "venv_detected"

    # Check parent directories up to repo root
    current = context_root
    while current != resolver.repo_root and current != current.parent:
        current = current.parent
        for venv_name in venv_names:
            venv_path = current / venv_name
            python_exe = _find_python_in_venv(venv_path)
            if python_exe:
                runtime.python_executable = str(python_exe)
                runtime.python_venv_path = str(venv_path)
                runtime.python_version = _get_python_version(python_exe)
                _set_python_env_vars(runtime, venv_path)
                return "venv_detected"

    # Check repo root explicitly
    for venv_name in venv_names:
        venv_path = resolver.repo_root / venv_name
        python_exe = _find_python_in_venv(venv_path)
        if python_exe:
            runtime.python_executable = str(python_exe)
            runtime.python_venv_path = str(venv_path)
            runtime.python_version = _get_python_version(python_exe)
            _set_python_env_vars(runtime, venv_path)
            return "venv_detected"

    # Check for Poetry
    if (context_root / "poetry.lock").exists() or (resolver.repo_root / "poetry.lock").exists():
        poetry_exe = shutil.which("poetry")
        if poetry_exe:
            poetry_python = _get_poetry_python(context_root)
            if poetry_python:
                runtime.python_executable = poetry_python
                runtime.python_version = _get_python_version(Path(poetry_python))
                return "poetry_detected"

    # Fallback to system Python
    system_python = shutil.which("python3") or shutil.which("python")
    if system_python:
        runtime.python_executable = system_python
        runtime.python_version = _get_python_version(Path(system_python))
        warnings.append(
            f"Using system Python: {system_python}. Consider creating a virtual environment."
        )
        return "path_detected"

    warnings.append("No Python executable found")
    return "not_found"


def _find_python_in_venv(venv_path: Path) -> Path | None:
    """Find Python executable in a venv directory."""
    if not venv_path.is_dir():
        return None
    has_pyvenv_cfg = (venv_path / "pyvenv.cfg").exists()
    has_unix_activate = (venv_path / "bin" / "activate").exists()
    has_win_activate = (venv_path / "Scripts" / "activate").exists()
    if not (has_pyvenv_cfg or has_unix_activate or has_win_activate):
        return None
    unix_python = venv_path / "bin" / "python"
    if unix_python.exists():
        return unix_python
    win_python = venv_path / "Scripts" / "python.exe"
    if win_python.exists():
        return win_python
    return None


def _run_version_check(
    args: list[str],
    parser: Callable[[subprocess.CompletedProcess[str]], str | None],
    timeout: int = 5,
) -> str | None:
    """Run a subprocess version check and parse the output."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        return parser(result)
    except FileNotFoundError:
        log.debug("version_check_exe_not_found", exc_info=True)
    except subprocess.TimeoutExpired:
        log.debug("version_check_timed_out", exc_info=True)
    except subprocess.SubprocessError:
        log.debug("version_check_subprocess_error", exc_info=True)
    return None


def _get_python_version(python_exe: Path) -> str | None:
    """Get Python version from executable."""
    def _parse(r: subprocess.CompletedProcess[str]) -> str | None:
        if r.returncode == 0:
            return r.stdout.strip().split()[-1]
        return None
    return _run_version_check([str(python_exe), "--version"], _parse)


def _get_poetry_python(context_root: Path) -> str | None:
    """Get Python executable from Poetry environment."""
    try:
        result = subprocess.run(
            ["poetry", "env", "info", "-e"],
            capture_output=True,
            timeout=10,
            text=True,
            cwd=context_root,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        log.debug("poetry_not_found", exc_info=True)
    except subprocess.TimeoutExpired:
        log.debug("poetry_env_timed_out", exc_info=True)
    except subprocess.SubprocessError:
        log.debug("poetry_env_subprocess_error", exc_info=True)
    return None


def _set_python_env_vars(runtime: ContextRuntime, venv_path: Path) -> None:
    """Set Python-specific environment variables."""
    env_vars = runtime.get_env_vars()
    env_vars["VIRTUAL_ENV"] = str(venv_path)
    bin_dir = venv_path / "bin" if (venv_path / "bin").exists() else venv_path / "Scripts"
    env_vars["_VENV_BIN"] = str(bin_dir)
    runtime.set_env_vars(env_vars)


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------

def _resolve_javascript(
    resolver: RuntimeResolver, context_root: Path, runtime: ContextRuntime, warnings: list[str],
) -> RuntimeResolutionMethod:
    """Resolve JavaScript/Node runtime."""
    pm, pm_exe = _detect_package_manager(resolver, context_root)
    runtime.package_manager = pm
    runtime.package_manager_executable = pm_exe

    nvmrc_path = context_root / ".nvmrc"
    has_nvmrc = nvmrc_path.exists()
    if not has_nvmrc:
        nvmrc_path = resolver.repo_root / ".nvmrc"
        has_nvmrc = nvmrc_path.exists()

    node_exe = shutil.which("node")
    if node_exe:
        runtime.node_executable = node_exe
        runtime.node_version = _get_node_version(node_exe)
        return "nvm_detected" if has_nvmrc else "path_detected"

    warnings.append("No Node.js executable found")
    return "not_found"


def _detect_package_manager(
    resolver: RuntimeResolver, context_root: Path,
) -> tuple[str, str | None]:
    """Detect JavaScript package manager."""
    checks = [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
    ]
    for lockfile, pm in checks:
        if (context_root / lockfile).exists() or (resolver.repo_root / lockfile).exists():
            exe = shutil.which(pm)
            return (pm, exe)
    npm_exe = shutil.which("npm")
    return ("npm", npm_exe)


def _get_node_version(node_exe: str) -> str | None:
    """Get Node.js version."""
    def _parse(r: subprocess.CompletedProcess[str]) -> str | None:
        if r.returncode == 0:
            return r.stdout.strip().lstrip("v")
        return None
    return _run_version_check([node_exe, "--version"], _parse)


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

def _resolve_go(
    resolver: RuntimeResolver, context_root: Path, runtime: ContextRuntime, warnings: list[str],
) -> RuntimeResolutionMethod:
    """Resolve Go runtime."""
    go_exe = shutil.which("go")
    if go_exe:
        runtime.go_executable = go_exe
        runtime.go_version = _get_go_version(go_exe)
        go_mod = context_root / "go.mod"
        if not go_mod.exists():
            go_mod = resolver.repo_root / "go.mod"
        if go_mod.exists():
            runtime.go_mod_path = str(go_mod)
        return "path_detected"

    warnings.append("No Go executable found")
    return "not_found"


def _get_go_version(go_exe: str) -> str | None:
    """Get Go version."""
    def _parse(r: subprocess.CompletedProcess[str]) -> str | None:
        if r.returncode == 0:
            parts = r.stdout.split()
            if len(parts) >= 3:
                return parts[2].lstrip("go")
        return None
    return _run_version_check([go_exe, "version"], _parse)


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

def _resolve_rust(
    _context_root: Path, runtime: ContextRuntime, warnings: list[str],
) -> RuntimeResolutionMethod:
    """Resolve Rust runtime."""
    cargo_exe = shutil.which("cargo")
    if cargo_exe:
        runtime.cargo_executable = cargo_exe
        runtime.rust_version = _get_rust_version(cargo_exe)
        return "path_detected"

    warnings.append("No Cargo executable found")
    return "not_found"


def _get_rust_version(cargo_exe: str) -> str | None:
    """Get Rust version via cargo."""
    def _parse(r: subprocess.CompletedProcess[str]) -> str | None:
        if r.returncode == 0:
            parts = r.stdout.split()
            if len(parts) >= 2:
                return parts[1]
        return None
    return _run_version_check([cargo_exe, "--version"], _parse)


# ---------------------------------------------------------------------------
# JVM (Java / Kotlin / Scala)
# ---------------------------------------------------------------------------

def _resolve_jvm(
    resolver: RuntimeResolver, context_root: Path, runtime: ContextRuntime, warnings: list[str],
) -> RuntimeResolutionMethod:
    """Resolve JVM (Java/Kotlin/Scala) runtime."""
    java_exe = shutil.which("java")
    if java_exe:
        runtime.java_executable = java_exe
        runtime.java_version = _get_java_version(java_exe)

        gradlew = context_root / "gradlew"
        if not gradlew.exists():
            gradlew = resolver.repo_root / "gradlew"
        if gradlew.exists():
            runtime.gradle_executable = str(gradlew)
        else:
            gradle = shutil.which("gradle")
            if gradle:
                runtime.gradle_executable = gradle

        mvnw = context_root / "mvnw"
        if not mvnw.exists():
            mvnw = resolver.repo_root / "mvnw"
        if mvnw.exists():
            runtime.maven_executable = str(mvnw)
        else:
            mvn = shutil.which("mvn")
            if mvn:
                runtime.maven_executable = mvn

        return "path_detected"

    warnings.append("No Java executable found")
    return "not_found"


def _get_java_version(java_exe: str) -> str | None:
    """Get Java version."""
    import re

    def _parse(r: subprocess.CompletedProcess[str]) -> str | None:
        output = r.stderr or r.stdout
        if output:
            for line in output.split("\n"):
                if "version" in line:
                    match = re.search(r'"([^"]+)"', line)
                    if match:
                        return match.group(1)
        return None
    return _run_version_check([java_exe, "-version"], _parse)


# ---------------------------------------------------------------------------
# .NET
# ---------------------------------------------------------------------------

def _resolve_dotnet(
    _context_root: Path, runtime: ContextRuntime, warnings: list[str],
) -> RuntimeResolutionMethod:
    """Resolve .NET runtime."""
    dotnet_exe = shutil.which("dotnet")
    if dotnet_exe:
        runtime.dotnet_executable = dotnet_exe
        runtime.dotnet_version = _get_dotnet_version(dotnet_exe)
        return "path_detected"

    warnings.append("No .NET executable found")
    return "not_found"


def _get_dotnet_version(dotnet_exe: str) -> str | None:
    """Get .NET version."""
    def _parse(r: subprocess.CompletedProcess[str]) -> str | None:
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    return _run_version_check([dotnet_exe, "--version"], _parse)


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------

def _resolve_ruby(
    _context_root: Path, runtime: ContextRuntime, warnings: list[str],
) -> RuntimeResolutionMethod:
    """Resolve Ruby runtime."""
    ruby_exe = shutil.which("ruby")
    if ruby_exe:
        runtime.ruby_executable = ruby_exe
        runtime.ruby_version = _get_ruby_version(ruby_exe)
        bundle_exe = shutil.which("bundle")
        if bundle_exe:
            runtime.bundle_executable = bundle_exe
        return "path_detected"

    warnings.append("No Ruby executable found")
    return "not_found"


def _get_ruby_version(ruby_exe: str) -> str | None:
    """Get Ruby version."""
    def _parse(r: subprocess.CompletedProcess[str]) -> str | None:
        if r.returncode == 0:
            parts = r.stdout.split()
            if len(parts) >= 2:
                return parts[1]
        return None
    return _run_version_check([ruby_exe, "--version"], _parse)
