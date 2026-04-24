"""Alembic environment for the index database."""

from __future__ import annotations

from alembic import context
from sqlmodel import SQLModel

# Import all index models to register them with SQLModel.metadata.
from coderecon.index.models import (  # noqa: F401
    AnchorGroup,
    Context,
    ContextMarker,
    DefFact,
    DefSnapshotRecord,
    DocCodeEdgeFact,
    DocCrossRef,
    DynamicAccessSite,
    EndpointFact,
    Epoch,
    ExportEntry,
    ExportSurface,
    ExportThunk,
    File,
    FileChunkVec,
    ImportFact,
    IndexedCoverageCapability,
    IndexedLintTool,
    InterfaceImplFact,
    LintStatusFact,
    LocalBindFact,
    MemberAccessFact,
    ReceiverShapeFact,
    RefFact,
    RepoState,
    ScopeFact,
    SemanticNeighborFact,
    SpladeVec,
    TestCoverageFact,
    TestTarget,
    TypeAnnotationFact,
    TypeMemberFact,
    Worktree,
)
from coderecon.testing.runtime import ContextRuntime  # noqa: F401

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
