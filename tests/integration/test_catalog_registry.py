"""Integration tests for catalog registry — register, unregister, lookup."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from coderecon.catalog.db import CatalogDB
from coderecon.catalog.models import RepoEntry, WorktreeEntry
from coderecon.catalog.registry import (
    CatalogRegistry,
    _detect_worktree_name,
    _get_current_branch,
    _repo_hash,
    _resolve_git_dir,
)

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = pytest.mark.integration


def _init_git(path: Path) -> None:
    """Helper: git init + first commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True, check=True
    )
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True
    )


@pytest.fixture
def catalog_db(tmp_path: Path) -> CatalogDB:
    """Create a temp CatalogDB."""
    return CatalogDB(home=tmp_path / "coderecon_home")


@pytest.fixture
def registry(catalog_db: CatalogDB) -> CatalogRegistry:
    """Create a CatalogRegistry backed by the temp DB."""
    return CatalogRegistry(catalog_db)


@pytest.fixture
def git_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """A simple git repo for catalog tests."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_git(repo)
    yield repo


class TestRepoHash:
    def test_deterministic(self) -> None:
        h1 = _repo_hash("/some/.git")
        h2 = _repo_hash("/some/.git")
        assert h1 == h2

    def test_different_paths_different_hashes(self) -> None:
        h1 = _repo_hash("/a/.git")
        h2 = _repo_hash("/b/.git")
        assert h1 != h2

    def test_returns_12_chars(self) -> None:
        assert len(_repo_hash("/foo/.git")) == 12


class TestResolveGitDir:
    def test_normal_repo(self, git_repo: Path) -> None:
        git_dir = _resolve_git_dir(git_repo)
        assert git_dir.endswith(".git")
        assert Path(git_dir).is_dir()

    def test_non_git_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Cannot resolve"):
            _resolve_git_dir(tmp_path / "no_such_repo")


class TestDetectWorktreeName:
    def test_main_checkout(self, git_repo: Path) -> None:
        git_dir = _resolve_git_dir(git_repo)
        name, is_main = _detect_worktree_name(git_repo, git_dir)
        assert name == "main"
        assert is_main is True


class TestGetCurrentBranch:
    def test_returns_branch(self, git_repo: Path) -> None:
        branch = _get_current_branch(git_repo)
        # git init creates either 'main' or 'master' depending on config
        assert branch in ("main", "master")

    def test_non_git_returns_none(self, tmp_path: Path) -> None:
        assert _get_current_branch(tmp_path) is None


class TestRegisterRepo:
    def test_register_creates_entries(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo, wt = registry.register(git_repo)
        assert isinstance(repo, RepoEntry)
        assert isinstance(wt, WorktreeEntry)
        assert repo.name == "myrepo"
        assert wt.is_main is True
        assert wt.root_path == str(git_repo.resolve())

    def test_register_idempotent(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo1, wt1 = registry.register(git_repo)
        repo2, wt2 = registry.register(git_repo)
        assert repo1.id == repo2.id
        assert wt1.id == wt2.id

    def test_register_creates_storage_dir(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        repo, _ = registry.register(git_repo)
        assert Path(repo.storage_dir).is_dir()

    def test_register_two_repos(
        self, registry: CatalogRegistry, git_repo: Path, tmp_path: Path
    ) -> None:
        repo2_path = tmp_path / "another"
        repo2_path.mkdir()
        _init_git(repo2_path)

        r1, _ = registry.register(git_repo)
        r2, _ = registry.register(repo2_path)
        assert r1.id != r2.id
        assert r1.git_dir != r2.git_dir

    def test_name_collision_appends_hash(
        self, registry: CatalogRegistry, tmp_path: Path
    ) -> None:
        """Two repos with same directory name get different slugs."""
        dir_a = tmp_path / "group_a" / "proj"
        dir_b = tmp_path / "group_b" / "proj"
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        _init_git(dir_a)
        _init_git(dir_b)

        r1, _ = registry.register(dir_a)
        r2, _ = registry.register(dir_b)
        assert r1.name != r2.name
        # One should be "proj", other "proj-<hash>"
        names = {r1.name, r2.name}
        assert "proj" in names


class TestUnregisterRepo:
    def test_unregister_removes_entries(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        registry.register(git_repo)
        assert registry.unregister(git_repo) is True
        assert registry.lookup_by_path(git_repo) is None

    def test_unregister_nonexistent_returns_false(
        self, registry: CatalogRegistry, tmp_path: Path
    ) -> None:
        assert registry.unregister(tmp_path / "nope") is False

    def test_unregister_last_worktree_removes_repo(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        registry.register(git_repo)
        registry.unregister(git_repo)
        repos = registry.list_repos()
        assert len(repos) == 0


class TestLookup:
    def test_lookup_by_path_exact(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        registry.register(git_repo)
        result = registry.lookup_by_path(git_repo)
        assert result is not None
        assert result[0].name == "myrepo"

    def test_lookup_by_path_subdir(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        """Looking up a subdirectory should walk up and find the repo root."""
        registry.register(git_repo)
        subdir = git_repo / "some" / "nested"
        subdir.mkdir(parents=True)
        result = registry.lookup_by_path(subdir)
        assert result is not None
        assert result[0].name == "myrepo"

    def test_lookup_by_name(self, registry: CatalogRegistry, git_repo: Path) -> None:
        registry.register(git_repo)
        result = registry.lookup_by_name("myrepo")
        assert result is not None
        assert result[1].root_path == str(git_repo.resolve())

    def test_lookup_by_name_missing(self, registry: CatalogRegistry) -> None:
        assert registry.lookup_by_name("no_such_repo") is None

    def test_lookup_by_path_missing(
        self, registry: CatalogRegistry, tmp_path: Path
    ) -> None:
        assert registry.lookup_by_path(tmp_path / "nowhere") is None

    def test_get_repo_name_for_path(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        registry.register(git_repo)
        assert registry.get_repo_name_for_path(git_repo) == "myrepo"
        assert registry.get_repo_name_for_path(git_repo / "src") == "myrepo"

    def test_get_repo_name_for_unregistered(
        self, registry: CatalogRegistry, tmp_path: Path
    ) -> None:
        assert registry.get_repo_name_for_path(tmp_path) is None


class TestListOps:
    def test_list_repos_empty(self, registry: CatalogRegistry) -> None:
        assert registry.list_repos() == []

    def test_list_repos_after_register(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        registry.register(git_repo)
        repos = registry.list_repos()
        assert len(repos) == 1
        assert repos[0].name == "myrepo"

    def test_list_worktrees(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo, _ = registry.register(git_repo)
        wts = registry.list_worktrees(repo.id)  # type: ignore[arg-type]
        assert len(wts) == 1
        assert wts[0].is_main is True

    def test_lookup_worktree(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo, _ = registry.register(git_repo)
        wt = registry.lookup_worktree(repo.id, "main")  # type: ignore[arg-type]
        assert wt is not None
        assert wt.name == "main"

    def test_lookup_worktree_missing(
        self, registry: CatalogRegistry, git_repo: Path
    ) -> None:
        repo, _ = registry.register(git_repo)
        assert registry.lookup_worktree(repo.id, "no_such") is None  # type: ignore[arg-type]

    def test_get_storage_dir(self, registry: CatalogRegistry, git_repo: Path) -> None:
        repo, _ = registry.register(git_repo)
        sd = registry.get_storage_dir(repo)
        assert isinstance(sd, Path)
        assert sd.is_dir()
