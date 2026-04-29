"""Integration tests for CLI commands."""

import json
import logging
import os
from collections.abc import Generator
from pathlib import Path

import subprocess

import pytest
import structlog
from click.testing import CliRunner

from coderecon.cli.main import cli
from coderecon.config import load_config
from coderecon.config.models import LoggingConfig, LogOutputConfig
from coderecon._core.logging import (
    clear_request_id,
    configure_logging,
    get_logger,
    set_request_id,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

@pytest.fixture(autouse=True)
def reset_state() -> Generator[None, None, None]:
    """Reset logging and env state between tests."""
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
    clear_request_id()
    orig = {k: v for k, v in os.environ.items() if k.startswith("CODERECON__")}
    for k in orig:
        del os.environ[k]
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
    clear_request_id()
    for k in list(os.environ.keys()):
        if k.startswith("CODERECON__"):
            del os.environ[k]
    os.environ.update(orig)

@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with initial commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, capture_output=True, check=True)

    # Create a file and commit
    (repo_path / "README.md").write_text("# Test repo")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True, check=True)

    return repo_path

class TestErrorPropagation:
    """Test that errors propagate properly through CLI."""

    def test_given_invalid_config_when_load_then_uses_defaults(self, temp_repo: Path) -> None:
        """Invalid config gracefully falls back to defaults."""
        # Given - init the repo first
        runner.invoke(cli, ["init", str(temp_repo)])

        # Given - corrupt the config
        config_path = temp_repo / ".recon" / "config.yaml"
        config_path.write_text("invalid: yaml: [unterminated")

        # When - load config (should not raise, falls back to defaults)
        config = load_config(repo_root=temp_repo)

        # Then - defaults are used
        assert config.logging.level == "INFO"  # default
        assert config.server.port == 7654  # default

    def test_given_init_error_when_invoke_then_nonzero_exit(self, tmp_path: Path) -> None:
        """CLI returns non-zero exit code on error."""
        # Given - directory without .git
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()

        # When
        result = runner.invoke(cli, ["init", str(non_git)])

        # Then
        assert result.exit_code == 1
        assert "not" in result.output.lower() and "git repository" in result.output.lower()

class TestWorkflows:
    """Test complete user workflows through CLI."""

    def test_given_new_repo_when_init_load_log_then_works(
        self, temp_repo: Path, tmp_path: Path
    ) -> None:
        """Full workflow: init → load config → configure logging → log."""
        # Step 1: Init
        result = runner.invoke(cli, ["init", str(temp_repo)])
        assert result.exit_code == 0

        # Step 2: Load config
        config = load_config(repo_root=temp_repo)
        assert config.logging.level == "INFO"

        # Step 3: Configure logging with file output
        log_file = tmp_path / "workflow.log"
        custom_config = LoggingConfig(
            level=config.logging.level,
            outputs=[LogOutputConfig(format="json", destination=str(log_file))],
        )
        configure_logging(config=custom_config)

        # Step 4: Log with request correlation
        logger = get_logger("workflow")
        set_request_id("workflow-test")
        logger.info("workflow complete", step="final")

        # Verify
        content = log_file.read_text()
        data = json.loads(content.strip())
        assert data["event"] == "workflow complete"
        assert data["request_id"] == "workflow-test"

    def test_given_env_override_when_init_and_load_then_env_wins(self, temp_repo: Path) -> None:
        """Environment variables override file config after init."""
        # Given - init creates default config
        runner.invoke(cli, ["init", str(temp_repo)])

        # Given - env vars set
        os.environ["CODERECON__LOGGING__LEVEL"] = "DEBUG"
        os.environ["CODERECON__SERVER__PORT"] = "3000"

        # When
        config = load_config(repo_root=temp_repo)

        # Then
        assert config.logging.level == "DEBUG"
        assert config.server.port == 3000
