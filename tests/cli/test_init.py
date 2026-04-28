"""Tests for recon init command."""

from pathlib import Path

import yaml
from click.testing import CliRunner

from coderecon.cli.main import cli

runner = CliRunner()

class TestInitCommand:
    """recon init command tests."""

    def test_given_git_repo_when_init_then_creates_coderecon_dir(self, temp_git_repo: Path) -> None:
        """Init creates .recon/ directory with config."""
        # Given
        repo = temp_git_repo

        # When
        result = runner.invoke(cli, ["init", str(repo)])

        # Then
        assert result.exit_code == 0
        assert (repo / ".recon").is_dir()
        assert (repo / ".recon" / "config.yaml").exists()

    def test_given_git_repo_when_init_then_creates_ignore_file(self, temp_git_repo: Path) -> None:
        """Init creates .recon/.reconignore with default patterns."""
        # Given
        repo = temp_git_repo

        # When
        runner.invoke(cli, ["init", str(repo)])

        # Then
        reconignore_path = repo / ".recon" / ".reconignore"
        assert reconignore_path.exists()
        content = reconignore_path.read_text()
        assert "node_modules/" in content
        assert ".env" in content

    def test_given_git_repo_when_init_then_config_is_valid_yaml(self, temp_git_repo: Path) -> None:
        """Init creates valid YAML config with simplified user fields."""
        # Given
        repo = temp_git_repo

        # When
        runner.invoke(cli, ["init", str(repo)])

        # Then - new simplified config format
        config_path = repo / ".recon" / "config.yaml"
        with config_path.open() as f:
            config = yaml.safe_load(f)
        # New format has root-level fields, not nested
        assert "port" in config

    def test_given_git_repo_when_init_then_creates_state_file(self, temp_git_repo: Path) -> None:
        """Init creates state.yaml with index_path."""
        # Given
        repo = temp_git_repo

        # When
        runner.invoke(cli, ["init", str(repo)])

        # Then - state.yaml should exist with index_path
        state_path = repo / ".recon" / "state.yaml"
        assert state_path.exists()
        with state_path.open() as f:
            state = yaml.safe_load(f)
        assert "index_path" in state

    def test_given_non_git_dir_when_init_then_fails(self, temp_non_git: Path) -> None:
        """Init fails with error when run outside git repository."""
        # Given
        non_git_dir = temp_non_git

        # When
        result = runner.invoke(cli, ["init", str(non_git_dir)])

        # Then
        assert result.exit_code == 1
        assert "Not inside a git repository" in result.output
        assert "CodeRecon commands must be run from within a git repository" in result.output

    def test_given_initialized_repo_when_init_again_then_idempotent(
        self, temp_git_repo: Path
    ) -> None:
        """Init without --reindex is idempotent on already initialized repo."""
        # Given
        repo = temp_git_repo
        runner.invoke(cli, ["init", str(repo)])

        # When
        result = runner.invoke(cli, ["init", str(repo)])

        # Then
        assert result.exit_code == 0
        assert "Already initialized" in result.output

    def test_given_initialized_repo_when_init_reindex_then_reinitializes(
        self, temp_git_repo: Path
    ) -> None:
        """Init with --reindex wipes and rebuilds from scratch."""
        # Given
        repo = temp_git_repo
        runner.invoke(cli, ["init", str(repo)])
        config_path = repo / ".recon" / "config.yaml"
        config_path.write_text("custom: true")

        # When
        result = runner.invoke(cli, ["init", "--reindex", str(repo)])

        # Then
        assert result.exit_code == 0
        assert "Initializing CodeRecon" in result.output
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "custom" not in config
