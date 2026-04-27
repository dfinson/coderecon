"""Incremental reindex implementation for the index coordinator.

Standalone function extracted from IndexCoordinatorEngine.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import bindparam, text
from sqlmodel import col, select

from coderecon.core.languages import detect_language_family
from coderecon.index._internal.indexing import (
    materialize_all,
    resolve_references,
    resolve_type_traced,
    run_pass_1_5,
)
from coderecon.index.models import Context, File, ProbeStatus
from coderecon.index.ops_glob import _matches_glob
from coderecon.index.ops_types import IndexStats

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)
logger = structlog.get_logger(__name__)


async def _reindex_incremental_impl(
    engine: IndexCoordinatorEngine,
    changed_paths: list[Path],
    worktree: str = "main",
) -> IndexStats:
    """Incremental reindex for changed files (unified single-pass).
    SERIALIZED: Acquires reconcile_lock and tantivy_write_lock.
    If .reconignore changes, triggers a full reindex to apply new patterns.
    """
    if not engine._initialized:
        msg = "Coordinator not initialized"
        raise RuntimeError(msg)
    # Deduplicate paths to avoid UNIQUE constraint violations
    changed_paths = list(dict.fromkeys(changed_paths))
    # Resolve effective root
    _effective_root = engine._worktree_root_cache.get(worktree, engine.repo_root)
    # Normalize: convert any absolute worktree paths to repo-relative.
    _normalized: list[Path] = []
    for p in changed_paths:
        if p.is_absolute():
            try:
                p = p.relative_to(_effective_root)
            except ValueError:
                try:
                    p = p.relative_to(engine.repo_root)
                except ValueError:
                    log.debug("path_resolve_failed", path=str(p))
                    continue
        _normalized.append(p)
    changed_paths = _normalized
    start_time = time.time()
    files_added = 0
    files_updated = 0
    files_removed = 0
    symbols_indexed = 0
    with engine._get_worktree_lock(worktree):
        # Reconcile changes
        _wt_id = engine._get_or_create_worktree_id(worktree)
        if engine._reconciler is not None:
            reconcile_result = engine._reconciler.reconcile(
                changed_paths, worktree_id=_wt_id,
                worktree_root=_effective_root if worktree != "main" else None,
            )
            # If .reconignore changed, do full reindex to apply new patterns
            if reconcile_result.reconignore_changed:
                from coderecon.index.ops_reindex_full import _reindex_for_reconignore_change
                return await _reindex_for_reconignore_change(engine)
        # Separate existing vs new files
        existing_paths: list[Path] = []
        new_paths: list[Path] = []
        removed_paths: list[Path] = []
        with engine.db.session() as session:
            indexed_set = set(
                session.exec(
                    select(File.path).where(File.worktree_id == _wt_id)
                ).all()
            )
        for path in changed_paths:
            full_path = _effective_root / path
            str_path = str(path)
            if full_path.exists():
                if str_path in indexed_set:
                    existing_paths.append(path)
                else:
                    new_paths.append(path)
            else:
                if str_path in indexed_set:
                    removed_paths.append(path)
        # Create File records for new files BEFORE structural indexing
        file_id_map: dict[str, int] = {}
        if new_paths:
            with engine.db.session() as session:
                for path in new_paths:
                    full_path = _effective_root / path
                    if not full_path.exists():
                        continue
                    try:
                        content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
                        lang = detect_language_family(full_path)
                        file_record = File(
                            path=str(path),
                            content_hash=content_hash,
                            language_family=lang,
                            worktree_id=engine._get_or_create_worktree_id(worktree),
                        )
                        session.add(file_record)
                        session.flush()
                        if file_record.id is not None:
                            file_id_map[str(path)] = file_record.id
                        files_added += 1
                    except (OSError, UnicodeDecodeError):
                        log.debug("file_read_failed", path=str(path), exc_info=True)
                        continue
                session.commit()
        # === Unified single-pass: extract, stage Tantivy, persist structural facts ===
        all_changed = existing_paths + new_paths
        str_changed = [str(p) for p in all_changed if (_effective_root / p).exists()]
        existing_set = {str(p) for p in existing_paths}
        if str_changed and engine._structural is not None and engine._lexical is not None:
            with engine.db.session() as session:
                contexts = session.exec(
                    select(Context).where(Context.probe_status == ProbeStatus.VALID.value)
                ).all()
                specific_contexts = [c for c in contexts if c.tier != 3 and c.id is not None]
                specific_contexts.sort(
                    key=lambda c: len(c.root_path) if c.root_path else 0,
                    reverse=True,
                )
                file_to_context: dict[str, int] = {}
                for ctx in specific_contexts:
                    if ctx.id is None:
                        continue
                    ctx_id: int = ctx.id
                    ctx_root = ctx.root_path or ""
                    exclude_globs = ctx.get_exclude_globs()
                    include_globs = ctx.get_include_globs()
                    for str_path in str_changed:
                        if str_path in file_to_context:
                            continue
                        if (
                            ctx_root
                            and str_path != ctx_root
                            and not str_path.startswith(ctx_root + "/")
                        ):
                            continue
                        rel_to_ctx = str_path[len(ctx_root) + 1 :] if ctx_root else str_path
                        if any(_matches_glob(rel_to_ctx, p) for p in exclude_globs):
                            continue
                        if include_globs and not any(
                            _matches_glob(rel_to_ctx, p) for p in include_globs
                        ):
                            continue
                        file_to_context[str_path] = ctx_id
                # Assign unclaimed files to root fallback context
                root_ctx = next((c for c in contexts if c.tier == 3 and c.id is not None), None)
                if root_ctx is not None and root_ctx.id is not None:
                    root_id: int = root_ctx.id
                    root_exclude = root_ctx.get_exclude_globs()
                    root_include = root_ctx.get_include_globs()
                    for str_path in str_changed:
                        if str_path not in file_to_context:
                            if any(_matches_glob(str_path, p) for p in root_exclude):
                                continue
                            if root_include and not any(
                                _matches_glob(str_path, p) for p in root_include
                            ):
                                continue
                            file_to_context[str_path] = root_id
                # Populate file_id_map for existing files
                files = session.exec(
                    select(File).where(
                        col(File.path).in_(str_changed),
                        File.worktree_id == _wt_id,
                    )
                ).all()
                for f in files:
                    if f.id is not None:
                        file_id_map[f.path] = f.id
            # Group files by context_id
            context_files: dict[int, list[str]] = {}
            for str_path, ctx_id in file_to_context.items():
                context_files.setdefault(ctx_id, []).append(str_path)
            _PARALLEL_THRESHOLD = 8
            workers = (
                min(os.cpu_count() or 4, 16) if len(str_changed) >= _PARALLEL_THRESHOLD else 1
            )
            with engine._tantivy_write_lock:
                _extract_root = _effective_root
                for ctx_id, paths in context_files.items():
                    extractions = engine._structural.extract_files(
                        paths, ctx_id, workers=workers, repo_root=_extract_root
                    )
                    failed_paths: list[str] = []
                    for extraction in extractions:
                        if extraction.content_text is None:
                            if extraction.file_path in existing_set:
                                engine._lexical.stage_remove(extraction.file_path, worktree)
                                files_removed += 1
                            failed_paths.append(extraction.file_path)
                            continue
                        fid = file_id_map.get(extraction.file_path, 0)
                        engine._lexical.stage_file(
                            extraction.file_path,
                            extraction.content_text,
                            context_id=ctx_id,
                            file_id=fid,
                            symbols=extraction.symbol_names,
                            worktree=worktree,
                        )
                        if extraction.file_path in existing_set:
                            files_updated += 1
                        symbols_indexed += len(extraction.symbol_names)
                        extraction.content_text = None
                        extraction.symbol_names = []
                    # Persist structural facts
                    ok_extractions = [e for e in extractions if e.file_path not in failed_paths]
                    ok_paths = [e.file_path for e in ok_extractions]
                    engine._structural.index_files(
                        ok_paths,
                        ctx_id,
                        file_id_map=file_id_map,
                        worktree_id=engine._get_or_create_worktree_id(worktree),
                        is_main_worktree=engine._is_main_worktree(worktree),
                        _extractions=ok_extractions,
                    )
                    # Purge stale structural facts for failed extractions
                    if failed_paths:
                        engine._remove_structural_facts_for_paths(
                            failed_paths, worktree_id=_wt_id,
                        )
                        with engine.db.session() as session:
                            session.exec(
                                text(
                                    "UPDATE files SET content_hash = NULL "
                                    "WHERE path IN :paths AND worktree_id = :wt"
                                ).bindparams(
                                    bindparam("paths", expanding=True),
                                    wt=_wt_id,
                                ),
                                params={"paths": failed_paths},
                            )  # type: ignore[call-overload]
                            session.commit()
                # Stage removals
                for path in removed_paths:
                    engine._lexical.stage_remove(str(path), worktree)
                    files_removed += 1
                # Commit all staged changes atomically
                try:
                    engine._lexical.commit_staged()
                except (OSError, RuntimeError):
                    logger.exception("tantivy_commit_failed")
                    with engine.db.session() as session:
                        session.exec(
                            text(
                                "UPDATE files SET content_hash = NULL "
                                "WHERE path IN :paths AND worktree_id = :wt"
                            ).bindparams(
                                bindparam("paths", expanding=True),
                                wt=_wt_id,
                            ),
                            params={"paths": list(str_changed)},
                        )  # type: ignore[call-overload]
                        session.commit()
                    raise
            # Reload searcher to see committed changes
            engine._lexical.reload()
            # Collect file IDs for scoped resolution passes
            with engine.db.session() as session:
                _id_rows = session.exec(
                    select(File.id).where(
                        col(File.path).in_(str_changed),
                        File.worktree_id == _wt_id,
                    )
                ).all()
                changed_file_ids: list[int] = [fid for fid in _id_rows if fid is not None]
            # Pass 1.5 / 2 / 3: cross-file resolution (scoped to changed files)
            if changed_file_ids:
                _extra_file_ids = engine._invalidate_dangling_refs(
                    changed_file_ids, worktree_id=_wt_id,
                )
                _resolve_fids = list(
                    dict.fromkeys(changed_file_ids + _extra_file_ids)
                )
                run_pass_1_5(engine.db, None, file_ids=_resolve_fids)
                resolve_references(engine.db, file_ids=_resolve_fids, worktree_id=_wt_id)
                resolve_type_traced(engine.db, file_ids=_resolve_fids)
            # Sweep orphaned edge rows after resolution
            engine._sweep_orphaned_edges()
            # Materialize ExportSurface/ExportThunk/AnchorGroup tables
            materialize_all(engine.db)
            # Mark coverage facts as stale for defs in changed files
            if changed_file_ids:
                engine._mark_coverage_stale(changed_file_ids)
            # SPLADE: re-encode vectors for changed files
            engine._reindex_splade_vectors(changed_file_ids)
            # Passes 5-7: semantic passes scoped to changed files.
            engine._reindex_semantic_passes(changed_file_ids)
            # Mark successfully indexed files as indexed
            if changed_file_ids:
                now = time.time()
                with engine.db.session() as session:
                    for fid in changed_file_ids:
                        session.exec(
                            text(
                                "UPDATE files SET indexed_at = :ts WHERE id = :fid"
                            ).bindparams(ts=now, fid=fid)
                        )  # type: ignore[call-overload]
                    session.commit()
        else:
            # Only removals (or nothing changed)
            with engine._tantivy_write_lock:
                for path in removed_paths:
                    if engine._lexical is not None:
                        engine._lexical.stage_remove(str(path), worktree)
                    files_removed += 1
                if engine._lexical is not None:
                    engine._lexical.commit_staged()
            if engine._lexical is not None:
                engine._lexical.reload()
        # Propagate def changes to sibling worktrees
        engine._propagate_def_changes(_wt_id)
        # Remove structural facts for removed files
        if removed_paths:
            engine._remove_structural_facts_for_paths(
                [str(p) for p in removed_paths], worktree_id=_wt_id,
            )
        # Remove File records for removed paths
        if removed_paths:
            with engine.db.bulk_writer() as writer:
                for path in removed_paths:
                    writer.delete_where(
                        File,
                        "path = :p AND worktree_id = :wt",
                        {"p": str(path), "wt": _wt_id},
                    )
        # Incrementally update test targets for changed test files
        await engine._update_test_targets_incremental(new_paths, existing_paths, removed_paths)
        # Incrementally update lint tools if config files changed
        await engine._update_lint_tools_incremental(changed_paths)
    # WAL checkpoint to keep WAL file bounded after bulk writes
    try:
        engine.db.checkpoint("PASSIVE")
    except (OSError, RuntimeError):
        logger.debug("wal_checkpoint_skipped_after_reindex", exc_info=True)
    duration = time.time() - start_time
    return IndexStats(
        files_processed=len(changed_paths),
        files_added=files_added,
        files_updated=files_updated,
        files_removed=files_removed,
        symbols_indexed=symbols_indexed,
        duration_seconds=duration,
    )
