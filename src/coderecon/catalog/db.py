"""Catalog database management.

Handles creation and access to ~/.coderecon/catalog.db.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine


def _default_coderecon_home() -> Path:
    """Get the global CodeRecon home directory.

    Respects CODERECON_HOME env var, falls back to ~/.coderecon.
    """
    env = os.environ.get("CODERECON_HOME")
    if env:
        return Path(env)
    return Path.home() / ".coderecon"


class CatalogDB:
    """Manages the global catalog SQLite database."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or _default_coderecon_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "catalog.db"
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                f"sqlite:///{self.db_path}",
                echo=False,
                connect_args={"check_same_thread": False},
            )
        return self._engine

    def create_all(self) -> None:
        """Create catalog tables if they don't exist."""
        from coderecon.catalog.models import RepoEntry, WorktreeEntry

        # Only create catalog tables, not the full SQLModel metadata
        tables = [
            RepoEntry.__table__,  # type: ignore[attr-defined]
            WorktreeEntry.__table__,  # type: ignore[attr-defined]
        ]
        from sqlmodel import SQLModel

        SQLModel.metadata.create_all(self.engine, tables=tables)

    def session(self) -> Session:
        """Create a new database session."""
        return Session(self.engine)

    @property
    def repos_dir(self) -> Path:
        """Directory where per-repo storage lives."""
        p = self.home / "repos"
        p.mkdir(parents=True, exist_ok=True)
        return p
