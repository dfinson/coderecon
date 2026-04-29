"""Test fixtures for git module - subprocess-based (no pygit2)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from coderecon.adapters.git import GitOps

if TYPE_CHECKING:
    from collections.abc import Generator

def _run_git(cwd: Path, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command in a directory."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        input=input,
    )

def _init_repo(path: Path, branch: str = "main") -> Path:
    """Initialize a git repo with basic config."""
    path.mkdir(exist_ok=True)
    _run_git(path, "init", "-b", branch)
    _run_git(path, "config", "user.name", "Test User")
    _run_git(path, "config", "user.email", "test@example.com")
    return path

def _make_commit(path: Path, files: dict[str, str], message: str) -> str:
    """Create files and commit. Returns commit SHA."""
    for name, content in files.items():
        f = path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        _run_git(path, "add", name)
    _run_git(path, "commit", "-m", message)
    sha_result = _run_git(path, "rev-parse", "HEAD")
    return sha_result.stdout.strip()

@pytest.fixture
def temp_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository with initial commit. Returns repo path."""
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _make_commit(repo_path, {"README.md": "# Test Repo\n"}, "Initial commit")
    yield repo_path

@pytest.fixture
def bare_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a bare repository for remote testing."""
    bare_path = tmp_path / "bare.git"
    bare_path.mkdir()
    _run_git(bare_path, "init", "--bare")
    yield bare_path

@pytest.fixture
def repo_with_remote(
    temp_repo: Path,
    bare_repo: Path,
) -> Path:
    """Repository with a configured remote."""
    _run_git(temp_repo, "remote", "add", "origin", str(bare_repo.resolve()))
    _run_git(temp_repo, "push", "origin", "main")
    # Ensure bare repo HEAD points to main so clones check out the right branch
    _run_git(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")
    return temp_repo

@pytest.fixture
def repo_with_remote_branch(
    temp_repo: Path,
    bare_repo: Path,
) -> Path:
    """Repository with a remote tracking branch that doesn't exist locally."""
    # Add remote and push main
    _run_git(temp_repo, "remote", "add", "origin", str(bare_repo.resolve()))
    _run_git(temp_repo, "push", "origin", "main")

    # Create branch, add commit, push it
    _run_git(temp_repo, "checkout", "-b", "remote-only")
    _make_commit(temp_repo, {"remote-only.txt": "remote only content\n"}, "Commit on remote-only branch")
    _run_git(temp_repo, "push", "origin", "remote-only")

    # Go back to main and delete local branch
    _run_git(temp_repo, "checkout", "main")
    _run_git(temp_repo, "branch", "-D", "remote-only")

    # Fetch to get remote tracking ref
    _run_git(temp_repo, "fetch", "origin")

    return temp_repo

@pytest.fixture
def repo_with_branches(temp_repo: Path) -> Path:
    """Repository with multiple branches."""
    # Create feature branch at current HEAD
    _run_git(temp_repo, "branch", "feature")

    # Add commit on main
    _make_commit(temp_repo, {"main.txt": "main branch\n"}, "Commit on main")

    # Checkout feature and add commit
    _run_git(temp_repo, "checkout", "feature")
    _make_commit(temp_repo, {"feature.txt": "feature branch\n"}, "Commit on feature")

    # Back to main
    _run_git(temp_repo, "checkout", "main")

    return temp_repo

@pytest.fixture
def repo_with_uncommitted(temp_repo: Path) -> Path:
    """Repository with uncommitted changes."""
    # Staged change
    (temp_repo / "staged.txt").write_text("staged content\n")
    _run_git(temp_repo, "add", "staged.txt")

    # Modified (unstaged)
    (temp_repo / "README.md").write_text("# Modified\n")

    # Untracked
    (temp_repo / "untracked.txt").write_text("untracked\n")

    return temp_repo

@pytest.fixture
def repo_with_conflict(
    temp_repo: Path,
) -> tuple[Path, str]:
    """Repository with a merge conflict setup."""
    # Create branch
    _run_git(temp_repo, "branch", "conflict-branch")

    # Modify on main
    _make_commit(temp_repo, {"conflict.txt": "main content\n"}, "Add conflict.txt on main")

    # Checkout branch and create conflicting change
    _run_git(temp_repo, "checkout", "conflict-branch")
    _make_commit(temp_repo, {"conflict.txt": "branch content\n"}, "Add conflict.txt on branch")

    # Back to main
    _run_git(temp_repo, "checkout", "main")

    return temp_repo, "conflict-branch"

@pytest.fixture
def repo_with_history(temp_repo: Path) -> Path:
    """Repository with multiple commits."""
    for i in range(5):
        _make_commit(temp_repo, {f"file{i}.txt": f"content {i}\n"}, f"Commit {i}")
    return temp_repo

# --- GitOps-returning fixtures ---

@pytest.fixture
def git_repo(tmp_path: Path) -> Generator[tuple[Path, GitOps], None, None]:
    """GitOps wrapper around a fresh repository with initial commit."""
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _make_commit(repo_path, {"README.md": "# Test\n"}, "Initial commit")
    yield repo_path, GitOps(repo_path)

@pytest.fixture
def git_repo_with_commit(git_repo: tuple[Path, GitOps]) -> tuple[Path, GitOps]:
    """GitOps repo with one additional commit beyond initial."""
    repo_path, ops = git_repo
    (repo_path / "file.txt").write_text("content\n")
    ops.stage(["file.txt"])
    ops.commit("Add file")
    return repo_path, ops

@pytest.fixture
def git_repo_with_commits(git_repo: tuple[Path, GitOps]) -> tuple[Path, GitOps, list[str]]:
    """GitOps repo with multiple commits for rebase testing."""
    repo_path, ops = git_repo
    shas = []
    for i in range(5):
        (repo_path / f"file{i}.txt").write_text(f"content {i}\n")
        ops.stage([f"file{i}.txt"])
        sha = ops.commit(f"Commit {i}")
        shas.append(sha)
    return repo_path, ops, shas

@pytest.fixture
def git_repo_with_branch(git_repo: tuple[Path, GitOps]) -> tuple[Path, GitOps, str]:
    """GitOps repo with a feature branch diverged from default branch."""
    repo_path, ops = git_repo
    default_branch = ops.current_branch()
    assert default_branch is not None

    # Add a commit on default branch
    (repo_path / "default.txt").write_text("default content\n")
    ops.stage(["default.txt"])
    ops.commit("Default branch commit")
    default_head = ops.head()

    # Create feature branch from initial commit
    initial_sha = ops.log()[1].sha
    ops.create_branch("feature", initial_sha)
    ops.checkout("feature")

    # Add commits on feature
    (repo_path / "feature.txt").write_text("feature content\n")
    ops.stage(["feature.txt"])
    ops.commit("Feature commit")

    # Return to default branch
    ops.checkout(default_branch)

    return repo_path, ops, default_head.target_sha

@pytest.fixture
def git_repo_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[tuple[Path, GitOps], tuple[Path, GitOps]], None, None]:
    """Two separate repos for testing operations requiring multiple repositories."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")

    # First repo (main)
    main_path = tmp_path / "main_repo"
    _init_repo(main_path)
    _make_commit(main_path, {"README.md": "# Main Repo\n"}, "Initial commit")

    # Second repo (submodule source)
    sub_path = tmp_path / "sub_repo"
    _init_repo(sub_path)
    _make_commit(sub_path, {"lib.py": "# Library\n"}, "Initial lib commit")

    yield (main_path, GitOps(main_path)), (sub_path, GitOps(sub_path))
