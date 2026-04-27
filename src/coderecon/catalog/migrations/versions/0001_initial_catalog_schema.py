"""initial catalog schema

Revision ID: 0001
Revises:
Create Date: 2026-04-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

def upgrade() -> None:
    # Use batch mode for SQLite compatibility.
    # If tables already exist (pre-alembic databases), this is a no-op.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if "catalog_repositories" not in existing:
        op.create_table(
            "catalog_repositories",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String, nullable=False, index=True),
            sa.Column("git_dir", sa.String, nullable=False, unique=True, index=True),
            sa.Column("storage_dir", sa.String, nullable=False, unique=True),
            sa.Column("default_branch", sa.String, nullable=True),
            sa.Column("registered_at", sa.Float, nullable=False),
            sa.Column("last_indexed_at", sa.Float, nullable=True),
        )

    if "catalog_worktrees" not in existing:
        op.create_table(
            "catalog_worktrees",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "repo_id",
                sa.Integer,
                sa.ForeignKey("catalog_repositories.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("name", sa.String, nullable=False, index=True),
            sa.Column("root_path", sa.String, nullable=False, unique=True, index=True),
            sa.Column("branch", sa.String, nullable=True),
            sa.Column("is_main", sa.Boolean, nullable=False, default=False),
            sa.Column("registered_at", sa.Float, nullable=False),
            sa.Column("last_indexed_at", sa.Float, nullable=True),
        )

def downgrade() -> None:
    op.drop_table("catalog_worktrees")
    op.drop_table("catalog_repositories")
