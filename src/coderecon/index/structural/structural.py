"""Structural indexer — parallel file processing and fact persistence."""

from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from coderecon.index.db import Database
    from coderecon.index.db.database import BulkWriter

from coderecon.config.constants import MS_PER_SEC
from coderecon.index.structural.extract import (
    _extract_file,
)
from coderecon.index.structural.helpers import (
    BatchResult,
    ExtractionResult,
    _apply_worktree_uid_remap,
)
from coderecon.index.structural.resolve import (
    _augment_declared_modules,
    _resolve_import_paths,
    _resolve_xref_target,
)
from coderecon.index.structural.resolve import (
    resolve_all_imports as _resolve_all_imports_impl,
)
from coderecon.index.models import (
    DefFact,
    DocCrossRef,
    DynamicAccessSite,
    EndpointFact,
    File,
    ImportFact,
    InterfaceImplFact,
    LocalBindFact,
    MemberAccessFact,
    ReceiverShapeFact,
    RefFact,
    ScopeFact,
    TypeAnnotationFact,
    TypeMemberFact,
)

log = structlog.get_logger(__name__)

# Canonical list of per-file fact tables (keyed by file_id).
_FILE_FACT_TABLES: tuple[type, ...] = (
    DefFact, RefFact, LocalBindFact, ImportFact, DynamicAccessSite,
    TypeAnnotationFact, TypeMemberFact, MemberAccessFact, InterfaceImplFact,
    ReceiverShapeFact, EndpointFact, ScopeFact,
)

class StructuralIndexer:
    """Extracts facts from source files using Tree-sitter.
    This is the Tier 1 (syntactic) indexing layer. It provides:
    - DefFact extraction (function/class/method definitions)
    - RefFact extraction (identifier occurrences)
    - ScopeFact extraction (lexical scopes)
    - ImportFact extraction (import statements)
    - LocalBindFact extraction (same-file bindings)
    - DynamicAccessSite extraction (dynamic access telemetry)
    Files whose language has no tree-sitter grammar (e.g., F#, VB.NET, Erlang)
    are gracefully skipped by this indexer. They will still be searchable via
    the lexical (Tantivy) index.
    Usage::
        indexer = StructuralIndexer(db, repo_path)
        result = indexer.index_files(file_paths, context_id=1, worktree_id=wt_id)
    """
    def __init__(self, db: Database, repo_path: Path | str) -> None:
        self.db = db
        self.repo_path = Path(repo_path)
    def extract_files(
        self,
        file_paths: list[str],
        context_id: int,
        workers: int = 1,
        *,
        repo_root: Path | str | None = None,
    ) -> list[ExtractionResult]:
        """Extract facts from files without persisting.
        Returns ExtractionResult list that can be passed to
        index_files(_extractions=...) for persistence.
        Each result includes content_text and symbol_names for
        unified single-pass indexing (lexical + structural).
        ``repo_root`` overrides the default ``self.repo_path`` for file
        reading.  Pass the worktree checkout directory when indexing a
        git worktree so files are read from the correct location.
        """
        effective_root = Path(repo_root) if repo_root is not None else self.repo_path
        if workers > 1 and len(file_paths) > 1:
            return self._parallel_extract(file_paths, context_id, workers, repo_root=effective_root)
        return self._sequential_extract(file_paths, context_id, repo_root=effective_root)
    def index_files(
        self,
        file_paths: list[str],
        context_id: int,
        file_id_map: dict[str, int] | None = None,
        workers: int = 1,
        *,
        worktree_id: int,
        is_main_worktree: bool = True,
        _extractions: list[ExtractionResult] | None = None,
    ) -> BatchResult:
        """Index a batch of files.
        If _extractions is provided, uses pre-computed extraction results
        instead of extracting from disk. This enables single-pass indexing
        where the coordinator extracts once and reuses results for both
        Tantivy staging and structural fact persistence.
        """
        start = time.monotonic()
        result = BatchResult()
        if _extractions is not None:
            extractions = _extractions
        elif workers > 1:
            extractions = self._parallel_extract(file_paths, context_id, workers)
        else:
            extractions = self._sequential_extract(file_paths, context_id)
        # Augment declared_module for languages needing config file resolution
        # (Go → go.mod, Rust → Cargo.toml). Tree-sitter gives Go only the
        # short package name; the full import path needs go.mod context.
        self._augment_declared_modules(extractions)
        # Resolve import source_literal → target file path (all languages).
        # Must run after _augment_declared_modules so Go/Rust declared_modules
        # are fully resolved.  Populates import_dict["resolved_path"].
        self._resolve_import_paths(extractions)
        # Pre-create all files BEFORE entering bulk_writer to avoid lock contention
        if file_id_map is None:
            file_id_map = {}
        for extraction in extractions:
            if extraction.error:
                # Record parse failure on the File row even though we won't extract facts
                if extraction.file_path not in file_id_map:
                    file_id_map[extraction.file_path] = self._ensure_file_id(
                        extraction.file_path,
                        extraction.content_hash,
                        extraction.line_count,
                        context_id,
                        language_family=extraction.language_family,
                        worktree_id=worktree_id,
                        parse_status="failed",
                    )
                continue
            if extraction.file_path not in file_id_map:
                # Determine parse_status from extraction result
                _ps = "ok"
                if extraction.skipped_no_grammar:
                    _ps = "skipped"
                file_id_map[extraction.file_path] = self._ensure_file_id(
                    extraction.file_path,
                    extraction.content_hash,
                    extraction.line_count,
                    context_id,
                    language_family=extraction.language_family,
                    declared_module=extraction.declared_module,
                    worktree_id=worktree_id,
                    parse_status=_ps,
                )
        # Remap def_uid / import_uid to include worktree_id so that two
        # worktrees indexing the same file don't collide on PK constraints.
        for extraction in extractions:
            if not extraction.error:
                _apply_worktree_uid_remap(
                    extraction, worktree_id, is_main_worktree=is_main_worktree
                )
        with self.db.bulk_writer() as writer:
            for extraction in extractions:
                result.files_processed += 1
                if extraction.error:
                    result.errors.append(f"{extraction.file_path}: {extraction.error}")
                    continue
                if extraction.skipped_no_grammar:
                    result.files_skipped_no_grammar += 1
                    continue
                file_id = file_id_map.get(extraction.file_path)
                if file_id is None:
                    result.errors.append(f"{extraction.file_path}: File ID not found")
                    continue
                _persist_single_extraction(writer, extraction, file_id, result, self)
        result.duration_ms = int((time.monotonic() - start) * MS_PER_SEC)
        return result
    def _augment_declared_modules(self, extractions: list[ExtractionResult]) -> None:
        """Post-process declared_module for languages needing config files."""
        _augment_declared_modules(self.db, self.repo_path, extractions)
    def _resolve_xref_target(self, writer: BulkWriter, target_name: str) -> str | None:
        """Resolve a cross-ref target name to a def_uid."""
        return _resolve_xref_target(writer, target_name)
    def _resolve_import_paths(self, extractions: list[ExtractionResult]) -> None:
        """Resolve import source_literal to target file path."""
        _resolve_import_paths(self.db, self.repo_path, extractions)
    def resolve_all_imports(self) -> int:
        """Re-resolve all unresolved import paths using the complete DB."""
        return _resolve_all_imports_impl(self.db, self.repo_path)
    def _sequential_extract(
        self, file_paths: list[str], unit_id: int, repo_root: Path | None = None
    ) -> list[ExtractionResult]:
        """Extract facts sequentially."""
        root = str(repo_root if repo_root is not None else self.repo_path)
        results = []
        for path in file_paths:
            result = _extract_file(path, root, unit_id)
            results.append(result)
        return results
    def _parallel_extract(
        self, file_paths: list[str], unit_id: int, workers: int, repo_root: Path | None = None
    ) -> list[ExtractionResult]:
        """Extract facts in parallel using process pool."""
        results = []
        root = str(repo_root if repo_root is not None else self.repo_path)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_extract_file, path, root, unit_id): path
                for path in file_paths
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except (OSError, UnicodeDecodeError, RuntimeError, ValueError) as e:
                    path = futures[future]
                    results.append(ExtractionResult(file_path=path, error=str(e)))
        return results
    def _ensure_file_id(
        self,
        file_path: str,
        content_hash: str | None,
        line_count: int,
        _context_id: int,
        language_family: str | None = None,
        declared_module: str | None = None,
        *,
        worktree_id: int,
        parse_status: str | None = None,
    ) -> int:
        """Ensure file exists in database and return its ID."""
        import time
        with self.db.session() as session:
            from sqlmodel import select
            stmt = select(File).where(
                File.path == file_path,
                File.worktree_id == worktree_id,
            )
            existing = session.exec(stmt).first()
            if existing and existing.id is not None:
                _changed = False
                if existing.declared_module != declared_module:
                    existing.declared_module = declared_module
                    _changed = True
                if parse_status is not None and existing.parse_status != parse_status:
                    existing.parse_status = parse_status
                    _changed = True
                if content_hash is not None and existing.content_hash != content_hash:
                    existing.content_hash = content_hash
                    existing.line_count = line_count
                    _changed = True
                if _changed:
                    session.add(existing)
                    session.commit()
                return existing.id
            file = File(
                path=file_path,
                content_hash=content_hash,
                line_count=line_count,
                language_family=language_family,
                declared_module=declared_module,
                indexed_at=time.time(),
                worktree_id=worktree_id,
                parse_status=parse_status,
            )
            session.add(file)
            session.commit()
            session.refresh(file)
            if file.id is None:
                raise RuntimeError(f"Failed to allocate file id for {file_path!r}")
            return file.id
    def extract_single(self, file_path: str, unit_id: int = 0) -> ExtractionResult:
        """Extract facts from a single file without storing."""
        return _extract_file(file_path, str(self.repo_path), unit_id)
def index_context(
    db: Database,
    repo_path: Path | str,
    context_id: int,
    file_paths: list[str],
    workers: int = os.cpu_count() or 1,
    *,
    worktree_id: int,
) -> BatchResult:
    """Convenience function to index all files in a context."""
    indexer = StructuralIndexer(db, repo_path)
    return indexer.index_files(file_paths, context_id, workers=workers, worktree_id=worktree_id)


def _persist_single_extraction(
    writer: Any,
    extraction: ExtractionResult,
    file_id: int,
    result: BatchResult,
    indexer: StructuralIndexer,
) -> None:
    """Persist all facts from a single extraction into the database."""
    # Delete existing facts for idempotent re-indexing
    for fact_model in _FILE_FACT_TABLES:
        writer.delete_where(fact_model, "file_id = :fid", {"fid": file_id})
    writer.delete_where(DocCrossRef, "source_file_id = :fid", {"fid": file_id})
    # Build local_scope_id -> db_scope_id mapping
    scope_id_map: dict[int, int] = {}
    from sqlalchemy import text as _sa_text
    for scope_dict in extraction.scopes:
        local_id = scope_dict.pop("local_scope_id")
        parent_local_id = scope_dict.pop("parent_local_scope_id")
        scope_dict["file_id"] = file_id
        scope_dict["parent_scope_id"] = (
            scope_id_map[parent_local_id]
            if parent_local_id is not None and parent_local_id in scope_id_map
            else None
        )
        writer.insert_many(ScopeFact, [scope_dict])
        row = writer.conn.execute(_sa_text("SELECT last_insert_rowid()")).fetchone()
        if row is not None:
            scope_id_map[local_id] = row[0]
        result.scopes_extracted += 1
    for def_dict in extraction.defs:
        def_dict["file_id"] = file_id
        writer.insert_many(DefFact, [def_dict])
        result.defs_extracted += 1
    for ref_dict in extraction.refs:
        ref_dict["file_id"] = file_id
        local_sid = ref_dict.pop("local_scope_id", None)
        ref_dict["scope_id"] = scope_id_map.get(local_sid) if local_sid else None
        writer.insert_many(RefFact, [ref_dict])
        result.refs_extracted += 1
    seen_import_uids: set[str] = set()
    for import_dict in extraction.imports:
        uid = import_dict.get("import_uid")
        if isinstance(uid, str) and uid:
            if uid in seen_import_uids:
                continue
            seen_import_uids.add(uid)
        import_dict["file_id"] = file_id
        import_dict.pop("_start_line", None)
        import_dict.pop("_start_col", None)
        writer.insert_many(ImportFact, [import_dict])
        result.imports_extracted += 1
    for bind_dict in extraction.binds:
        bind_dict["file_id"] = file_id
        local_sid = bind_dict.pop("local_scope_id", None)
        bind_dict["scope_id"] = scope_id_map.get(local_sid) if local_sid else None
        writer.insert_many(LocalBindFact, [bind_dict])
        result.binds_extracted += 1
    for dyn_dict in extraction.dynamic_sites:
        dyn_dict["file_id"] = file_id
        writer.insert_many(DynamicAccessSite, [dyn_dict])
        result.dynamic_sites_extracted += 1
    for ann_dict in extraction.type_annotations:
        ann_dict["file_id"] = file_id
        writer.insert_many(TypeAnnotationFact, [ann_dict])
        result.type_annotations_extracted += 1
    for mem_dict in extraction.type_members:
        mem_dict["file_id"] = file_id
        writer.insert_many(TypeMemberFact, [mem_dict])
        result.type_members_extracted += 1
    for acc_dict in extraction.member_accesses:
        acc_dict["file_id"] = file_id
        writer.insert_many(MemberAccessFact, [acc_dict])
        result.member_accesses_extracted += 1
    for impl_dict in extraction.interface_impls:
        impl_dict["file_id"] = file_id
        writer.insert_many(InterfaceImplFact, [impl_dict])
        result.interface_impls_extracted += 1
    for shape_dict in extraction.receiver_shapes:
        shape_dict["file_id"] = file_id
        writer.insert_many(ReceiverShapeFact, [shape_dict])
        result.receiver_shapes_extracted += 1
    # Detect and insert EndpointFacts
    if extraction.content_text and extraction.language:
        from coderecon.index.analysis.endpoint_detection import (
            detect_endpoints_in_source,
        )
        endpoints = detect_endpoints_in_source(
            extraction.content_text, extraction.language
        )
        if endpoints:
            func_defs = [
                d for d in extraction.defs
                if d.get("kind") in ("function", "method")
            ]
            for ep in endpoints:
                handler_uid = None
                for d in func_defs:
                    if d["start_line"] <= ep.line <= d["end_line"]:
                        handler_uid = d["def_uid"]
                writer.insert_many(EndpointFact, [{
                    "file_id": file_id,
                    "kind": ep.kind,
                    "http_method": ep.http_method,
                    "url_pattern": ep.url_pattern,
                    "handler_def_uid": handler_uid,
                    "start_line": ep.line,
                    "end_line": ep.line,
                    "framework": ep.framework,
                }])
    # Extract and insert DocCrossRefs from docstrings
    if extraction.defs:
        from coderecon.index.analysis.docstring_xref import (
            extract_cross_refs,
        )
        for def_dict in extraction.defs:
            docstring = def_dict.get("docstring")
            if not docstring:
                continue
            raw_refs = extract_cross_refs(
                docstring, start_line=def_dict["start_line"]
            )
            if not raw_refs:
                continue
            for ref in raw_refs:
                target_uid = indexer._resolve_xref_target(
                    writer, ref.target_name
                )
                if target_uid:
                    writer.insert_many(DocCrossRef, [{
                        "source_file_id": file_id,
                        "source_def_uid": def_dict.get("def_uid"),
                        "source_line": ref.source_line,
                        "raw_text": ref.raw_text,
                        "target_def_uid": target_uid,
                        "confidence": ref.confidence,
                    }])
