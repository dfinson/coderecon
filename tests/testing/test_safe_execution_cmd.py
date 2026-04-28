"""Tests for testing/safe_execution_cmd.py — language-specific command sanitization.

Covers sanitize_cmd_for_lang dispatcher and all per-language strategies.
"""

from pathlib import Path

from coderecon.testing.safe_execution import SafeExecutionConfig
from coderecon.testing.safe_execution_cmd import (
    sanitize_cmd_for_lang,
    _sanitize_python_cmd,
    _sanitize_javascript_cmd,
    _sanitize_go_cmd,
    _sanitize_rust_cmd,
    _sanitize_java_cmd,
    _sanitize_csharp_cmd,
    _sanitize_cpp_cmd,
    _sanitize_ruby_cmd,
    _sanitize_php_cmd,
    _sanitize_elixir_cmd,
    _sanitize_dart_cmd,
    _sanitize_swift_cmd,
)


def _cfg(*, strip_coverage: bool = False, timeout: int = 300) -> SafeExecutionConfig:
    return SafeExecutionConfig(
        artifact_dir=Path("/tmp/art"),
        workspace_root=Path("/tmp/ws"),
        timeout_sec=timeout,
        strip_coverage_flags=strip_coverage,
    )


# ===========================================================================
# Dispatcher
# ===========================================================================

class TestSanitizeCmdForLang:
    def test_known_language_dispatches(self) -> None:
        result = sanitize_cmd_for_lang("python", ["pytest", "-v"], _cfg())
        assert isinstance(result, list)

    def test_unknown_language_passthrough(self) -> None:
        cmd = ["some", "command"]
        assert sanitize_cmd_for_lang("unknown", cmd, _cfg()) is cmd


# ===========================================================================
# Python
# ===========================================================================

class TestPythonCmd:
    def test_strips_coverage_flags(self) -> None:
        cmd = ["pytest", "--cov=src", "--cov-report=xml", "tests/"]
        result = _sanitize_python_cmd(cmd, _cfg(strip_coverage=True))
        assert "--cov=src" not in result
        assert "--cov-report=xml" not in result
        assert "tests/" in result

    def test_strips_cov_with_space_arg(self) -> None:
        cmd = ["pytest", "--cov", "src", "tests/"]
        result = _sanitize_python_cmd(cmd, _cfg(strip_coverage=True))
        assert "--cov" not in result
        assert "src" not in result

    def test_strips_cov_report_with_space(self) -> None:
        cmd = ["pytest", "--cov-report", "html", "tests/"]
        result = _sanitize_python_cmd(cmd, _cfg(strip_coverage=True))
        assert "--cov-report" not in result
        assert "html" not in result

    def test_keeps_coverage_flags_when_not_stripping(self) -> None:
        cmd = ["pytest", "--cov=src"]
        result = _sanitize_python_cmd(cmd, _cfg(strip_coverage=False))
        assert "--cov=src" in result

    def test_removes_watch_mode(self) -> None:
        cmd = ["pytest", "--watch", "tests/"]
        result = _sanitize_python_cmd(cmd, _cfg())
        assert "--watch" not in result

    def test_downgrades_verbose(self) -> None:
        cmd = ["pytest", "-vvv", "tests/"]
        result = _sanitize_python_cmd(cmd, _cfg())
        assert "-vvv" not in result
        assert "-v" in result

    def test_passthrough_normal_args(self) -> None:
        cmd = ["pytest", "-x", "--timeout=30", "tests/unit/"]
        result = _sanitize_python_cmd(cmd, _cfg())
        assert result == cmd


# ===========================================================================
# JavaScript
# ===========================================================================

class TestJavascriptCmd:
    def test_removes_watch_flags(self) -> None:
        cmd = ["jest", "--watch", "--watchAll"]
        result = _sanitize_javascript_cmd(cmd, _cfg())
        assert "--watch" not in result
        assert "--watchAll" not in result

    def test_adds_force_exit_for_jest(self) -> None:
        cmd = ["jest"]
        result = _sanitize_javascript_cmd(cmd, _cfg())
        assert "--forceExit" in result
        assert "--no-watchman" in result
        assert "--detectOpenHandles" in result

    def test_no_duplicate_jest_flags(self) -> None:
        cmd = ["jest", "--forceExit", "--no-watchman"]
        result = _sanitize_javascript_cmd(cmd, _cfg())
        assert result.count("--forceExit") == 1

    def test_removes_interactive_flags(self) -> None:
        cmd = ["jest", "--interactive", "-i"]
        result = _sanitize_javascript_cmd(cmd, _cfg())
        assert "--interactive" not in result
        assert "-i" not in result


# ===========================================================================
# Go
# ===========================================================================

class TestGoCmd:
    def test_adds_count_flag(self) -> None:
        cmd = ["go", "test", "./..."]
        result = _sanitize_go_cmd(cmd, _cfg())
        assert "-count=1" in result

    def test_no_duplicate_count(self) -> None:
        cmd = ["go", "test", "-count=1", "./..."]
        result = _sanitize_go_cmd(cmd, _cfg())
        assert result.count("-count=1") == 1

    def test_adds_timeout(self) -> None:
        cmd = ["go", "test", "./..."]
        result = _sanitize_go_cmd(cmd, _cfg(timeout=60))
        assert "-timeout=60s" in result

    def test_no_duplicate_timeout(self) -> None:
        cmd = ["go", "test", "-timeout=120s", "./..."]
        result = _sanitize_go_cmd(cmd, _cfg())
        timeout_args = [a for a in result if "-timeout" in a]
        assert len(timeout_args) == 1


# ===========================================================================
# Rust
# ===========================================================================

class TestRustCmd:
    def test_adds_no_color(self) -> None:
        cmd = ["cargo", "test"]
        result = _sanitize_rust_cmd(cmd, _cfg())
        assert "--color=never" in result

    def test_no_duplicate_color(self) -> None:
        cmd = ["cargo", "test", "--color=always"]
        result = _sanitize_rust_cmd(cmd, _cfg())
        assert "--color=never" not in result


# ===========================================================================
# Java / Maven / Gradle
# ===========================================================================

class TestJavaCmd:
    def test_maven_batch_mode(self) -> None:
        cmd = ["mvn", "test"]
        result = _sanitize_java_cmd(cmd, _cfg())
        assert "-B" in result
        assert "-DfailIfNoTests=false" in result

    def test_gradle_no_daemon(self) -> None:
        cmd = ["./gradlew", "test"]
        result = _sanitize_java_cmd(cmd, _cfg())
        assert "--no-daemon" in result
        assert "--console=plain" in result

    def test_no_duplicate_maven_flags(self) -> None:
        cmd = ["mvn", "-B", "test"]
        result = _sanitize_java_cmd(cmd, _cfg())
        assert result.count("-B") == 1


# ===========================================================================
# C#
# ===========================================================================

class TestCsharpCmd:
    def test_adds_minimal_verbosity(self) -> None:
        cmd = ["dotnet", "test"]
        result = _sanitize_csharp_cmd(cmd, _cfg())
        assert "--verbosity=minimal" in result

    def test_no_duplicate_verbosity(self) -> None:
        cmd = ["dotnet", "test", "--verbosity=normal"]
        result = _sanitize_csharp_cmd(cmd, _cfg())
        assert "--verbosity=minimal" not in result


# ===========================================================================
# C++
# ===========================================================================

class TestCppCmd:
    def test_ctest_output_on_failure(self) -> None:
        cmd = ["ctest"]
        result = _sanitize_cpp_cmd(cmd, _cfg())
        assert "--output-on-failure" in result
        assert "--parallel" in result

    def test_non_ctest_passthrough(self) -> None:
        cmd = ["./build/test_runner"]
        result = _sanitize_cpp_cmd(cmd, _cfg())
        assert result == ["./build/test_runner"]


# ===========================================================================
# Ruby
# ===========================================================================

class TestRubyCmd:
    def test_rspec_no_color(self) -> None:
        cmd = ["rspec"]
        result = _sanitize_ruby_cmd(cmd, _cfg())
        assert "--no-color" in result
        assert "--format" in result
        assert "documentation" in result


# ===========================================================================
# PHP
# ===========================================================================

class TestPhpCmd:
    def test_phpunit_no_interaction(self) -> None:
        cmd = ["phpunit"]
        result = _sanitize_php_cmd(cmd, _cfg())
        assert "--no-interaction" in result
        assert "--colors=never" in result


# ===========================================================================
# Elixir
# ===========================================================================

class TestElixirCmd:
    def test_removes_stale_flag(self) -> None:
        cmd = ["mix", "test", "--stale"]
        result = _sanitize_elixir_cmd(cmd, _cfg())
        assert "--stale" not in result


# ===========================================================================
# Dart
# ===========================================================================

class TestDartCmd:
    def test_flutter_no_pub(self) -> None:
        cmd = ["flutter", "test"]
        result = _sanitize_dart_cmd(cmd, _cfg())
        assert "--no-pub" in result

    def test_dart_passthrough(self) -> None:
        cmd = ["dart", "test"]
        result = _sanitize_dart_cmd(cmd, _cfg())
        assert result == ["dart", "test"]


# ===========================================================================
# Swift
# ===========================================================================

class TestSwiftCmd:
    def test_swift_adds_parallel(self) -> None:
        cmd = ["swift", "test"]
        result = _sanitize_swift_cmd(cmd, _cfg())
        assert "--parallel" in result

    def test_no_parallel_when_present(self) -> None:
        cmd = ["swift", "test", "--parallel"]
        result = _sanitize_swift_cmd(cmd, _cfg())
        assert result.count("--parallel") == 1
