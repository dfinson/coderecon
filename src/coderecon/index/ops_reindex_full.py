"""Full reindex and reconignore-change operations for the index coordinator.

Standalone functions extracted from IndexCoordinatorEngine.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlmodel import select

from coderecon._core.languages import detect_language_family
from coderecon.index.resolution.resolver import (
    resolve_references,
    run_pass_1_5,
)
from coderecon.index.resolution.type_resolver import resolve_type_traced
from coderecon.index.structural.materialize import materialize_all
from coderecon.index.models import Context, File, ProbeStatus
from coderecon.index.ops_types import IndexStats

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


async def _reindex_for_reconignore_change(
    engine: IndexCoordinatorEngine,
) -> IndexStats:
    """Handle .reconignore change by computing file diff and updating index.
    Removes files that are now ignored and adds files that are now included.
    Must be called while holding _reconcile_lock.
    """
    start_time = time.time()
    files_added = 0
    files_removed = 0
    # Get currently indexed files from database
    with engine.db.session() as session:
        file_stmt = select(File.path)
        indexed_paths = set(session.exec(file_stmt).all())
    # Get files that should be indexed under current .reconignore rules
    should_index: set[str] = set()
    file_to_context: dict[str, int] = {}
    with engine.db.session() as session:
        ctx_stmt = select(Context).where(
            Context.probe_status == ProbeStatus.VALID.value,
        )
        contexts = list(session.exec(ctx_stmt).all())
    # Walk filesystem once, apply reconignore
    all_files = engine._walk_all_files()
    for context in contexts:
        context_root = engine.repo_root / context.root_path
        if not context_root.exists():
            continue
        include_globs = context.get_include_globs()
        exclude_globs = context.get_exclude_globs()
        context_id = context.id or 1
        for file_path in engine._filter_files_for_context(
            all_files, context_root, include_globs, exclude_globs
        ):
            rel_path = str(file_path.relative_to(engine.repo_root))
            if rel_path not in should_index:
                should_index.add(rel_path)
                file_to_context[rel_path] = context_id
    # Compute diff
    to_remove = indexed_paths - should_index
    to_add = should_index - indexed_paths
    # Remove files that are now ignored
    with engine._tantivy_write_lock:
        for rel_path in to_remove:
            if engine._lexical is not None:
                engine._lexical.remove_file(rel_path)
            files_removed += 1
        # Add files that are now included
        for rel_path in to_add:
            full_path = engine.repo_root / rel_path
            if full_path.exists():
                try:
                    content = engine._safe_read_text(full_path)
                    symbols = engine._extract_symbols(full_path)
                    ctx_id = file_to_context.get(rel_path, 1)
                    if engine._lexical is not None:
                        engine._lexical.add_file(
                            rel_path, content, context_id=ctx_id, symbols=symbols,
                            worktree=engine._freshness_worktree or "main",
                        )
                    files_added += 1
                except (OSError, UnicodeDecodeError):
                    log.debug("file_index_failed", path=rel_path, exc_info=True)
                    continue
    # Reload index
    if engine._lexical is not None:
        engine._lexical.reload()
    # Pre-create File records for added files before structural indexing
    file_id_map: dict[str, int] = {}
    if to_add:
        with engine.db.session() as session:
            for rel_path in to_add:
                full_path = engine.repo_root / rel_path
                if not full_path.exists():
                    continue
                try:
                    content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
                except OSError:
                    log.error("file_read_failed_during_reconignore_upsert", path=rel_path, exc_info=True)
                    raise
                lang = detect_language_family(full_path)
                file_record = File(
                    path=rel_path,
                    content_hash=content_hash,
                    language_family=lang,
                    worktree_id=engine._get_or_create_worktree_id(
                        engine._freshness_worktree or "main"
                    ),
                )
                session.add(file_record)
                session.flush()
                if file_record.id is not None:
                    file_id_map[rel_path] = file_record.id
            session.commit()
    # Update structural index for added files, grouped by context
    if to_add and engine._structural is not None:
        _structural_index_added_files(engine, to_add, file_to_context, file_id_map)
        resolve_type_traced(engine.db)
    # Sweep orphaned edge rows after resolution
    engine._sweep_orphaned_edges()
    # Materialize ExportSurface/ExportThunk/AnchorGroup tables
    materialize_all(engine.db)
    # Remove structural facts for removed files
    if to_remove:
        _ri_wt_id = engine._get_or_create_worktree_id(
            engine._freshness_worktree or "main"
        )
        engine._remove_structural_facts_for_paths(
            list(to_remove), worktree_id=_ri_wt_id,
        )
    # Remove File records for removed paths
    if to_remove:
        with engine.db.bulk_writer() as writer:
            for rel_path in to_remove:
                writer.delete_where(
                    File,
                    "path = :p AND worktree_id = :wt",
                    {"p": rel_path, "wt": _ri_wt_id},
                )
    # Update test targets for files entering/leaving the index
    await engine._update_test_targets_incremental(
        new_paths=[Path(p) for p in to_add],
        existing_paths=[],
        removed_paths=[Path(p) for p in to_remove],
    )
    duration = time.time() - start_time
    return IndexStats(
        files_processed=len(to_add) + len(to_remove),
        files_added=files_added,
        files_updated=0,
        files_removed=files_removed,
        symbols_indexed=0,
        duration_seconds=duration,
    )


def _structural_index_added_files(
    engine: IndexCoordinatorEngine,
    to_add: set[str],
    file_to_context: dict[str, int],
    file_id_map: dict[str, int],
) -> None:
    """Extract and index structural facts for newly added files."""
    by_context: dict[int, list[str]] = {}
    for rel_path in to_add:
        ctx_id = file_to_context.get(rel_path, 1)
        if ctx_id not in by_context:
            by_context[ctx_id] = []
        by_context[ctx_id].append(rel_path)
    _wt = engine._freshness_worktree or "main"
    _root = engine._worktree_root_cache.get(_wt, engine.repo_root)
    for ctx_id, paths in by_context.items():
        extractions = engine._structural.extract_files(paths, ctx_id, repo_root=_root)
        engine._structural.index_files(
            paths,
            context_id=ctx_id,
            file_id_map=file_id_map,
            worktree_id=engine._get_or_create_worktree_id(_wt),
            is_main_worktree=engine._is_main_worktree(_wt),
            _extractions=extractions,
            repo_root=_root,
        )
    from coderecon.index.resolution.config_refs import (
        resolve_config_file_refs,
    )
    resolve_config_file_refs(engine.db, engine.repo_root)
    run_pass_1_5(engine.db, None)
    resolve_references(engine.db)


async def _reindex_full_impl(engine: IndexCoordinatorEngine) -> IndexStats:
    """Full repository reindex.
    Discovers all files on disk, compares against DB, and indexes new/changed files.
    Removes files that no longer exist.
    SERIALIZED: Acquires reconcile_lock and tantivy_write_lock.
    """
    if not engine._initialized:
        msg = "Coordinator not initialized"
        raise RuntimeError(msg)
    start_time = time.time()
    files_added = 0
    files_updated = 0
    files_removed = 0
    symbols_indexed = 0
    with engine._get_worktree_lock("main"):
        # Get currently indexed files from database (path → content_hash)
        indexed_hashes: dict[str, str | None] = {}
        with engine.db.session() as session:
            file_stmt = select(File.path, File.content_hash)
            for path, content_hash in session.exec(file_stmt).all():
                indexed_hashes[path] = content_hash
        indexed_paths = set(indexed_hashes.keys())
        # Get files that should be indexed (walk filesystem)
        should_index: set[str] = set()
        file_to_context: dict[str, int] = {}
        with engine.db.session() as session:
            ctx_stmt = select(Context).where(
                Context.probe_status == ProbeStatus.VALID.value,
            )
            contexts = list(session.exec(ctx_stmt).all())
        all_files = engine._walk_all_files()
        sorted_contexts = sorted(
            contexts,
            key=lambda c: c.root_path.count("/") if c.root_path else 0,
            reverse=True,
        )
        for context in sorted_contexts:
            context_root = engine.repo_root / context.root_path
            if not context_root.exists():
                continue
            include_globs = context.get_include_globs()
            exclude_globs = context.get_exclude_globs()
            context_id = context.id or 1
            for file_path in engine._filter_files_for_context(
                all_files, context_root, include_globs, exclude_globs
            ):
                rel_path = str(file_path.relative_to(engine.repo_root))
                if rel_path not in file_to_context:
                    should_index.add(rel_path)
                    file_to_context[rel_path] = context_id
        # Compute diff
        to_remove = indexed_paths - should_index
        to_add = should_index - indexed_paths
        # Check existing files for content changes
        to_update = _detect_changed_files(engine, indexed_paths & should_index, indexed_hashes)
        # Combine new + modified for indexing
        to_index = to_add | to_update
        # Process removals + updates
        with engine._tantivy_write_lock:
            for rel_path in to_remove:
                if engine._lexical is not None:
                    engine._lexical.remove_file(rel_path)
                files_removed += 1
            for rel_path in to_update:
                if engine._lexical is not None:
                    engine._lexical.remove_file(rel_path)
            for rel_path in to_index:
                full_path = engine.repo_root / rel_path
                if full_path.exists():
                    try:
                        content = engine._safe_read_text(full_path)
                        symbols = engine._extract_symbols(full_path)
                        ctx_id = file_to_context.get(rel_path, 1)
                        if engine._lexical is not None:
                            engine._lexical.add_file(
                                rel_path, content, context_id=ctx_id, symbols=symbols,
                                worktree=engine._freshness_worktree or "main",
                            )
                        if rel_path in to_update:
                            files_updated += 1
                        else:
                            files_added += 1
                        symbols_indexed += len(symbols)
                    except (OSError, UnicodeDecodeError):
                        log.debug("incremental_index_failed", path=rel_path, exc_info=True)
                        continue
        # Reload lexical index
        if engine._lexical is not None:
            engine._lexical.reload()
        # Pre-create/update File records before structural indexing
        _full_wt_id = engine._get_or_create_worktree_id(
            engine._freshness_worktree or "main"
        )
        file_id_map = _upsert_file_records(
            engine, to_add, to_update, _full_wt_id,
        )
        # Structural indexing for added + modified files
        if to_index and engine._structural is not None:
            _structural_index_added_files(engine, to_index, file_to_context, file_id_map)
            resolve_type_traced(engine.db)
        # Sweep orphaned edge rows after resolution
        engine._sweep_orphaned_edges()
        # Materialize ExportSurface/ExportThunk/AnchorGroup tables
        materialize_all(engine.db)
        # Remove structural facts for removed files
        if to_remove:
            engine._remove_structural_facts_for_paths(
                list(to_remove), worktree_id=_full_wt_id,
            )
        # Remove File records for removed paths
        if to_remove:
            with engine.db.bulk_writer() as writer:
                for rel_path in to_remove:
                    writer.delete_where(
                        File,
                        "path = :p AND worktree_id = :wt",
                        {"p": rel_path, "wt": _full_wt_id},
                    )
        # Update test targets for files entering/leaving the index
        await engine._update_test_targets_incremental(
            new_paths=[Path(p) for p in to_add],
            existing_paths=[Path(p) for p in to_update],
            removed_paths=[Path(p) for p in to_remove],
        )
        # Publish epoch
        if engine._epoch_manager is not None:
            engine._epoch_manager.publish_epoch(
                files_indexed=files_added,
                indexed_paths=list(to_add),
            )
    duration = time.time() - start_time

    # Post-reindex: run full test suite with coverage (best-effort, never raises)
    # Only run if files were actually added/modified — skip when nothing changed
    # (e.g. `recon up` startup with an already-current index).
    if to_index or to_remove:
        from coderecon.index.ops_coverage import run_coverage_full

        await run_coverage_full(engine)

    return IndexStats(
        files_processed=len(to_index) + len(to_remove),
        files_added=files_added,
        files_updated=files_updated,
        files_removed=files_removed,
        symbols_indexed=symbols_indexed,
        duration_seconds=duration,
    )


def _detect_changed_files(
    engine: IndexCoordinatorEngine,
    common: set[str],
    indexed_hashes: dict[str, str | None],
) -> set[str]:
    """Return paths from *common* whose on-disk hash differs from the DB hash."""
    to_update: set[str] = set()
    for rel_path in common:
        full_path = engine.repo_root / rel_path
        if full_path.exists():
            try:
                disk_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
            except OSError:
                log.error("hash_read_failed", path=rel_path, exc_info=True)
                raise
            if disk_hash != indexed_hashes.get(rel_path):
                to_update.add(rel_path)
    return to_update


def _upsert_file_records(
    engine: IndexCoordinatorEngine,
    to_add: set[str],
    to_update: set[str],
    worktree_id: int,
) -> dict[str, int]:
    """Create File rows for new paths and update hashes for changed paths."""
    file_id_map: dict[str, int] = {}
    to_index = to_add | to_update
    if not to_index:
        return file_id_map
    with engine.db.session() as session:
        for rel_path in to_update:
            full_path = engine.repo_root / rel_path
            if not full_path.exists():
                continue
            try:
                content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
            except OSError:
                log.error("file_read_failed_during_upsert", path=rel_path, exc_info=True)
                raise
            existing = session.exec(
                select(File).where(
                    File.path == rel_path,
                    File.worktree_id == worktree_id,
                )
            ).first()
            if existing and existing.id is not None:
                existing.content_hash = content_hash
                existing.indexed_at = time.time()
                session.flush()
                file_id_map[rel_path] = existing.id
        for rel_path in to_add:
            full_path = engine.repo_root / rel_path
            if not full_path.exists():
                continue
            try:
                content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
            except OSError:
                log.error("file_read_failed_during_upsert", path=rel_path, exc_info=True)
                raise
            lang = detect_language_family(full_path)
            file_record = File(
                path=rel_path,
                content_hash=content_hash,
                language_family=lang,
                indexed_at=time.time(),
                worktree_id=worktree_id,
            )
            session.add(file_record)
            session.flush()
            if file_record.id is not None:
                file_id_map[rel_path] = file_record.id
        session.commit()
    return file_id_map
