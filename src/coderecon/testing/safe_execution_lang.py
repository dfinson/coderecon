"""Language-specific environment and command strategies for safe execution.

Extracted from SafeExecutionContext to keep the main module focused on
orchestration while language strategies live here as standalone functions.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from coderecon.config.constants import BYTES_PER_MB
from coderecon.testing.safe_execution import (
    LanguageFamily,
    SafeExecutionConfig,
    _DELETE_KEY,
)


# ---------------------------------------------------------------------------
# Environment strategies
# ---------------------------------------------------------------------------


def _unknown_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Environment overrides for unknown language families.

    Explicitly removes language-specific variables that may have leaked from
    the parent environment (e.g., COVERAGE_FILE when CodeRecon itself runs
    under coverage). Unknown languages should get a minimal, predictable
    environment without Python/Node/etc-specific tooling configuration.

    Uses _DELETE_KEY sentinel to indicate keys that should be removed.
    """
    return {
        # Remove Python coverage variables - these should not leak
        # into tests for unknown languages
        "COVERAGE_FILE": _DELETE_KEY,
        "COVERAGE_PROCESS_START": _DELETE_KEY,
    }


def _python_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Python-specific environment overrides.

    Key protections:
    - COVERAGE_FILE: Prevents SQLite corruption from parallel pytest-cov runs
      by forcing each run to use a unique coverage file path. This overrides
      any pyproject.toml, setup.cfg, or .coveragerc settings.
    - PYTHONDONTWRITEBYTECODE: Prevents __pycache__ pollution
    - PYTEST_CURRENT_TEST: Cleared to avoid test pollution
    """
    # Create isolated coverage file path
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    coverage_file = coverage_dir / f".coverage.{config.run_id}"

    return {
        # CRITICAL: Isolate coverage database to prevent SQLite corruption
        # This overrides any project-level coverage configuration
        "COVERAGE_FILE": str(coverage_file),
        # Force coverage to use parallel mode even if project doesn't set it
        "COVERAGE_PROCESS_START": "",  # Clear any subprocess coverage
        # Prevent bytecode pollution
        "PYTHONDONTWRITEBYTECODE": "1",
        # Disable hash randomization for reproducible test order
        "PYTHONHASHSEED": "0",
        # Clear potentially polluting variables
        "PYTEST_CURRENT_TEST": "",
        # Ensure UTF-8 encoding
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        # Disable warnings that might clutter output
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
        # Prevent pip from prompting
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        # Prevent virtualenv prompts
        "VIRTUAL_ENV_DISABLE_PROMPT": "1",
        # Pytest specific
        "PYTEST_ADDOPTS": "--tb=short -q",  # Override verbose project settings
    }


def _javascript_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """JavaScript/TypeScript environment overrides.

    Key protections:
    - Disables watchman (file watching) which can hang in CI
    - Forces exit after tests (prevents Jest hanging)
    - Disables workers in some scenarios for stability
    """
    # Create isolated coverage directory
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    return {
        # Jest specific
        "JEST_WORKER_ID": "1",  # Consistent worker ID
        # Disable watchman to prevent file watching hangs
        "WATCHMAN_SOCK": "/dev/null",
        # Node.js options
        "NODE_ENV": "test",
        "NODE_OPTIONS": f"--max-old-space-size={config.subprocess_memory_limit_mb or 4096}",
        # Prevent npm/yarn prompts
        "npm_config_yes": "true",
        "YARN_ENABLE_IMMUTABLE_INSTALLS": "false",
        # Disable update checks
        "NO_UPDATE_NOTIFIER": "1",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        # Vitest specific
        "VITEST": "true",
        # Coverage isolation
        "COVERAGE_DIR": str(coverage_dir),
        # Disable interactive mode
        "npm_config_progress": "false",
        # Prevent Nx/Turborepo prompts
        "NX_DAEMON": "false",
        "TURBO_TELEMETRY_DISABLED": "1",
    }


def _go_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Go environment overrides.

    Key protections:
    - Isolates coverage profile to prevent overwrites
    - Disables cgo for faster, more portable builds (optional)
    - Ensures module mode
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    go_env: dict[str, str | object] = {
        # Ensure module mode
        "GO111MODULE": "on",
        # Isolated coverage output
        "GOCOVERDIR": str(coverage_dir),
        # Disable cgo for more portable test runs (can be overridden)
        # "CGO_ENABLED": "0",  # Commented: may break tests that need cgo
        # Reduce test flakiness from timing
        "GOTESTFLAGS": "-count=1",  # Disable test caching
        # Ensure consistent GOPATH behavior
        "GOFLAGS": "-mod=readonly",
        # Prevent prompts
        "GOTELEMETRY": "off",
    }
    limit = config.subprocess_memory_limit_mb
    if limit:
        go_env["GOMEMLIMIT"] = f"{limit}MiB"
    return go_env


def _rust_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Rust environment overrides.

    Key protections:
    - Isolates coverage profile for llvm-cov
    - Controls cargo behavior
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    return {
        # Coverage isolation for llvm-cov
        "LLVM_PROFILE_FILE": str(coverage_dir / f"{config.run_id}-%p-%m.profraw"),
        # Cargo settings
        "CARGO_TERM_COLOR": "never",
        "CARGO_INCREMENTAL": "0",  # Reproducible builds
        # Prevent rustup prompts
        "RUSTUP_TOOLCHAIN": "stable",  # Use stable by default
        # Disable cargo update checks
        "CARGO_NET_OFFLINE": "false",  # Allow network but...
        "CARGO_HTTP_CHECK_REVOKE": "false",  # ...skip cert checks for speed
    }


def _java_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Java/Kotlin/Scala environment overrides.

    Key protections:
    - Disables Gradle daemon (can hang)
    - Prevents Maven interactive mode
    - Isolates JaCoCo output
    - Caps JVM heap when memory ceiling is set
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    limit = config.subprocess_memory_limit_mb
    xmx = f" -Xmx{limit}m" if limit else ""

    return {
        # Gradle settings - disable daemon to prevent orphan processes
        "GRADLE_OPTS": f"-Dorg.gradle.daemon=false -Dorg.gradle.parallel=true{xmx}",
        # Maven settings - batch mode, no prompts
        "MAVEN_OPTS": f"-Djansi.force=false -Dstyle.color=never{xmx}",
        "MAVEN_BATCH_MODE": "true",
        # JaCoCo coverage output directory
        "JACOCO_DESTFILE": str(coverage_dir / "jacoco.exec"),
        # Disable sbt prompts (Scala)
        "SBT_OPTS": f"-Dsbt.ci=true -Dsbt.color=false{xmx}",
        # Prevent Java auto-update prompts + heap cap
        "_JAVA_OPTIONS": f"-Djava.awt.headless=true{xmx}",
        # Kotlin specific
        "KOTLIN_DAEMON_ENABLED": "false",
    }


def _csharp_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """C#/.NET environment overrides.

    Key protections:
    - Disables telemetry
    - Prevents nuget prompts
    - Isolates coverage output
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    env: dict[str, str | object] = {
        # .NET CLI settings
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        # Nuget settings - no prompts
        "NUGET_XMLDOC_MODE": "skip",
        # Coverage output
        "COVERLET_OUTPUT": str(coverage_dir) + "/",
        "COVERLET_OUTPUT_FORMAT": "cobertura",
        # Prevent interactive restore
        "DOTNET_INTERACTIVE": "false",
        # MSBuild settings
        "MSBUILDDISABLENODEREUSE": "1",
    }
    limit = config.subprocess_memory_limit_mb
    if limit:
        # .NET GC hard heap limit in bytes (hex)
        env["DOTNET_GCHeapHardLimit"] = hex(limit * BYTES_PER_MB)
    return env


def _cpp_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """C/C++ environment overrides.

    Key protections:
    - Isolates coverage profiles (gcov/llvm)
    - Controls CMake behavior
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    return {
        # GCC coverage output
        "GCOV_PREFIX": str(coverage_dir),
        "GCOV_PREFIX_STRIP": "0",
        # LLVM coverage output
        "LLVM_PROFILE_FILE": str(coverage_dir / f"{config.run_id}-%p.profraw"),
        # CMake settings
        "CMAKE_COLOR_DIAGNOSTICS": "OFF",
        # CTest settings
        "CTEST_OUTPUT_ON_FAILURE": "1",
        "CTEST_PARALLEL_LEVEL": "4",
    }


def _ruby_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Ruby environment overrides.

    Key protections:
    - Isolates SimpleCov output
    - Disables Spring (Rails preloader that can hang)
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    return {
        # SimpleCov coverage directory
        "COVERAGE": "true",
        "COVERAGE_DIR": str(coverage_dir),
        "SIMPLECOV_COVERAGE_DIR": str(coverage_dir),
        # Disable Spring preloader (can hang)
        "DISABLE_SPRING": "1",
        # Rails environment
        "RAILS_ENV": "test",
        "RACK_ENV": "test",
        # Bundler settings - no prompts
        "BUNDLE_SILENCE_ROOT_WARNING": "1",
        "BUNDLE_DISABLE_SHARED_GEMS": "1",
        # RSpec settings
        "SPEC_OPTS": "--no-color --format documentation",
    }


def _php_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """PHP environment overrides.

    Key protections:
    - Isolates coverage output (Xdebug/PCOV)
    - Disables interactive composer
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    return {
        # Xdebug coverage output
        "XDEBUG_MODE": "coverage",
        # PCOV settings
        "PCOV_ENABLED": "1",
        # Coverage output directory
        "COVERAGE_OUTPUT_DIR": str(coverage_dir),
        # Composer settings - no prompts
        "COMPOSER_NO_INTERACTION": "1",
        "COMPOSER_ALLOW_SUPERUSER": "1",
        # PHPUnit settings
        "PHPUNIT_RESULT_CACHE": str(config.artifact_dir / ".phpunit.result.cache"),
        # Memory limit — use ceiling if set, else unlimited
        "PHP_MEMORY_LIMIT": f"{config.subprocess_memory_limit_mb}M"
        if config.subprocess_memory_limit_mb
        else "-1",
    }


def _elixir_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Elixir environment overrides.

    Key protections:
    - Isolates ExCoveralls output
    - Controls Mix behavior
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    env: dict[str, str | object] = {
        # Mix environment
        "MIX_ENV": "test",
        # Disable prompts
        "MIX_QUIET": "1",
        # ExCoveralls output
        "COVERALLS_REPO_TOKEN": "",  # Clear to prevent accidental uploads  # nosec B105
        # Coverage output
        "EXCOVERALLS_OUTPUT_DIR": str(coverage_dir),
        # Hex settings - no prompts
        "HEX_HTTP_TIMEOUT": "60",
    }
    limit = config.subprocess_memory_limit_mb
    if limit:
        env["ERL_FLAGS"] = f"+MBs {limit}"
    return env


def _dart_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Dart/Flutter environment overrides.

    Key protections:
    - Isolates coverage output
    - Disables analytics
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    return {
        # Dart settings
        "PUB_CACHE": str(config.workspace_root / ".pub-cache"),
        # Flutter settings
        "FLUTTER_SUPPRESS_ANALYTICS": "true",
        "CI": "true",  # Flutter respects this
        # Coverage output
        "DART_COVERAGE_DIR": str(coverage_dir),
    }


def _swift_env(config: SafeExecutionConfig) -> dict[str, str | object]:
    """Swift environment overrides.

    Key protections:
    - Controls Xcode behavior
    - Isolates coverage output
    """
    coverage_dir = config.artifact_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    return {
        # Xcode settings
        "DEVELOPER_DIR": os.environ.get(
            "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer"
        ),
        # Disable derived data caching issues
        "SWIFT_DETERMINISTIC_HASHING": "1",
        # Coverage output
        "LLVM_PROFILE_FILE": str(coverage_dir / f"{config.run_id}-%p.profraw"),
    }


# ---------------------------------------------------------------------------
# Dispatcher functions
# ---------------------------------------------------------------------------

_ENV_STRATEGIES: dict[LanguageFamily, Callable[[SafeExecutionConfig], dict[str, str | object]]] = {
    "python": _python_env,
    "javascript": _javascript_env,
    "typescript": _javascript_env,  # Same as JS
    "go": _go_env,
    "rust": _rust_env,
    "java": _java_env,
    "kotlin": _java_env,  # JVM-based
    "scala": _java_env,  # JVM-based
    "csharp": _csharp_env,
    "cpp": _cpp_env,
    "ruby": _ruby_env,
    "php": _php_env,
    "elixir": _elixir_env,
    "dart": _dart_env,
    "swift": _swift_env,
    "unknown": _unknown_env,
}


def get_env_for_lang(
    lang: LanguageFamily, config: SafeExecutionConfig
) -> dict[str, str | object]:
    """Return environment overrides for *lang*."""
    strategy = _ENV_STRATEGIES.get(lang, _unknown_env)
    return strategy(config)


# Re-export cmd dispatcher so callers can import from either module.
from coderecon.testing.safe_execution_cmd import sanitize_cmd_for_lang  # noqa: E402, F401
