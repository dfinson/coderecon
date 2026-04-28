"""Tests for testing/safe_execution_lang.py — language-specific environment strategies.

Covers get_env_for_lang dispatcher and all per-language env builders.
"""

from pathlib import Path

from coderecon.testing.safe_execution import SafeExecutionConfig, _DELETE_KEY
from coderecon.testing.safe_execution_lang import (
    get_env_for_lang,
    _python_env,
    _javascript_env,
    _go_env,
    _rust_env,
    _java_env,
    _csharp_env,
    _cpp_env,
    _ruby_env,
    _php_env,
    _elixir_env,
    _dart_env,
    _swift_env,
    _unknown_env,
)


def _cfg(tmp_path: Path, **overrides) -> SafeExecutionConfig:
    defaults = dict(
        artifact_dir=tmp_path / "artifacts",
        workspace_root=tmp_path / "ws",
        timeout_sec=300,
    )
    defaults.update(overrides)
    return SafeExecutionConfig(**defaults)


# ===========================================================================
# Dispatcher
# ===========================================================================

class TestGetEnvForLang:
    def test_known_language(self, tmp_path: Path) -> None:
        env = get_env_for_lang("python", _cfg(tmp_path))
        assert "COVERAGE_FILE" in env

    def test_unknown_falls_back(self, tmp_path: Path) -> None:
        env = get_env_for_lang("unknown", _cfg(tmp_path))
        assert env["COVERAGE_FILE"] is _DELETE_KEY

    def test_missing_language_falls_back_to_unknown(self, tmp_path: Path) -> None:
        env = get_env_for_lang("nonexistent", _cfg(tmp_path))
        assert env["COVERAGE_FILE"] is _DELETE_KEY


# ===========================================================================
# Python
# ===========================================================================

class TestPythonEnv:
    def test_coverage_file_isolated(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        env = _python_env(cfg)
        assert cfg.run_id in env["COVERAGE_FILE"]
        assert "coverage" in env["COVERAGE_FILE"]

    def test_hash_seed_deterministic(self, tmp_path: Path) -> None:
        env = _python_env(_cfg(tmp_path))
        assert env["PYTHONHASHSEED"] == "0"

    def test_bytecode_disabled(self, tmp_path: Path) -> None:
        env = _python_env(_cfg(tmp_path))
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_coverage_dir_created(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        _python_env(cfg)
        assert (cfg.artifact_dir / "coverage").exists()

    def test_pytest_addopts(self, tmp_path: Path) -> None:
        env = _python_env(_cfg(tmp_path))
        assert "PYTEST_ADDOPTS" in env


# ===========================================================================
# JavaScript
# ===========================================================================

class TestJavascriptEnv:
    def test_node_env_test(self, tmp_path: Path) -> None:
        env = _javascript_env(_cfg(tmp_path))
        assert env["NODE_ENV"] == "test"

    def test_watchman_disabled(self, tmp_path: Path) -> None:
        env = _javascript_env(_cfg(tmp_path))
        assert env["WATCHMAN_SOCK"] == "/dev/null"

    def test_node_options_memory(self, tmp_path: Path) -> None:
        env = _javascript_env(_cfg(tmp_path, subprocess_memory_limit_mb=2048))
        assert "2048" in env["NODE_OPTIONS"]

    def test_default_memory(self, tmp_path: Path) -> None:
        env = _javascript_env(_cfg(tmp_path))
        assert "4096" in env["NODE_OPTIONS"]


# ===========================================================================
# Go
# ===========================================================================

class TestGoEnv:
    def test_module_mode(self, tmp_path: Path) -> None:
        env = _go_env(_cfg(tmp_path))
        assert env["GO111MODULE"] == "on"

    def test_cover_dir_isolated(self, tmp_path: Path) -> None:
        env = _go_env(_cfg(tmp_path))
        assert "coverage" in env["GOCOVERDIR"]

    def test_memory_limit(self, tmp_path: Path) -> None:
        env = _go_env(_cfg(tmp_path, subprocess_memory_limit_mb=512))
        assert env["GOMEMLIMIT"] == "512MiB"

    def test_no_memory_limit_by_default(self, tmp_path: Path) -> None:
        env = _go_env(_cfg(tmp_path))
        assert "GOMEMLIMIT" not in env


# ===========================================================================
# Rust
# ===========================================================================

class TestRustEnv:
    def test_profile_file_isolated(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        env = _rust_env(cfg)
        assert cfg.run_id in env["LLVM_PROFILE_FILE"]

    def test_cargo_no_color(self, tmp_path: Path) -> None:
        env = _rust_env(_cfg(tmp_path))
        assert env["CARGO_TERM_COLOR"] == "never"


# ===========================================================================
# Java
# ===========================================================================

class TestJavaEnv:
    def test_gradle_daemon_disabled(self, tmp_path: Path) -> None:
        env = _java_env(_cfg(tmp_path))
        assert "daemon=false" in env["GRADLE_OPTS"]

    def test_maven_batch_mode(self, tmp_path: Path) -> None:
        env = _java_env(_cfg(tmp_path))
        assert env["MAVEN_BATCH_MODE"] == "true"

    def test_jvm_heap_with_limit(self, tmp_path: Path) -> None:
        env = _java_env(_cfg(tmp_path, subprocess_memory_limit_mb=1024))
        assert "-Xmx1024m" in env["_JAVA_OPTIONS"]

    def test_kotlin_daemon_disabled(self, tmp_path: Path) -> None:
        env = _java_env(_cfg(tmp_path))
        assert env["KOTLIN_DAEMON_ENABLED"] == "false"


# ===========================================================================
# C#
# ===========================================================================

class TestCsharpEnv:
    def test_telemetry_disabled(self, tmp_path: Path) -> None:
        env = _csharp_env(_cfg(tmp_path))
        assert env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"

    def test_coverlet_output(self, tmp_path: Path) -> None:
        env = _csharp_env(_cfg(tmp_path))
        assert "COVERLET_OUTPUT" in env
        assert env["COVERLET_OUTPUT_FORMAT"] == "cobertura"

    def test_gc_heap_limit_with_ceiling(self, tmp_path: Path) -> None:
        env = _csharp_env(_cfg(tmp_path, subprocess_memory_limit_mb=256))
        assert "DOTNET_GCHeapHardLimit" in env


# ===========================================================================
# C++
# ===========================================================================

class TestCppEnv:
    def test_gcov_prefix(self, tmp_path: Path) -> None:
        env = _cpp_env(_cfg(tmp_path))
        assert "GCOV_PREFIX" in env

    def test_ctest_output_on_failure(self, tmp_path: Path) -> None:
        env = _cpp_env(_cfg(tmp_path))
        assert env["CTEST_OUTPUT_ON_FAILURE"] == "1"


# ===========================================================================
# Ruby
# ===========================================================================

class TestRubyEnv:
    def test_rails_env(self, tmp_path: Path) -> None:
        env = _ruby_env(_cfg(tmp_path))
        assert env["RAILS_ENV"] == "test"

    def test_spring_disabled(self, tmp_path: Path) -> None:
        env = _ruby_env(_cfg(tmp_path))
        assert env["DISABLE_SPRING"] == "1"


# ===========================================================================
# PHP
# ===========================================================================

class TestPhpEnv:
    def test_xdebug_coverage(self, tmp_path: Path) -> None:
        env = _php_env(_cfg(tmp_path))
        assert env["XDEBUG_MODE"] == "coverage"

    def test_composer_no_interaction(self, tmp_path: Path) -> None:
        env = _php_env(_cfg(tmp_path))
        assert env["COMPOSER_NO_INTERACTION"] == "1"

    def test_memory_limit_with_ceiling(self, tmp_path: Path) -> None:
        env = _php_env(_cfg(tmp_path, subprocess_memory_limit_mb=128))
        assert env["PHP_MEMORY_LIMIT"] == "128M"

    def test_memory_unlimited_by_default(self, tmp_path: Path) -> None:
        env = _php_env(_cfg(tmp_path))
        assert env["PHP_MEMORY_LIMIT"] == "-1"


# ===========================================================================
# Elixir
# ===========================================================================

class TestElixirEnv:
    def test_mix_env(self, tmp_path: Path) -> None:
        env = _elixir_env(_cfg(tmp_path))
        assert env["MIX_ENV"] == "test"

    def test_erl_flags_with_limit(self, tmp_path: Path) -> None:
        env = _elixir_env(_cfg(tmp_path, subprocess_memory_limit_mb=1024))
        assert env["ERL_FLAGS"] == "+MBs 1024"


# ===========================================================================
# Dart
# ===========================================================================

class TestDartEnv:
    def test_analytics_disabled(self, tmp_path: Path) -> None:
        env = _dart_env(_cfg(tmp_path))
        assert env["FLUTTER_SUPPRESS_ANALYTICS"] == "true"

    def test_ci_flag(self, tmp_path: Path) -> None:
        env = _dart_env(_cfg(tmp_path))
        assert env["CI"] == "true"


# ===========================================================================
# Swift
# ===========================================================================

class TestSwiftEnv:
    def test_deterministic_hashing(self, tmp_path: Path) -> None:
        env = _swift_env(_cfg(tmp_path))
        assert env["SWIFT_DETERMINISTIC_HASHING"] == "1"

    def test_profile_isolated(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        env = _swift_env(cfg)
        assert cfg.run_id in env["LLVM_PROFILE_FILE"]


# ===========================================================================
# Unknown
# ===========================================================================

class TestUnknownEnv:
    def test_deletes_coverage_vars(self, tmp_path: Path) -> None:
        env = _unknown_env(_cfg(tmp_path))
        assert env["COVERAGE_FILE"] is _DELETE_KEY
        assert env["COVERAGE_PROCESS_START"] is _DELETE_KEY
