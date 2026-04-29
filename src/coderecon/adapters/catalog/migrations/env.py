"""Alembic environment for the catalog database."""

from __future__ import annotations

from alembic import context
from sqlmodel import SQLModel

from coderecon.adapters.catalog.models import RepoEntry, WorktreeEntry  # noqa: F401

target_metadata = SQLModel.metadata

def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("No connection provided to Alembic config")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()

run_migrations_online()
