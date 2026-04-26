"""Catalog database management.

Handles creation and access to ~/.coderecon/catalog.db.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import DBAPIConnection
    from sqlalchemy.pool import ConnectionPoolEntry


def _configure_catalog_pragmas(dbapi_conn: DBAPIConnection, _connection_record: ConnectionPoolEntry) -> None:
    """Configure SQLite pragmas for the catalog database."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _default_coderecon_home() -> Path:
    """Get the global CodeRecon home directory.

    Respects CODERECON_HOME env var, falls back to ~/.coderecon.
    """
    env = os.environ.get("CODERECON_HOME")
    if env:
        return Path(env)
    return Path.home() / ".coderecon"


def _run_migrations(engine: Engine) -> None:
    """Run Alembic migrations to bring the catalog DB up to date."""
    from alembic import command
    from alembic.config import Config

    migrations_dir = str(Path(__file__).parent / "migrations")

    cfg = Config()
    cfg.set_main_option("script_location", migrations_dir)
    cfg.attributes["connection"] = engine

    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


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
            event.listen(self._engine, "connect", _configure_catalog_pragmas)
        return self._engine

    def create_all(self) -> None:
        """Apply Alembic migrations to bring catalog schema up to date."""
        _run_migrations(self.engine)

    def session(self) -> Session:
        """Create a new database session."""
        return Session(self.engine)

    @property
    def repos_dir(self) -> Path:
        """Directory where per-repo storage lives."""
        p = self.home / "repos"
        p.mkdir(parents=True, exist_ok=True)
        return p
