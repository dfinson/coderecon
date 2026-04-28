"""Cross-file DB-backed resolution passes for Go, Rust, and Java.

Also contains the resolution pass registry and ``run_pass_1_5`` orchestrator.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel import col, select

from coderecon.index._internal.indexing.resolver_crossfile import (
    CrossFileResolutionStats,
    ResolutionPassFn,
    _build_file_filter,
    _build_unit_filter,
    resolve_namespace_refs,
    resolve_same_namespace_refs,
    resolve_star_import_refs,
)
from coderecon.index.models import (
    Certainty,
    File,
    ImportFact,
    RefTier,
    Role,
)

if TYPE_CHECKING:
    from coderecon.index._internal.db import Database


def resolve_go_dot_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Go dot-import evidence (DB-backed).

    For each file with ``import . "pkg"``, resolves the source package
    to a project file, and upgrades matching UNKNOWN refs to STRONG.
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    with db.session() as session:
        # Step 1: Find all dot imports (imported_name == "*" for go_import)
        dot_stmt = select(ImportFact).where(
            ImportFact.imported_name == "*",
            ImportFact.import_kind == "go_import",
        )
        if file_ids:
            dot_stmt = dot_stmt.where(col(ImportFact.file_id).in_(file_ids))
        if unit_id is not None:
            dot_stmt = dot_stmt.where(ImportFact.unit_id == unit_id)
        dot_imports = list(session.exec(dot_stmt).all())
        if not dot_imports:
            return stats
        # Step 2: Build package path -> file_id mapping for Go files
        all_files: list[tuple[int | None, str]] = list(
            session.exec(select(File.id, File.path)).all()
        )
        pkg_to_file_id: dict[str, int] = {}
        for fid, fpath in all_files:
            if fid is None or fpath is None:
                continue
            if fpath.endswith(".go") and not fpath.endswith("_test.go"):
                # Use directory as package path
                pkg_path = fpath.rsplit("/", 1)[0] if "/" in fpath else ""
                if pkg_path:
                    pkg_to_file_id[pkg_path] = fid
        # Step 3: Create temp mapping table
        session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS _go_dot_map "
                "(importing_file_id INTEGER, source_file_id INTEGER)"
            )
        )
        session.execute(text("DELETE FROM _go_dot_map"))
        mappings: list[dict[str, int]] = []
        for dot_imp in dot_imports:
            source_literal = dot_imp.source_literal
            if not source_literal:
                continue
            # Try to find a matching Go file by package path suffix
            source_file_id = _find_go_package_file(source_literal, pkg_to_file_id)
            if source_file_id is not None:
                mappings.append(
                    {
                        "importing_file_id": dot_imp.file_id,
                        "source_file_id": source_file_id,
                    }
                )
        if not mappings:
            session.execute(text("DROP TABLE IF EXISTS _go_dot_map"))
            return stats
        for m in mappings:
            session.execute(
                text("INSERT INTO _go_dot_map VALUES (:importing_file_id, :source_file_id)"),
                m,
            )
        # Step 4: Count eligible refs - exported Go symbols are capitalized
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN _go_dot_map tsm ON tsm.importing_file_id = rf.file_id
            JOIN def_facts df ON df.file_id = tsm.source_file_id
                AND df.name = rf.token_text
                AND df.name GLOB '[A-Z]*'
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()
        if stats.refs_matched == 0:
            session.execute(text("DROP TABLE IF EXISTS _go_dot_map"))
            return stats
        # Step 5: Upgrade refs
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM _go_dot_map tsm
                    JOIN def_facts df ON df.file_id = tsm.source_file_id
                        AND df.name = ref_facts.token_text
                        AND df.name GLOB '[A-Z]*'
                    WHERE tsm.importing_file_id = ref_facts.file_id
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN _go_dot_map tsm ON tsm.importing_file_id = rf.file_id
                JOIN def_facts df ON df.file_id = tsm.source_file_id
                    AND df.name = rf.token_text
                    AND df.name GLOB '[A-Z]*'
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]
        session.execute(text("DROP TABLE IF EXISTS _go_dot_map"))
        session.commit()
    return stats


def _find_go_package_file(
    import_path: str,
    pkg_to_file_id: dict[str, int],
) -> int | None:
    """Resolve Go import path to a file_id via whole-segment suffix matching."""
    import_segments = [seg for seg in import_path.split("/") if seg]
    for pkg_path, fid in pkg_to_file_id.items():
        pkg_segments = [seg for seg in pkg_path.split("/") if seg]
        # Check if pkg_path is a suffix of import_path (segment-wise)
        if (
            len(pkg_segments) <= len(import_segments)
            and import_segments[-len(pkg_segments) :] == pkg_segments
        ):
            return fid
    return None


def resolve_rust_glob_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Rust glob-import evidence (DB-backed).

    For each file with ``use crate::module::*;``, resolves the source module
    to a project file, and upgrades matching UNKNOWN refs to STRONG.
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    with db.session() as session:
        # Step 1: Find all glob imports (imported_name == "*" for rust_use)
        glob_stmt = select(ImportFact).where(
            ImportFact.imported_name == "*",
            ImportFact.import_kind == "rust_use",
        )
        if file_ids:
            glob_stmt = glob_stmt.where(col(ImportFact.file_id).in_(file_ids))
        if unit_id is not None:
            glob_stmt = glob_stmt.where(ImportFact.unit_id == unit_id)
        glob_imports = list(session.exec(glob_stmt).all())
        if not glob_imports:
            return stats
        # Step 2: Build module path -> file_id mapping for Rust files
        all_files: list[tuple[int | None, str]] = list(
            session.exec(select(File.id, File.path)).all()
        )
        module_to_file_id: dict[str, int] = {}
        for fid, fpath in all_files:
            if fid is None or fpath is None:
                continue
            if fpath.endswith(".rs"):
                # Convert path to Rust module path (e.g., src/lib.rs -> crate)
                module_path = _path_to_rust_module(fpath)
                if module_path:
                    module_to_file_id[module_path] = fid
        # Step 3: Create temp mapping table
        session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS _rust_glob_map "
                "(importing_file_id INTEGER, source_file_id INTEGER)"
            )
        )
        session.execute(text("DELETE FROM _rust_glob_map"))
        mappings: list[dict[str, int]] = []
        for glob_imp in glob_imports:
            source_literal = glob_imp.source_literal
            if not source_literal:
                continue
            source_file_id = _find_rust_module_file(source_literal, module_to_file_id)
            if source_file_id is not None:
                mappings.append(
                    {
                        "importing_file_id": glob_imp.file_id,
                        "source_file_id": source_file_id,
                    }
                )
        if not mappings:
            session.execute(text("DROP TABLE IF EXISTS _rust_glob_map"))
            return stats
        for m in mappings:
            session.execute(
                text("INSERT INTO _rust_glob_map VALUES (:importing_file_id, :source_file_id)"),
                m,
            )
        # Step 4: Count eligible refs - public Rust symbols
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN _rust_glob_map tsm ON tsm.importing_file_id = rf.file_id
            JOIN def_facts df ON df.file_id = tsm.source_file_id
                AND df.name = rf.token_text
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()
        if stats.refs_matched == 0:
            session.execute(text("DROP TABLE IF EXISTS _rust_glob_map"))
            return stats
        # Step 5: Upgrade refs
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM _rust_glob_map tsm
                    JOIN def_facts df ON df.file_id = tsm.source_file_id
                        AND df.name = ref_facts.token_text
                    WHERE tsm.importing_file_id = ref_facts.file_id
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN _rust_glob_map tsm ON tsm.importing_file_id = rf.file_id
                JOIN def_facts df ON df.file_id = tsm.source_file_id
                    AND df.name = rf.token_text
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]
        session.execute(text("DROP TABLE IF EXISTS _rust_glob_map"))
        session.commit()
    return stats


def _path_to_rust_module(path: str) -> str | None:
    """Convert file path to Rust module path."""
    if not path.endswith(".rs"):
        return None
    # Remove .rs extension
    module = path[:-3]
    # Map lib.rs and mod.rs to parent directory, main.rs to crate
    if module.endswith("/lib") or module.endswith("/mod"):
        module = module.rsplit("/", 1)[0]
    elif module.endswith("/main"):
        return "crate"
    # Convert path separators to ::
    module = module.replace("/", "::").replace("\\", "::")
    # Handle src/ prefix
    if module.startswith("src::"):
        module = "crate::" + module[5:]
    return module


def _find_rust_module_file(
    source_literal: str,
    module_to_file_id: dict[str, int],
) -> int | None:
    """Resolve Rust use path to a file_id."""
    # Direct match
    if source_literal in module_to_file_id:
        return module_to_file_id[source_literal]
    # Try suffix matching
    for mod_path, fid in module_to_file_id.items():
        if mod_path.endswith(source_literal) or source_literal.endswith(mod_path.split("::")[-1]):
            return fid
    return None


def resolve_java_star_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Java star-import evidence (DB-backed).

    For each file with ``import com.foo.*;``, resolves types from the
    imported package and upgrades matching UNKNOWN refs to STRONG.
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    def_unit_filter, def_unit_binds = _build_unit_filter(unit_id, "df")
    # Java type kinds to match
    java_type_kinds = "('class', 'interface', 'enum', 'record', 'annotation')"
    with db.session() as session:
        # Count refs that will be upgraded.
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN import_facts imf ON rf.file_id = imf.file_id
                AND imf.import_kind = 'java_import'
                AND imf.imported_name = '*'
            JOIN def_facts df ON df.name = rf.token_text
                AND df.namespace = imf.source_literal
                AND df.kind IN {java_type_kinds}
                {def_unit_filter}
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **def_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()
        if stats.refs_matched == 0:
            return stats
        # Perform the upgrade
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM import_facts imf
                    JOIN def_facts df ON df.name = ref_facts.token_text
                        AND df.namespace = imf.source_literal
                        AND df.kind IN {java_type_kinds}
                        {def_unit_filter}
                    WHERE imf.file_id = ref_facts.file_id
                        AND imf.import_kind = 'java_import'
                        AND imf.imported_name = '*'
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN import_facts imf ON rf.file_id = imf.file_id
                    AND imf.import_kind = 'java_import'
                    AND imf.imported_name = '*'
                JOIN def_facts df ON df.name = rf.token_text
                    AND df.namespace = imf.source_literal
                    AND df.kind IN {java_type_kinds}
                    {def_unit_filter}
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **def_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]
        session.commit()
    return stats


# Resolution Registry — add new passes to _RESOLUTION_PASSES below.

_RESOLUTION_PASSES: list[ResolutionPassFn] = []


def _register_resolution_passes() -> None:
    """Populate the resolution pass registry.

    Called at module load time after all resolution functions are defined.
    """
    _RESOLUTION_PASSES.clear()
    _RESOLUTION_PASSES.extend([
        resolve_namespace_refs,  # C# namespace-using
        resolve_same_namespace_refs,  # C# same/parent namespace visibility
        resolve_star_import_refs,  # Python from X import *
        resolve_go_dot_import_refs,  # Go import . "pkg"
        resolve_rust_glob_import_refs,  # Rust use module::*
        resolve_java_star_import_refs,  # Java import pkg.*
    ])


def run_pass_1_5(
    db: Database,
    unit_id: int | None = None,
    file_ids: list[int] | None = None,
) -> list[CrossFileResolutionStats]:
    """Run all registered Pass 1.5 cross-file resolution passes in parallel.

    Each language-specific pass runs in its own thread with its own DB session.
    Passes are independent (touch disjoint ref subsets).
    """
    if not _RESOLUTION_PASSES:
        _register_resolution_passes()
    with ThreadPoolExecutor(max_workers=len(_RESOLUTION_PASSES)) as pool:
        futures = [pool.submit(fn, db, unit_id, file_ids) for fn in _RESOLUTION_PASSES]
        return [f.result() for f in futures]
