"""Tests for the catalog registry."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coderecon.catalog.registry import CatalogRegistry

@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a real git repo for testing."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(repo), capture_output=True, check=True,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    return repo

class TestRegister:
    def test_register_new_repo(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo, wt = registry.register(git_repo)
        assert repo.name == "myrepo"
        assert wt.is_main is True
        assert wt.root_path == str(git_repo.resolve())
        assert Path(repo.storage_dir).exists()

    def test_register_idempotent(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo1, wt1 = registry.register(git_repo)
        repo2, wt2 = registry.register(git_repo)
        assert repo1.id == repo2.id
        assert wt1.id == wt2.id

    def test_register_creates_storage_dir(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo, _ = registry.register(git_repo)
        assert Path(repo.storage_dir).is_dir()

class TestUnregister:
    def test_unregister_existing(self, registry: CatalogRegistry, git_repo: Path) -> None:
        registry.register(git_repo)
        assert registry.unregister(git_repo) is True

    def test_unregister_nonexistent(self, registry: CatalogRegistry, tmp_path: Path) -> None:
        assert registry.unregister(tmp_path / "nope") is False

    def test_unregister_removes_repo_when_last_worktree(
        self, registry: CatalogRegistry, git_repo: Path,
    ) -> None:
        registry.register(git_repo)
        registry.unregister(git_repo)
        assert registry.list_repos() == []

class TestLookup:
    def test_lookup_by_path_exact(self, registry: CatalogRegistry, git_repo: Path) -> None:
        registry.register(git_repo)
        result = registry.lookup_by_path(git_repo)
        assert result is not None
        repo, wt = result
        assert repo.name == "myrepo"

    def test_lookup_by_path_subdirectory(self, registry: CatalogRegistry, git_repo: Path) -> None:
        registry.register(git_repo)
        subdir = git_repo / "src" / "deep"
        subdir.mkdir(parents=True)
        result = registry.lookup_by_path(subdir)
        assert result is not None

    def test_lookup_by_path_miss(self, registry: CatalogRegistry, tmp_path: Path) -> None:
        assert registry.lookup_by_path(tmp_path / "nope") is None

    def test_lookup_by_name(self, registry: CatalogRegistry, git_repo: Path) -> None:
        registry.register(git_repo)
        result = registry.lookup_by_name("myrepo")
        assert result is not None
        repo, wt = result
        assert repo.name == "myrepo"
        assert wt.is_main is True

    def test_lookup_by_name_miss(self, registry: CatalogRegistry) -> None:
        assert registry.lookup_by_name("nope") is None

class TestListRepos:
    def test_empty(self, registry: CatalogRegistry) -> None:
        assert registry.list_repos() == []

    def test_multiple_repos(self, registry: CatalogRegistry, tmp_path: Path) -> None:
        for name in ("repo-a", "repo-b"):
            repo_path = tmp_path / name
            repo_path.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo_path), capture_output=True, check=True)
            registry.register(repo_path)

        repos = registry.list_repos()
        names = {r.name for r in repos}
        assert names == {"repo-a", "repo-b"}

class TestDiscoverWorktrees:
    def test_single_repo_returns_root(self, registry: CatalogRegistry, git_repo: Path) -> None:
        paths = registry.discover_worktrees(git_repo)
        assert len(paths) >= 1
        assert git_repo.resolve() in [p.resolve() for p in paths]
