"""Language-specific command sanitization strategies for safe execution.

Extracted from safe_execution_lang.py to keep both modules under 500 LOC.
"""

from __future__ import annotations

from collections.abc import Callable

from coderecon.testing.safe_execution import (
    LanguageFamily,
    SafeExecutionConfig,
)


def _sanitize_python_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Python test commands.

    Removes:
    - Coverage flags if we're injecting our own (strip_coverage_flags=True)
    - Verbose flags that clutter output
    - Watch mode flags
    """
    result = []
    skip_next = False
    for i, arg in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue

        # Strip coverage flags if requested
        if config.strip_coverage_flags:
            if arg.startswith("--cov"):
                # Handle --cov=path and --cov path formats
                if "=" not in arg and i + 1 < len(cmd) and not cmd[i + 1].startswith("-"):
                    skip_next = True
                continue
            if arg == "--cov-report":
                skip_next = True
                continue
            if arg.startswith("--cov-report="):
                continue

        # Remove watch mode
        if arg in ("--watch", "-w", "--watch-all"):
            continue

        # Remove overly verbose flags (we set our own via PYTEST_ADDOPTS)
        if arg in ("-vvv", "-vvvv"):
            result.append("-v")  # Keep single verbose
            continue

        result.append(arg)
    return result


def _sanitize_javascript_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize JavaScript/TypeScript test commands.

    Adds:
    - --forceExit for Jest (prevents hanging)
    - --run for Vitest (prevents watch mode)
    - --no-watchman (prevents file watching hangs)

    Removes:
    - --watch flags
    - Interactive flags
    """
    result = []
    is_jest = any("jest" in arg.lower() for arg in cmd)
    is_vitest = any("vitest" in arg.lower() for arg in cmd)

    for arg in cmd:
        # Remove watch mode flags
        if arg in ("--watch", "-w", "--watchAll", "--watch-all"):
            continue
        # Remove interactive flags
        if arg in ("--interactive", "-i"):
            continue
        result.append(arg)

    # Add safety flags
    if is_jest:
        if "--forceExit" not in result:
            result.append("--forceExit")
        if "--no-watchman" not in result:
            result.append("--no-watchman")
        if "--detectOpenHandles" not in result:
            result.append("--detectOpenHandles")

    if is_vitest and "run" not in result and result and result[0] not in ("vitest",):
        # Ensure run mode (not watch)
        # Find vitest in command and insert 'run' after it
        for i, arg in enumerate(result):
            if "vitest" in arg:
                result.insert(i + 1, "run")
                break
    return result


def _sanitize_go_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Go test commands.

    Adds:
    - -count=1 to disable test caching
    - Timeout if not specified
    """
    result = list(cmd)

    # Add cache-busting flag
    if "-count" not in " ".join(result):
        result.append("-count=1")

    # Add timeout if not present
    has_timeout = any("-timeout" in arg for arg in result)
    if not has_timeout:
        result.append(f"-timeout={config.timeout_sec}s")
    return result


def _sanitize_rust_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Rust test commands.

    Adds:
    - --no-fail-fast for comprehensive test runs
    - Color disabled
    """
    result = list(cmd)

    # Disable color
    if "--color" not in " ".join(result):
        result.append("--color=never")
    return result


def _sanitize_java_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Java/Maven/Gradle commands.

    Adds:
    - Batch mode for Maven
    - No-daemon for Gradle
    - Fail-safe thresholds disabled
    """
    result = list(cmd)

    # Check if Maven
    is_maven = any(arg in ("mvn", "./mvnw", "mvnw") for arg in cmd)
    is_gradle = any(arg in ("gradle", "./gradlew", "gradlew") for arg in cmd)

    if is_maven:
        if "-B" not in result and "--batch-mode" not in result:
            # Insert batch mode early in command
            insert_pos = 1 if len(result) > 1 else len(result)
            result.insert(insert_pos, "-B")
        # Disable fail-on-coverage-threshold
        if "-DfailIfNoTests=false" not in result:
            result.append("-DfailIfNoTests=false")

    if is_gradle:
        if "--no-daemon" not in result:
            insert_pos = 1 if len(result) > 1 else len(result)
            result.insert(insert_pos, "--no-daemon")
        if "--console" not in " ".join(result):
            result.append("--console=plain")
    return result


def _sanitize_csharp_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize .NET test commands.

    Adds:
    - --no-restore to speed up (assume restore done)
    - Verbosity control
    """
    result = list(cmd)

    # Add verbosity control
    if "--verbosity" not in " ".join(result) and "-v" not in result:
        result.append("--verbosity=minimal")
    return result


def _sanitize_cpp_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize C/C++ test commands (CTest, etc).

    Adds:
    - --output-on-failure
    - Parallel execution
    """
    result = list(cmd)
    is_ctest = "ctest" in " ".join(cmd).lower()

    if is_ctest:
        if "--output-on-failure" not in result:
            result.append("--output-on-failure")
        if "--parallel" not in " ".join(result) and "-j" not in result:
            result.extend(["--parallel", "4"])
    return result


def _sanitize_ruby_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Ruby test commands.

    Adds:
    - --format for consistent output
    - Disable color
    """
    result = list(cmd)
    is_rspec = "rspec" in " ".join(cmd).lower()

    if is_rspec:
        if "--no-color" not in result:
            result.append("--no-color")
        # Ensure formatter for parsing
        has_format = any("--format" in arg or arg == "-f" for arg in result)
        if not has_format:
            result.extend(["--format", "documentation"])
    return result


def _sanitize_php_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize PHP test commands.

    Adds:
    - --no-interaction
    - Disable color
    """
    result = list(cmd)
    is_phpunit = "phpunit" in " ".join(cmd).lower()

    if is_phpunit:
        if "--no-interaction" not in result:
            result.append("--no-interaction")
        if "--colors" not in " ".join(result):
            result.append("--colors=never")
    return result


def _sanitize_elixir_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Elixir test commands.

    Adds:
    - --no-start for isolation
    """
    result = list(cmd)
    # Elixir mix test is generally safe
    # Just ensure we're not in watch mode
    if "--stale" in result:
        result.remove("--stale")  # Run all tests, not just stale
    return result


def _sanitize_dart_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Dart/Flutter test commands.

    Adds:
    - --no-pub for speed
    - Reporter format
    """
    result = list(cmd)
    is_flutter = "flutter" in " ".join(cmd).lower()
    if is_flutter and "--no-pub" not in result:
        result.append("--no-pub")
    return result


def _sanitize_swift_cmd(cmd: list[str], config: SafeExecutionConfig) -> list[str]:
    """Sanitize Swift test commands.

    Generally safe, minimal modifications needed.
    """
    result = list(cmd)
    # Swift package manager tests
    if "swift" in cmd and "test" in cmd and "--parallel" not in result:
        result.append("--parallel")
    return result


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_CMD_STRATEGIES: dict[
    LanguageFamily, Callable[[list[str], SafeExecutionConfig], list[str]]
] = {
    "python": _sanitize_python_cmd,
    "javascript": _sanitize_javascript_cmd,
    "typescript": _sanitize_javascript_cmd,
    "go": _sanitize_go_cmd,
    "rust": _sanitize_rust_cmd,
    "java": _sanitize_java_cmd,
    "kotlin": _sanitize_java_cmd,
    "scala": _sanitize_java_cmd,
    "csharp": _sanitize_csharp_cmd,
    "cpp": _sanitize_cpp_cmd,
    "ruby": _sanitize_ruby_cmd,
    "php": _sanitize_php_cmd,
    "elixir": _sanitize_elixir_cmd,
    "dart": _sanitize_dart_cmd,
    "swift": _sanitize_swift_cmd,
}


def sanitize_cmd_for_lang(
    lang: LanguageFamily, cmd: list[str], config: SafeExecutionConfig
) -> list[str]:
    """Return sanitized command for *lang*."""
    strategy = _CMD_STRATEGIES.get(lang)
    if strategy is None:
        return cmd
    return strategy(cmd, config)
