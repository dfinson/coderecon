"""Safe execution context for running tests on untrusted repositories.

This module provides defensive environment isolation to protect CodeRecon from
misconfigurations in target repositories that could cause:
- Data corruption (e.g., SQLite coverage DB race conditions)
- Hangs (e.g., interactive prompts, watchman file watching)
- False failures (e.g., coverage threshold enforcement)
- Resource leaks (e.g., orphaned processes)

Each language has specific defensive strategies documented inline.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Sentinel value indicating a key should be removed from the environment.
# Used by _unknown_env() to strip language-specific variables that may have
# leaked from CodeRecon's own environment (e.g., COVERAGE_FILE).
_DELETE_KEY = object()

@dataclass
class SafeExecutionConfig:
    """Configuration for safe execution context."""
    artifact_dir: Path
    workspace_root: Path
    timeout_sec: int = 300
    # Whether to strip coverage flags from commands (when we inject our own)
    strip_coverage_flags: bool = False
    # Unique run identifier for isolation
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # Per-subprocess memory ceiling (MB). When set, language strategies
    # inject this via their runtime-specific env vars.
    subprocess_memory_limit_mb: int | None = None

LanguageFamily = Literal[
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "csharp",
    "cpp",
    "ruby",
    "php",
    "elixir",
    "dart",
    "swift",
    "kotlin",
    "scala",
    "unknown",
]

def _get_language_family(pack_id: str) -> LanguageFamily:
    """Map pack_id to language family for strategy selection."""
    mappings: dict[str, LanguageFamily] = {
        "python.pytest": "python",
        "python.unittest": "python",
        "python.nose": "python",
        "js.jest": "javascript",
        "js.vitest": "javascript",
        "js.mocha": "javascript",
        "ts.jest": "typescript",
        "ts.vitest": "typescript",
        "go.gotest": "go",
        "rust.nextest": "rust",
        "rust.cargo_test": "rust",
        "java.maven": "java",
        "java.gradle": "java",
        "kotlin.gradle": "kotlin",
        "scala.sbt": "scala",
        "csharp.dotnet": "csharp",
        "cpp.ctest": "cpp",
        "cpp.gtest": "cpp",
        "cpp.catch2": "cpp",
        "ruby.rspec": "ruby",
        "ruby.minitest": "ruby",
        "php.phpunit": "php",
        "php.pest": "php",
        "elixir.exunit": "elixir",
        "dart.darttest": "dart",
        "dart.fluttertest": "dart",
        "swift.xctest": "swift",
    }
    return mappings.get(pack_id, "unknown")

class SafeExecutionContext:
    """Provides defensive environment isolation for test execution.
    Protects against misconfigurations in target repositories by:
    1. Setting environment variables that override project configs
    2. Sanitizing commands to remove dangerous flags
    3. Isolating coverage/artifact files to prevent corruption
    4. Enforcing non-interactive execution modes
    """
    def __init__(self, config: SafeExecutionConfig) -> None:
        self._config = config
        self._temp_dirs: list[Path] = []
    @property
    def config(self) -> SafeExecutionConfig:
        return self._config
    def prepare_environment(
        self,
        pack_id: str,
        *,
        base_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build safe environment variables for test execution.
        Args:
            pack_id: Runner pack identifier (e.g., 'python.pytest')
            base_env: Base environment to extend (defaults to os.environ)
        Returns:
            Environment dict with defensive overrides applied
        """
        env = dict(base_env) if base_env is not None else dict(os.environ)
        # Apply universal protections first
        env.update(self._universal_env_overrides())
        # Apply language-specific protections
        lang = _get_language_family(pack_id)
        strategy = self._get_env_strategy(lang)
        overrides = strategy()
        # Apply overrides: _DELETE_KEY sentinel means "remove this key"
        for key, value in overrides.items():
            if value is _DELETE_KEY:
                env.pop(key, None)
            else:
                env[key] = value  # type: ignore[assignment]
        return env
    def sanitize_command(
        self,
        cmd: list[str],
        pack_id: str,
    ) -> list[str]:
        """Remove or override dangerous flags from test commands.
        Args:
            cmd: Original command list
            pack_id: Runner pack identifier
        Returns:
            Sanitized command list
        """
        if not cmd:
            return cmd
        lang = _get_language_family(pack_id)
        strategy = self._get_cmd_strategy(lang)
        return strategy(list(cmd))  # Copy to avoid mutation
    def cleanup(self) -> None:
        """Clean up any temporary resources created during execution."""
        # Temp dirs are managed by tempfile module, but we track them
        # for explicit cleanup if needed
        self._temp_dirs.clear()
    # Universal Environment Overrides
    def _universal_env_overrides(self) -> dict[str, str]:
        """Environment variables that apply to all languages.
        These enforce non-interactive mode and CI-like behavior.
        """
        return {
            # Signal CI environment - most tools respect this
            "CI": "true",
            "CONTINUOUS_INTEGRATION": "true",
            # Prevent interactive prompts
            "NONINTERACTIVE": "1",
            "DEBIAN_FRONTEND": "noninteractive",
            # Disable color output for cleaner parsing
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
            # Prevent git prompts
            "GIT_TERMINAL_PROMPT": "0",
            # Prevent SSH prompts
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=no",
            # Disable telemetry for various tools
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "GATSBY_TELEMETRY_DISABLED": "1",
            "NEXT_TELEMETRY_DISABLED": "1",
            "NUXT_TELEMETRY_DISABLED": "1",
            "HOMEBREW_NO_ANALYTICS": "1",
            # Disable auto-update prompts
            "HOMEBREW_NO_AUTO_UPDATE": "1",
            "DISABLE_OPENCOLLECTIVE": "1",
            # Prevent browser opening
            "BROWSER": "none",
            # CodeRecon marker
            "CODERECON_EXECUTION": "1",
            "CODERECON_RUN_ID": self._config.run_id,
        }
    # Language-Specific Environment Strategies

    def _get_env_strategy(self, lang: LanguageFamily) -> Callable[[], dict[str, str | object]]:
        """Get environment strategy for language family."""
        from coderecon.testing.safe_execution_lang import get_env_for_lang

        return lambda: get_env_for_lang(lang, self._config)

    # Language-Specific Command Sanitization Strategies

    def _get_cmd_strategy(self, lang: LanguageFamily) -> Callable[[list[str]], list[str]]:
        """Get command sanitization strategy for language family."""
        from coderecon.testing.safe_execution_cmd import sanitize_cmd_for_lang

        return lambda cmd: sanitize_cmd_for_lang(lang, cmd, self._config)

# Factory Function

def create_safe_context(
    artifact_dir: Path,
    workspace_root: Path,
    *,
    timeout_sec: int = 300,
    strip_coverage_flags: bool = False,
) -> SafeExecutionContext:
    """Create a safe execution context for test runs.
    Args:
        artifact_dir: Directory for test artifacts
        workspace_root: Root of the workspace being tested
        timeout_sec: Timeout for test execution
        strip_coverage_flags: Whether to strip coverage flags from commands
    Returns:
        Configured SafeExecutionContext
    """
    config = SafeExecutionConfig(
        artifact_dir=artifact_dir,
        workspace_root=workspace_root,
        timeout_sec=timeout_sec,
        strip_coverage_flags=strip_coverage_flags,
    )
    return SafeExecutionContext(config)
