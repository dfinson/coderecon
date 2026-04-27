"""Getter and query operations for the index coordinator.

Standalone functions extracted from IndexCoordinatorEngine. Each takes
``engine`` as its first parameter.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlmodel import col, func, select

from coderecon.index._internal.db import (
    EpochStats,
    IntegrityChecker,
    IntegrityReport,
    IndexRecovery,
)
from coderecon.index._internal.indexing import FactQueries
from coderecon.index.models import (
    Certainty,
    Context,
    DefFact,
    File,
    ImportFact,
    IndexedCoverageCapability,
    IndexedLintTool,
    ProbeStatus,
    RefFact,
    TestTarget,
)
from coderecon.tools.map_repo import IncludeOption, MapRepoResult, RepoMapper

if TYPE_CHECKING:
    from coderecon.index._internal.indexing.import_graph import (
        CoverageGap,
        CoverageSourceResult,
        ImportGraphResult,
    )
    from coderecon.index.models import FileState
    from coderecon.index.ops import IndexCoordinatorEngine
    from coderecon.testing.runtime import ContextRuntime

log = structlog.get_logger(__name__)


async def get_def(
    engine: IndexCoordinatorEngine,
    name: str,
    path: str | None = None,  # noqa: ARG001 - reserved for future use
    context_id: int | None = None,
) -> DefFact | None:
    """Get first definition by name. Thread-safe."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(DefFact).where(DefFact.name == name)
        if context_id is not None:
            stmt = stmt.where(DefFact.unit_id == context_id)
        return session.exec(stmt).first()


async def get_all_defs(
    engine: IndexCoordinatorEngine,
    name: str,
    *,
    path: str | None = None,
    context_id: int | None = None,
    limit: int = 100,
) -> list[DefFact]:
    """Get all definitions by name. Thread-safe."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(DefFact).where(DefFact.name == name)
        if path is not None:
            subq = select(File.id).where(File.path == path).scalar_subquery()
            stmt = stmt.where(DefFact.file_id == subq)
        if context_id is not None:
            stmt = stmt.where(DefFact.unit_id == context_id)
        stmt = stmt.limit(limit)
        return list(session.exec(stmt).all())


async def get_references(
    engine: IndexCoordinatorEngine,
    def_fact: DefFact,
    _context_id: int,
    *,
    limit: int = 10_000,
    offset: int = 0,
) -> list[RefFact]:
    """Get references to a definition. Thread-safe."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        facts = FactQueries(session)
        return facts.list_refs_by_def_uid(def_fact.def_uid, limit=limit, offset=offset)


async def get_all_references(
    engine: IndexCoordinatorEngine,
    def_fact: DefFact,
    _context_id: int,
) -> list[RefFact]:
    """Get ALL references to a definition exhaustively. Thread-safe."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        facts = FactQueries(session)
        return facts.list_all_refs_by_def_uid(def_fact.def_uid)


async def get_callees(
    engine: IndexCoordinatorEngine,
    def_fact: DefFact,
    *,
    limit: int = 50,
) -> list[DefFact]:
    """Get definitions referenced (called/used) by a definition. Thread-safe."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        facts = FactQueries(session)
        return facts.list_callees_in_scope(
            def_fact.file_id,
            def_fact.start_line,
            def_fact.end_line,
            limit=limit,
        )


async def get_file_imports(
    engine: IndexCoordinatorEngine,
    rel_path: str,
    *,
    limit: int = 100,
) -> list[ImportFact]:
    """Get import facts for a file by its repo-relative path. Thread-safe."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        facts = FactQueries(session)
        file_rec = facts.get_file_by_path(rel_path)
        if file_rec is None or file_rec.id is None:
            return []
        return facts.list_imports(file_rec.id, limit=limit)


async def get_file_state(
    engine: IndexCoordinatorEngine, file_id: int, context_id: int
) -> FileState:
    """Get computed file state for mutation gating."""
    await engine.wait_for_freshness()
    if engine._state is None:
        from coderecon.index.models import FileState, Freshness
        return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNCERTAIN)
    return engine._state.get_file_state(file_id, context_id)


async def get_file_stats(engine: IndexCoordinatorEngine) -> dict[str, int]:
    """Get file counts by language family from the index."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = (
            select(File.language_family, func.count())
            .where(File.language_family != None)  # noqa: E711
            .group_by(File.language_family)
        )
        results = session.exec(stmt).all()
        return {lang: count for lang, count in results if lang}


async def get_indexed_file_count(
    engine: IndexCoordinatorEngine, language_family: str | None = None
) -> int:
    """Get count of indexed files, optionally filtered by language."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(func.count()).select_from(File)
        if language_family:
            stmt = stmt.where(File.language_family == language_family)
        result = session.exec(stmt).one()
        return result or 0


async def get_indexed_files(
    engine: IndexCoordinatorEngine,
    language_family: str | None = None,
    path_prefix: str | None = None,
) -> list[str]:
    """Get paths of indexed files."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(File.path)
        if language_family:
            stmt = stmt.where(File.language_family == language_family)
        if path_prefix:
            stmt = stmt.where(File.path.startswith(path_prefix))
        return list(session.exec(stmt).all())


async def get_contexts(engine: IndexCoordinatorEngine) -> list[Context]:
    """Get all valid contexts from the index."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(Context).where(
            Context.probe_status == ProbeStatus.VALID.value,
        )
        return list(session.exec(stmt).all())


async def get_test_targets(
    engine: IndexCoordinatorEngine,
    target_ids: list[str] | None = None,
) -> list[TestTarget]:
    """Get test targets from the index."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(TestTarget)
        if target_ids:
            stmt = stmt.where(col(TestTarget.target_id).in_(target_ids))
        return list(session.exec(stmt).all())


async def get_affected_test_targets(
    engine: IndexCoordinatorEngine,
    changed_files: list[str],
) -> ImportGraphResult:
    """Given changed source files, find test targets affected by those changes."""
    from coderecon.index._internal.indexing.import_graph import (
        ImportGraph,
    )
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        graph = ImportGraph(session)
        return graph.affected_tests(changed_files)


async def get_coverage_sources(
    engine: IndexCoordinatorEngine,
    test_files: list[str],
) -> CoverageSourceResult:
    """Given test files, find source directories for --cov scoping."""
    from coderecon.index._internal.indexing.import_graph import (
        ImportGraph,
    )
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        graph = ImportGraph(session)
        return graph.imported_sources(test_files)


async def get_coverage_gaps(engine: IndexCoordinatorEngine) -> list[CoverageGap]:
    """Find source modules with no test imports."""
    from coderecon.index._internal.indexing.import_graph import (
        ImportGraph,
    )
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        graph = ImportGraph(session)
        return graph.uncovered_modules()


async def get_lint_tools(
    engine: IndexCoordinatorEngine,
    tool_ids: list[str] | None = None,
    category: str | None = None,
) -> list[IndexedLintTool]:
    """Get lint tools from the index."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(IndexedLintTool)
        if tool_ids:
            stmt = stmt.where(col(IndexedLintTool.tool_id).in_(tool_ids))
        if category:
            stmt = stmt.where(IndexedLintTool.category == category)
        return list(session.exec(stmt).all())


async def get_context_runtime(
    engine: IndexCoordinatorEngine,
    workspace_root: str,
) -> ContextRuntime | None:
    """Get pre-indexed runtime context for a workspace root."""
    await engine.wait_for_freshness()
    from coderecon.testing.runtime import ContextRuntime
    with engine.db.session() as session:
        try:
            rel_path = str(Path(workspace_root).relative_to(engine.repo_root))
            if rel_path == ".":
                rel_path = ""
        except ValueError:
            rel_path = ""  # workspace_root is repo_root itself
        # Find context for this workspace
        stmt = select(Context).where(
            Context.root_path == rel_path,
            Context.probe_status == ProbeStatus.VALID.value,
        )
        context = session.exec(stmt).first()
        if not context or context.id is None:
            return None
        # Get associated runtime
        runtime_stmt = select(ContextRuntime).where(ContextRuntime.context_id == context.id)
        return session.exec(runtime_stmt).first()


async def get_coverage_capability(
    engine: IndexCoordinatorEngine,
    workspace_root: str,
    runner_pack_id: str,
) -> dict[str, bool]:
    """Get pre-indexed coverage tools for a (workspace, runner_pack) pair."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        stmt = select(IndexedCoverageCapability).where(
            IndexedCoverageCapability.workspace_root == workspace_root,
            IndexedCoverageCapability.runner_pack_id == runner_pack_id,
        )
        capability = session.exec(stmt).first()
        if capability:
            return capability.get_tools()
        return {}


async def map_repo(
    engine: IndexCoordinatorEngine,
    include: list[IncludeOption] | None = None,
    depth: int = 3,
    limit: int = 100,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    respect_gitignore: bool = True,
) -> MapRepoResult:
    """Build repository mental model from indexed data."""
    await engine.wait_for_freshness()
    with engine.db.session() as session:
        mapper = RepoMapper(session, engine.repo_root)
        return mapper.map(
            include=include,
            depth=depth,
            limit=limit,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            respect_gitignore=respect_gitignore,
        )


async def verify_integrity(engine: IndexCoordinatorEngine) -> IntegrityReport:
    """Verify index integrity (FK violations, missing files, Tantivy sync)."""
    checker = IntegrityChecker(engine.db, engine.repo_root, engine._lexical)
    return checker.verify()


async def recover(engine: IndexCoordinatorEngine) -> None:
    """Wipe and prepare for full reindex."""
    recovery = IndexRecovery(engine.db, engine.tantivy_path)
    recovery.wipe_all()
    engine._initialized = False
    engine._lexical = None


def get_current_epoch(engine: IndexCoordinatorEngine) -> int:
    """Return current epoch ID, or 0 if none published."""
    if engine._epoch_manager is None:
        return 0
    return engine._epoch_manager.get_current_epoch()


def publish_epoch(
    engine: IndexCoordinatorEngine,
    files_indexed: int = 0,
    commit_hash: str | None = None,
) -> EpochStats:
    """Atomically publish a new epoch. See SPEC.md §7.6."""
    if engine._epoch_manager is None:
        raise RuntimeError("Coordinator not initialized")
    return engine._epoch_manager.publish_epoch(files_indexed, commit_hash)


def await_epoch(
    engine: IndexCoordinatorEngine, target_epoch: int, timeout_seconds: float = 5.0
) -> bool:
    """Block until epoch >= target, or timeout. Returns True if reached."""
    if engine._epoch_manager is None:
        return False
    return engine._epoch_manager.await_epoch(target_epoch, timeout_seconds)


def close(engine: IndexCoordinatorEngine) -> None:
    """Close all resources."""
    engine._lexical = None
    engine._def_cache = None
    engine._initialized = False
    # Dispose DB engine to release file handles
    if hasattr(engine, "db") and engine.db is not None:
        engine.db.engine.dispose()
