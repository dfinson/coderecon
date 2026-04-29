"""Full indexing operations for the index coordinator.

Standalone functions extracted from IndexCoordinatorEngine. Each takes
``engine`` as its first parameter.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlmodel import col, select

from coderecon._core.languages import detect_language_family
from coderecon.index.discovery.ignore import IgnoreChecker
from coderecon.index.resolution.resolver import (
    resolve_references,
    run_pass_1_5,
)
from coderecon.index.resolution.type_resolver import resolve_type_traced
from coderecon.index.structural.materialize import materialize_all
from coderecon.index.models import Context, DefFact, File, ProbeStatus
from coderecon.index.ops_glob import _compile_glob_set

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


async def _index_all_files(
    engine: IndexCoordinatorEngine,
    on_progress: Callable[[int, int, dict[str, int], str], None],
) -> tuple[int, list[str], dict[str, int]]:
    """Index all files in valid contexts (unified single-pass).
    Single-pass architecture:
    - Each file is read and tree-sitter parsed ONCE by the structural extractor
    - ExtractionResult carries content_text and symbol_names for Tantivy
    - Tantivy uses batched stage_file() + commit_staged() (1 commit, not N)
    - Structural extraction runs in parallel via ProcessPoolExecutor
    """
    if engine._lexical is None or engine._parser is None or engine._structural is None:
        return 0, [], {}
    with engine._tantivy_write_lock:
        # Get all valid contexts, separating root fallback from others
        with engine.db.session() as session:
            stmt = select(Context).where(
                Context.probe_status == ProbeStatus.VALID.value,
            )
            all_contexts = list(session.exec(stmt).all())
        # Separate root fallback (tier=3) from specific contexts
        specific_contexts = [c for c in all_contexts if c.tier != 3]
        root_context = next((c for c in all_contexts if c.tier == 3), None)
        # Walk filesystem ONCE - applies PRUNABLE_DIRS and reconignore
        all_files = _walk_all_files(engine)
        files_to_index: list[tuple[Path, str, int, str | None]] = []
        # (full_path, rel_str, ctx_id, language_family)
        claimed_paths: set[str] = set()
        # First pass: match files to specific contexts (tier 1/2/ambient)
        for context in specific_contexts:
            context_root = engine.repo_root / context.root_path
            if not context_root.exists():
                continue
            include_globs = context.get_include_globs()
            exclude_globs = context.get_exclude_globs()
            context_id = context.id or 0
            for file_path in _filter_files_for_context(
                engine, all_files, context_root, include_globs, exclude_globs
            ):
                rel_path = file_path.relative_to(engine.repo_root)
                rel_str = str(rel_path)
                if rel_str in claimed_paths:
                    continue
                claimed_paths.add(rel_str)
                files_to_index.append((file_path, rel_str, context_id, context.language_family))
        # Second pass: assign unclaimed files to root fallback context
        if root_context is not None:
            root_context_id = root_context.id or 0
            exclude_globs = root_context.get_exclude_globs()
            for file_path in _filter_unclaimed_files(engine, all_files, exclude_globs):
                rel_path = file_path.relative_to(engine.repo_root)
                rel_str = str(rel_path)
                if rel_str in claimed_paths:
                    continue
                lang_value = detect_language_family(file_path)
                claimed_paths.add(rel_str)
                files_to_index.append((file_path, rel_str, root_context_id, lang_value))
        # === Unified single-pass indexing ===
        total = len(files_to_index)
        count = 0
        indexed_paths: list[str] = []
        files_by_ext: dict[str, int] = {}
        workers = int(os.environ.get("CODERECON_INDEX_WORKERS", 0)) or min(os.cpu_count() or 4, 16)
        if engine._structural is not None:
            batch_size = 50
            _extract_start = time.time()
            for batch_start in range(0, total, batch_size):
                batch_end = min(batch_start + batch_size, total)
                batch = files_to_index[batch_start:batch_end]
                # Group batch by context_id
                batch_by_context: dict[int, list[str]] = {}
                for _full_path, rel_str, ctx_id, _lang in batch:
                    batch_by_context.setdefault(ctx_id, []).append(rel_str)
                for ctx_id, paths in batch_by_context.items():
                    # Extract facts (parallel for speed)
                    extractions = engine._structural.extract_files(paths, ctx_id, workers=workers)
                    # Stage each file into Tantivy using extraction results
                    for extraction in extractions:
                        if extraction.content_text is None:
                            count += 1
                            on_progress(count, total, files_by_ext, "indexing")
                            continue
                        engine._lexical.stage_file(
                            extraction.file_path,
                            extraction.content_text,
                            context_id=ctx_id,
                            symbols=extraction.symbol_names,
                            worktree=engine._freshness_worktree or "main",
                        )
                        # Release file content now
                        extraction.content_text = None
                        extraction.symbol_names = []
                        count += 1
                        indexed_paths.append(extraction.file_path)
                        # Track by file extension
                        ext = os.path.splitext(extraction.file_path)[1].lower()
                        if not ext:
                            ext = os.path.basename(extraction.file_path).lower()
                        files_by_ext[ext] = files_by_ext.get(ext, 0) + 1
                        # Report per-file progress
                        on_progress(count, total, files_by_ext, "indexing")
                    # Persist structural facts (re-uses pre-computed extractions)
                    engine._structural.index_files(
                        paths, ctx_id,
                        worktree_id=engine._get_or_create_worktree_id(
                            engine._freshness_worktree or "main"
                        ),
                        is_main_worktree=engine._is_main_worktree(
                            engine._freshness_worktree or "main"
                        ),
                        _extractions=extractions,
                    )
                # Commit Tantivy after each batch so the staging buffer doesn't grow unbounded.
                if engine._lexical.has_staged_changes():
                    engine._lexical.commit_staged()
            _extract_elapsed = time.time() - _extract_start
            log.info("index.stage.extract_complete",
                     extra={"files": count, "elapsed_sec": round(_extract_elapsed, 1),
                     "workers": workers})
            _run_resolution_passes(engine, on_progress, files_by_ext)
            # SPLADE sparse vector encoding
            _index_splade_vectors(engine, on_progress, files_by_ext)
            # Pass 5: Semantic neighbors
            _compute_semantic_neighbors(engine, on_progress, files_by_ext)
            # Pass 6: Doc chunk linking
            _index_doc_chunks(engine, on_progress, files_by_ext)
    return count, indexed_paths, files_by_ext


def _run_resolution_passes(
    engine: IndexCoordinatorEngine,
    on_progress: Callable[[int, int, dict[str, int], str], None],
    files_by_ext: dict[str, int],
) -> None:
    """Run cross-file resolution passes (1.5 through 3), sweep, and materialize."""
    _resolve_start = time.time()
    engine._structural.resolve_all_imports()
    from coderecon.index.resolution.config_refs import (
        resolve_config_file_refs,
    )
    resolve_config_file_refs(engine.db, engine.repo_root)
    on_progress(0, 1, files_by_ext, "resolving_cross_file")
    run_pass_1_5(engine.db, None)

    def pass2_progress(processed: int, total: int) -> None:
        on_progress(processed, total, files_by_ext, "resolving_refs")

    on_progress(0, 1, files_by_ext, "resolving_refs")
    resolve_references(engine.db, on_progress=pass2_progress)

    def pass3_progress(processed: int, total: int) -> None:
        on_progress(processed, total, files_by_ext, "resolving_types")

    on_progress(0, 1, files_by_ext, "resolving_types")
    resolve_type_traced(engine.db, on_progress=pass3_progress)
    _resolve_elapsed = time.time() - _resolve_start
    log.info("index.stage.resolve_complete",
             extra={"elapsed_sec": round(_resolve_elapsed, 1)})
    engine._sweep_orphaned_edges()
    materialize_all(engine.db)


def _index_splade_vectors(
    engine: IndexCoordinatorEngine,
    on_progress: Callable[[int, int, dict[str, int], str], None],
    files_by_ext: dict[str, int],
) -> None:
    """Compute SPLADE vectors for all defs (full index)."""
    from coderecon.index.search.splade import index_splade_vectors
    on_progress(0, 1, files_by_ext, "encoding_splade")
    def _splade_progress(encoded: int, total: int) -> None:
        on_progress(encoded, total, files_by_ext, "encoding_splade")
    stored = index_splade_vectors(engine.db, progress_cb=_splade_progress)
    log.info("index.splade.complete", extra={"stored": stored})


def _reindex_splade_vectors(
    engine: IndexCoordinatorEngine, file_ids: list[int]
) -> None:
    """Re-encode SPLADE vectors for defs in changed files (incremental)."""
    if not file_ids:
        return
    from coderecon.index.search.splade import index_splade_vectors
    stored = index_splade_vectors(engine.db, file_ids=file_ids)
    log.debug("reindex.splade.complete", extra={"stored": stored, "file_ids": len(file_ids)})


def _reindex_semantic_passes(
    engine: IndexCoordinatorEngine, changed_file_ids: list[int]
) -> None:
    """Run Passes 5-7 after incremental SPLADE re-encode."""
    if not changed_file_ids:
        return
    from coderecon.index.structural.doc_chunks import (
        index_doc_chunk_vectors,
        link_doc_chunks_to_defs,
    )
    from coderecon.index.search.semantic_neighbors import (
        compute_semantic_neighbors,
    )
    # Pass 6: Semantic neighbors — recompute for changed defs.
    try:
        edges = compute_semantic_neighbors(
            engine.db, changed_file_ids=changed_file_ids
        )
        log.debug("reindex.semantic_neighbors.complete", extra={"edges": edges})
    except (ImportError, OSError, RuntimeError, ValueError):
        log.warning("reindex.semantic_neighbors.failed", exc_info=True)
    # Pass 7: Doc chunk linking
    try:
        doc_file_ids = _get_doc_file_ids(engine, changed_file_ids)
        if doc_file_ids:
            chunks = index_doc_chunk_vectors(engine.db, file_ids=doc_file_ids)
            log.debug("reindex.doc_chunks.encode", extra={"chunks": chunks})
        # Re-link chunks in changed doc files against updated def vectors
        edges = link_doc_chunks_to_defs(engine.db, file_ids=doc_file_ids)
        log.debug("reindex.doc_chunks.link", extra={"edges": edges})
    except (ImportError, OSError, RuntimeError, ValueError):
        log.warning("reindex.doc_chunks.failed", exc_info=True)


def _get_doc_file_ids(
    engine: IndexCoordinatorEngine, file_ids: list[int]
) -> list[int]:
    """Filter file_ids to only doc/config files."""
    if not file_ids:
        return []
    with engine.db.session() as session:
        from coderecon.index.structural.doc_chunks import _DOC_FAMILIES
        rows = session.exec(
            select(File.id).where(
                col(File.id).in_(file_ids),
                col(File.language_family).in_(list(_DOC_FAMILIES)),
            )
        ).all()
        return [r for r in rows if r is not None]


def _compute_semantic_neighbors(
    engine: IndexCoordinatorEngine,
    on_progress: Callable[[int, int, dict[str, int], str], None],
    files_by_ext: dict[str, int],
) -> None:
    """Pass 5: Compute semantic neighbor edges."""
    from coderecon.index.search.semantic_neighbors import (
        compute_semantic_neighbors,
    )
    on_progress(0, 1, files_by_ext, "semantic_neighbors")
    edges = compute_semantic_neighbors(engine.db)
    on_progress(1, 1, files_by_ext, "semantic_neighbors")
    log.info("index.semantic_neighbors.complete", extra={"edges": edges})


def _index_doc_chunks(
    engine: IndexCoordinatorEngine,
    on_progress: Callable[[int, int, dict[str, int], str], None],
    files_by_ext: dict[str, int],
) -> None:
    """Pass 6: Encode doc chunks and link to code defs."""
    from coderecon.index.structural.doc_chunks import (
        index_doc_chunk_vectors,
        link_doc_chunks_to_defs,
    )
    on_progress(0, 2, files_by_ext, "doc_chunk_linking")
    chunks = index_doc_chunk_vectors(engine.db)
    on_progress(1, 2, files_by_ext, "doc_chunk_linking")
    edges = link_doc_chunks_to_defs(engine.db)
    on_progress(2, 2, files_by_ext, "doc_chunk_linking")
    log.info("index.doc_chunks.complete", extra={"chunks": chunks, "edges": edges})


def batch_get_defs(
    engine: IndexCoordinatorEngine, def_uids: list[str]
) -> dict[str, DefFact]:
    """Get DefFacts by UID, using an in-memory cache."""
    if not def_uids:
        return {}
    if engine._def_cache is None:
        with engine.db.session() as session:
            all_defs = list(session.exec(select(DefFact)).all())
            # Expunge so objects are usable outside the session
            for d in all_defs:
                session.expunge(d)
            engine._def_cache = {d.def_uid: d for d in all_defs}
            log.debug(
                "def_cache.loaded",
                extra={"count": len(engine._def_cache)},
            )
    return {uid: engine._def_cache[uid] for uid in def_uids if uid in engine._def_cache}


def _clear_all_structural_facts(engine: IndexCoordinatorEngine) -> None:
    """Clear all structural facts from the database."""
    from sqlalchemy import text as sa_text
    with engine.db.session() as session:
        session.exec(sa_text("DELETE FROM def_facts"))  # type: ignore[call-overload]
        session.exec(sa_text("DELETE FROM ref_facts"))  # type: ignore[call-overload]
        session.exec(sa_text("DELETE FROM scope_facts"))  # type: ignore[call-overload]
        session.exec(sa_text("DELETE FROM import_facts"))  # type: ignore[call-overload]
        session.exec(sa_text("DELETE FROM local_bind_facts"))  # type: ignore[call-overload]
        session.exec(sa_text("DELETE FROM dynamic_access_sites"))  # type: ignore[call-overload]
        session.commit()


def _extract_symbols(engine: IndexCoordinatorEngine, file_path: Path) -> list[str]:
    """Extract symbol names from a file."""
    if engine._parser is None:
        return []
    try:
        content = file_path.read_bytes()
        result = engine._parser.parse(file_path, content)
        if result is None:
            return []
        symbols = engine._parser.extract_symbols(result)
        return [s.name for s in symbols]
    except (OSError, UnicodeDecodeError, ValueError):
        return []


def _safe_read_text(engine: IndexCoordinatorEngine, path: Path) -> str:
    """Read file text, treating binary/encoding errors as empty content."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        log.debug("safe_read_text_failed", exc_info=True)
        return ""


def _walk_all_files(engine: IndexCoordinatorEngine) -> list[str]:
    """Walk filesystem once, return all indexable file paths (relative to repo root)."""
    checker = IgnoreChecker.empty(engine.repo_root)
    # Eagerly load root-level ignore files
    for root_ignore in (
        engine.repo_root / ".recon" / IgnoreChecker.CPLIGNORE_NAME,
        engine.repo_root / IgnoreChecker.CPLIGNORE_NAME,
        engine.repo_root / ".gitignore",
    ):
        if root_ignore.exists():
            checker.load_ignore_file(root_ignore, "")
    all_files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(engine.repo_root):
        dirpath_p = Path(dirpath)
        rel_dir = str(dirpath_p.relative_to(engine.repo_root)).replace("\\", "/")
        prefix = "" if rel_dir == "." else rel_dir
        # Prune dirs in-place
        pruned: list[str] = []
        for d in dirnames:
            if checker.should_prune_dir(d):
                continue
            child_rel = f"{prefix}/{d}" if prefix else d
            if checker.should_prune_dir_path(child_rel):
                continue
            pruned.append(d)
        dirnames[:] = pruned
        # Load nested ignore files (skip root — already loaded above)
        if prefix:
            for ignore_name in (IgnoreChecker.CPLIGNORE_NAME, ".gitignore"):
                ignore_path = dirpath_p / ignore_name
                if ignore_path.exists():
                    checker.load_ignore_file(ignore_path, prefix)
        for filename in filenames:
            full_path = dirpath_p / filename
            rel_str = str(full_path.relative_to(engine.repo_root)).replace("\\", "/")
            # Skip .recon dir but NOT .reconignore files
            if rel_str.startswith(".recon/") and filename != ".reconignore":
                continue
            if not checker.is_excluded_rel(rel_str):
                all_files.append(rel_str)
    return all_files


def _filter_files_for_context(
    engine: IndexCoordinatorEngine,
    all_files: list[str],
    context_root: Path,
    include_globs: list[str],
    exclude_globs: list[str],
) -> list[Path]:
    """Filter pre-walked files for a specific context."""
    try:
        context_prefix = str(context_root.relative_to(engine.repo_root)).replace("\\", "/")
        if context_prefix == ".":
            context_prefix = ""
    except ValueError:
        context_prefix = ""
    # Pre-compile glob matchers once for the entire loop
    exclude_rx = _compile_glob_set(exclude_globs)
    include_rx = _compile_glob_set(include_globs)
    files: list[Path] = []
    for rel_str_repo in all_files:
        # Filter to files under context root
        if context_prefix:
            if not rel_str_repo.startswith(context_prefix + "/"):
                continue
            rel_str = rel_str_repo[len(context_prefix) + 1 :]
        else:
            rel_str = rel_str_repo
        # Check exclude globs (single regex for all patterns)
        if exclude_rx is not None and exclude_rx.search(rel_str):
            continue
        # Check include globs (empty = include all)
        if include_rx is not None and not include_rx.search(rel_str):
            continue
        full_path = engine.repo_root / rel_str_repo
        try:
            if full_path.is_file():
                files.append(full_path)
        except OSError:
            log.debug("file_access_error", path=rel_str)
    return files


def _filter_unclaimed_files(
    engine: IndexCoordinatorEngine,
    all_files: list[str],
    exclude_globs: list[str],
) -> list[Path]:
    """Filter pre-walked files for root fallback context."""
    exclude_rx = _compile_glob_set(exclude_globs)
    files: list[Path] = []
    for rel_str in all_files:
        if exclude_rx is not None and exclude_rx.search(rel_str):
            continue
        full_path = engine.repo_root / rel_str
        if full_path.is_file():
            files.append(full_path)
    return files
