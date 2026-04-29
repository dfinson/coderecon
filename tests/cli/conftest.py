"""Shared fixtures for CLI tests."""

from __future__ import annotations

import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository with initial commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, capture_output=True, check=True)

    (repo_path / "README.md").write_text("# Test repo")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True, check=True)

    yield repo_path


@pytest.fixture
def temp_non_git(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary non-git directory."""
    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    yield non_git
