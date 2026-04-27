"""Structural index import/module resolution — post-extraction passes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from coderecon.index._internal.db import Database
    from coderecon.index._internal.db.database import BulkWriter

from coderecon.index._internal.indexing.structural_helpers import ExtractionResult
from coderecon.index.models import File

log = structlog.get_logger(__name__)


def _augment_declared_modules(db: Database, repo_path: Path, extractions: list[ExtractionResult]) -> None:
    """Post-process declared_module for languages needing config files.
    Go files get only the short package name from tree-sitter (e.g.
    ``mypackage``).  This method resolves the full import path using
    ``go.mod`` (e.g. ``github.com/user/repo/pkg/mypackage``).
    Rust files have no source-level package declaration.  The crate
    name is read from ``Cargo.toml`` and combined with the directory
    structure (e.g. ``my_crate::auth::token``).
    During batched initial indexing, config files (go.mod, Cargo.toml)
    may have been indexed in an earlier batch.  We seed the resolver
    with ALL file paths from the DB so config discovery works cross-batch.
    """
    from sqlmodel import select

    from coderecon.index._internal.indexing.config_resolver import (
        ConfigResolver,
    )
    # Seed file paths from DB (cross-batch config discovery)
    all_paths_set: set[str] = set()
    with db.session() as session:
        rows = session.exec(select(File.path)).all()
        for path in rows:
            all_paths_set.add(path)
    # Overlay current batch paths
    for e in extractions:
        if not e.error:
            all_paths_set.add(e.file_path)
    # Config files (go.mod, Cargo.toml) are not source code so they
    # may not be indexed by tree-sitter.  Discover them from the
    # filesystem so ConfigResolver can find them.
    for pattern in ("**/go.mod", "**/Cargo.toml"):
        for cfg in repo_path.glob(pattern):
            all_paths_set.add(str(cfg.relative_to(repo_path)))
    resolver = ConfigResolver(str(repo_path), list(all_paths_set))
    def _read_file(rel_path: str) -> str | None:
        full = repo_path / rel_path
        try:
            return full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            log.debug("module_config_read_failed", exc_info=True)
            return None
    for ex in extractions:
        if ex.error or ex.skipped_no_grammar:
            continue
        lang = ex.language
        if lang in ("go", "rust"):
            resolved = resolver.resolve(
                ex.file_path, lang, ex.declared_module, read_file=_read_file
            )
            if resolved:
                ex.declared_module = resolved
    # Fallback: derive declared_module from file path for languages
    # that don't have source-level module declarations (e.g. Python, JS/TS)
    from coderecon.index._internal.indexing.module_mapping import path_to_module
    for ex in extractions:
        if ex.error or ex.skipped_no_grammar:
            continue
        if ex.declared_module is None:
            ex.declared_module = path_to_module(ex.file_path)
def _resolve_xref_target(writer: BulkWriter, target_name: str) -> str | None:
    """Resolve a cross-ref target name to a def_uid using the BulkWriter's connection."""
    from sqlalchemy import text as sa_text
    conn = writer.conn
    # 1. Exact def_uid
    row = conn.execute(
        sa_text("SELECT def_uid FROM def_facts WHERE def_uid = :name LIMIT 1"),
        {"name": target_name},
    ).fetchone()
    if row:
        return row[0]
    # 2. Name-only match
    simple_name = target_name.rsplit(".", 1)[-1]
    row = conn.execute(
        sa_text("SELECT def_uid FROM def_facts WHERE name = :name LIMIT 1"),
        {"name": simple_name},
    ).fetchone()
    if row:
        return row[0]
    # 3. Suffix match
    row = conn.execute(
        sa_text("SELECT def_uid FROM def_facts WHERE def_uid LIKE :suffix LIMIT 1"),
        {"suffix": f"%.{target_name}"},
    ).fetchone()
    if row:
        return row[0]
    return None
def _resolve_import_paths(db: Database, repo_path: Path, extractions: list[ExtractionResult]) -> None:
    """Resolve every import's source_literal to a target file path.
    Populates ``import_dict["resolved_path"]`` for each import in each
    extraction.  Uses the ``ImportPathResolver`` which supports:
    - Python: dotted module → file path via ``module_mapping``
    - JS/TS: relative path resolution with extension probing
    - C/C++: relative header resolution
    - Declaration-based (Java, Kotlin, etc.): match against ``declared_module``
    During initial indexing files are processed in batches of 50.  To
    resolve cross-batch imports (e.g. a Java import in batch 3 targeting
    a class declared in batch 1) we seed the resolver with ALL file paths
    and declared_modules already persisted in the DB, then overlay the
    current batch on top (which may have fresher data).
    """
    from sqlmodel import select

    from coderecon.index._internal.indexing.config_resolver import (
        ImportPathResolver,
        build_js_package_exports,
    )
    valid = [e for e in extractions if not e.error and not e.skipped_no_grammar]
    # Seed from DB: all previously-indexed file paths + declared_modules.
    # This ensures cross-batch resolution works during initial indexing.
    all_paths_set: set[str] = set()
    declared_modules: dict[str, str] = {}
    with db.session() as session:
        rows = session.exec(select(File.path, File.declared_module)).all()
        for path, dm in rows:
            all_paths_set.add(path)
            if dm:
                declared_modules[path] = dm
    # Overlay current batch (may contain new/updated data not yet persisted).
    for e in valid:
        all_paths_set.add(e.file_path)
        if e.declared_module:
            declared_modules[e.file_path] = e.declared_module
    all_paths_list = list(all_paths_set)
    # Build JS/TS package.json exports map for bare specifier resolution
    def _read_file(rel_path: str) -> str | None:
        full = repo_path / rel_path
        try:
            return full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            log.debug("js_package_read_failed", exc_info=True)
            return None
    js_exports = build_js_package_exports(all_paths_list, _read_file)
    resolver = ImportPathResolver(all_paths_list, declared_modules, js_exports)
    for ex in valid:
        for imp in ex.imports:
            source_literal = imp.get("source_literal")
            import_kind = imp.get("import_kind", "")
            resolved = resolver.resolve(source_literal, import_kind, ex.file_path)
            if resolved:
                imp["resolved_path"] = resolved
def resolve_all_imports(db: Database, repo_path: Path) -> int:
    """Re-resolve all unresolved import paths using the complete DB.
    Called once after all batches have been indexed so that imports
    from early batches can resolve to files indexed in later batches.
    Returns:
        Number of imports that were newly resolved.
    """
    from sqlmodel import select

    from coderecon.index._internal.indexing.config_resolver import (
        ImportPathResolver,
        build_js_package_exports,
    )
    from coderecon.index.models import ImportFact
    # Build resolver from the complete DB
    all_paths: list[str] = []
    declared_modules: dict[str, str] = {}
    with db.session() as session:
        rows = session.exec(select(File.path, File.declared_module)).all()
        for path, dm in rows:
            all_paths.append(path)
            if dm:
                declared_modules[path] = dm
    # Build JS/TS package.json exports map for bare specifier resolution
    def _read_file(rel_path: str) -> str | None:
        full = repo_path / rel_path
        try:
            return full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            log.debug("js_package_read_failed", exc_info=True)
            return None
    js_exports = build_js_package_exports(all_paths, _read_file)
    resolver = ImportPathResolver(all_paths, declared_modules, js_exports)
    # Find all unresolved imports and try to resolve them
    newly_resolved = 0
    with db.session() as session:
        stmt = (
            select(ImportFact)
            .where(ImportFact.resolved_path.is_(None))  # type: ignore[union-attr]
            .where(ImportFact.source_literal.isnot(None))  # type: ignore[union-attr]
        )
        unresolved = list(session.exec(stmt).all())
        # Build file_id -> path mapping for importer_path lookup
        file_ids = {imp.file_id for imp in unresolved}
        file_map: dict[int, str] = {}
        if file_ids:
            file_rows = session.exec(
                select(File.id, File.path).where(
                    File.id.in_(file_ids)  # type: ignore[union-attr]
                )
            ).all()
            file_map = {int(fid): fp for fid, fp in file_rows if fid is not None}
        for imp in unresolved:
            importer_path = file_map.get(imp.file_id, "")
            resolved = resolver.resolve(
                imp.source_literal, imp.import_kind or "", importer_path
            )
            if resolved:
                imp.resolved_path = resolved
                session.add(imp)
                newly_resolved += 1
        if newly_resolved:
            session.commit()
    return newly_resolved
