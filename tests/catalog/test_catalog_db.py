"""Tests for the catalog database and models."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.catalog.db import CatalogDB
from coderecon.catalog.models import RepoEntry, WorktreeEntry


@pytest.fixture
def catalog(tmp_path: Path) -> CatalogDB:
    db = CatalogDB(home=tmp_path / ".coderecon")
    db.create_all()
    return db


class TestCatalogDB:
    def test_creates_home_dir(self, tmp_path: Path) -> None:
        home = tmp_path / ".coderecon"
        assert not home.exists()
        CatalogDB(home=home)
        assert home.exists()

    def test_creates_tables(self, catalog: CatalogDB) -> None:
        assert catalog.db_path.exists()
        with catalog.session() as session:
            # Should be able to query empty tables
            repos = session.query(RepoEntry).all()
            assert repos == []
            wts = session.query(WorktreeEntry).all()
            assert wts == []

    def test_repos_dir(self, catalog: CatalogDB) -> None:
        repos_dir = catalog.repos_dir
        assert repos_dir.exists()
        assert repos_dir.name == "repos"

    def test_insert_and_read(self, catalog: CatalogDB) -> None:
        with catalog.session() as session:
            repo = RepoEntry(
                name="test-repo",
                git_dir="/tmp/test/.git",
                storage_dir="/tmp/storage/abc123",
            )
            session.add(repo)
            session.commit()
            session.refresh(repo)
            assert repo.id is not None
            assert repo.name == "test-repo"

    def test_worktree_fk(self, catalog: CatalogDB) -> None:
        with catalog.session() as session:
            repo = RepoEntry(
                name="myrepo",
                git_dir="/tmp/myrepo/.git",
                storage_dir="/tmp/storage/def456",
            )
            session.add(repo)
            session.flush()

            wt = WorktreeEntry(
                repo_id=repo.id,
                name="main",
                root_path="/tmp/myrepo",
                is_main=True,
            )
            session.add(wt)
            session.commit()
            session.refresh(wt)

            assert wt.repo_id == repo.id
            assert wt.is_main is True
