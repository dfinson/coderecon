"""Catalog data models.

SQLModel tables stored in ~/.coderecon/catalog.db.
"""

from __future__ import annotations

import time

from sqlmodel import Field, SQLModel

class RepoEntry(SQLModel, table=True):
    """A registered git repository."""

    __tablename__ = "catalog_repositories"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    git_dir: str = Field(unique=True, index=True)
    storage_dir: str = Field(unique=True)
    default_branch: str | None = None
    registered_at: float = Field(default_factory=time.time)
    last_indexed_at: float | None = None

class WorktreeEntry(SQLModel, table=True):
    """A worktree (or main checkout) within a repository."""

    __tablename__ = "catalog_worktrees"

    id: int | None = Field(default=None, primary_key=True)
    repo_id: int = Field(foreign_key="catalog_repositories.id", index=True)
    name: str = Field(index=True)
    root_path: str = Field(unique=True, index=True)
    branch: str | None = None
    is_main: bool = Field(default=False)
    registered_at: float = Field(default_factory=time.time)
    last_indexed_at: float | None = None
