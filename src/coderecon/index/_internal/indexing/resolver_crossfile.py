"""Cross-file DB-backed resolution passes for Python and C#.

Pass 1.5 runs AFTER all structural facts are persisted to the DB,
resolving namespace imports, star imports, and same-namespace visibility.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel import col, select

from coderecon.index.models import (
    Certainty,
    File,
    ImportFact,
    RefTier,
    Role,
)

if TYPE_CHECKING:
    from coderecon.index._internal.db import Database

# Type alias for resolution pass signature
ResolutionPassFn = Callable[["Database", int | None, list[int] | None], "CrossFileResolutionStats"]


@dataclass
class CrossFileResolutionStats:
    """Statistics from cross-file DB-backed resolution."""
    refs_upgraded: int = 0
    refs_matched: int = 0


# Resolvable C# type declaration kinds — used in SQL IN-lists.
# SYNC: Must match the tree-sitter node types in ``_TYPE_DECLS`` within
# ``treesitter.py::extract_csharp_namespace_types``, modulo the
# ``_declaration`` suffix that generic symbol extraction strips.
_TYPE_KINDS: tuple[str, ...] = ("class", "struct", "interface", "enum", "record", "record_struct")
_TYPE_KIND_FILTER = f"({', '.join(repr(k) for k in _TYPE_KINDS)})"


def _build_file_filter(
    file_ids: list[int] | None,
    alias: str = "rf",
) -> tuple[str, dict[str, int]]:
    """Build a parameterized file_id filter clause and bind values.

    Returns:
        (sql_fragment, bind_dict) where sql_fragment is either empty or
        ``AND <alias>.file_id IN (:fid_0, :fid_1, ...)`` and bind_dict
        maps the placeholder names to integer values.
    """
    if not file_ids:
        return ("", {})
    placeholders = [f":fid_{i}" for i in range(len(file_ids))]
    sql = f"AND {alias}.file_id IN ({', '.join(placeholders)})"
    binds = {f"fid_{i}": fid for i, fid in enumerate(file_ids)}
    return (sql, binds)


def _build_unit_filter(
    unit_id: int | None,
    alias: str = "rf",
) -> tuple[str, dict[str, int]]:
    """Build a parameterized unit_id filter clause and bind values.

    Returns:
        (sql_fragment, bind_dict) where sql_fragment is either empty or
        ``AND <alias>.unit_id = :unit_id`` and bind_dict maps the
        placeholder name to the integer value.
    """
    if unit_id is None:
        return ("", {})
    sql = f"AND {alias}.unit_id = :unit_id"
    binds = {"unit_id": unit_id}
    return (sql, binds)


def resolve_namespace_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using C# namespace-using evidence (DB-backed).

    For each UNKNOWN REFERENCE ref in a file with ``using Namespace;``
    directives, checks if the ref's token_text matches a DefFact whose
    namespace equals one of the file's using'd namespaces.
    Matching refs are upgraded to STRONG.
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    def_unit_filter, def_unit_binds = _build_unit_filter(unit_id, "df")
    with db.session() as session:
        # Count refs that will be upgraded (for stats)
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN import_facts imf ON rf.file_id = imf.file_id
                AND imf.import_kind = 'csharp_using'
                AND imf.alias IS NULL
            JOIN def_facts df ON df.name = rf.token_text
                AND df.namespace = imf.imported_name
                AND df.kind IN {_TYPE_KIND_FILTER}
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
        # Perform the upgrade — also link target_def_uid so the rename
        # code path can find these refs via list_refs_by_def_uid().
        # Use a correlated subquery to resolve def_uid for each ref.
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM import_facts imf
                    JOIN def_facts df ON df.name = ref_facts.token_text
                        AND df.namespace = imf.imported_name
                        AND df.kind IN {_TYPE_KIND_FILTER}
                        {def_unit_filter}
                    WHERE imf.file_id = ref_facts.file_id
                        AND imf.import_kind = 'csharp_using'
                        AND imf.alias IS NULL
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN import_facts imf ON rf.file_id = imf.file_id
                    AND imf.import_kind = 'csharp_using'
                    AND imf.alias IS NULL
                JOIN def_facts df ON df.name = rf.token_text
                    AND df.namespace = imf.imported_name
                    AND df.kind IN {_TYPE_KIND_FILTER}
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


def resolve_star_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Python star-import evidence (DB-backed).

    For each file with ``from X import *``, resolves the source module to a
    project file, builds the set of module-level exports, and upgrades
    matching UNKNOWN refs to STRONG via a single SQL UPDATE.
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    with db.session() as session:
        # Step 1: Find all star imports
        star_stmt = select(ImportFact).where(
            ImportFact.imported_name == "*",
            ImportFact.import_kind == "python_from",
        )
        if file_ids:
            star_stmt = star_stmt.where(col(ImportFact.file_id).in_(file_ids))
        if unit_id is not None:
            star_stmt = star_stmt.where(ImportFact.unit_id == unit_id)
        star_imports = list(session.exec(star_stmt).all())
        if not star_imports:
            return stats
        # Step 2: Build module path -> file_id mapping (Python heuristic)
        all_files: list[tuple[int | None, str]] = list(
            session.exec(select(File.id, File.path)).all()
        )
        module_to_file_id: dict[str, int] = {}
        for fid, fpath in all_files:
            if fid is None or fpath is None:
                continue
            module_path = _path_to_python_module(fpath)
            if module_path:
                module_to_file_id[module_path] = fid
        # Step 3: Resolve each star import's source to a file_id,
        # build the temp mapping table.
        session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS _star_module_map "
                "(importing_file_id INTEGER, source_file_id INTEGER)"
            )
        )
        session.execute(text("DELETE FROM _star_module_map"))
        mappings: list[dict[str, int]] = []
        for star_imp in star_imports:
            source_literal = star_imp.source_literal
            if not source_literal:
                continue
            source_file_id = _find_python_module_file(
                source_literal, star_imp.file_id, module_to_file_id, all_files
            )
            if source_file_id is not None:
                mappings.append(
                    {
                        "importing_file_id": star_imp.file_id,
                        "source_file_id": source_file_id,
                    }
                )
        if not mappings:
            session.execute(text("DROP TABLE IF EXISTS _star_module_map"))
            return stats
        for m in mappings:
            session.execute(
                text("INSERT INTO _star_module_map VALUES (:importing_file_id, :source_file_id)"),
                m,
            )
        # Step 4: Count eligible refs (single query)
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN _star_module_map tsm ON tsm.importing_file_id = rf.file_id
            JOIN def_facts df ON df.file_id = tsm.source_file_id
                AND df.name = rf.token_text
                AND df.lexical_path = df.name
                AND df.name NOT LIKE '\\_%' ESCAPE '\\'
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
            session.execute(text("DROP TABLE IF EXISTS _star_module_map"))
            return stats
        # Step 5: Single UPDATE with JOIN through temp table
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM _star_module_map tsm
                    JOIN def_facts df ON df.file_id = tsm.source_file_id
                        AND df.name = ref_facts.token_text
                        AND df.lexical_path = df.name
                        AND df.name NOT LIKE '\\_%' ESCAPE '\\'
                    WHERE tsm.importing_file_id = ref_facts.file_id
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN _star_module_map tsm ON tsm.importing_file_id = rf.file_id
                JOIN def_facts df ON df.file_id = tsm.source_file_id
                    AND df.name = rf.token_text
                    AND df.lexical_path = df.name
                    AND df.name NOT LIKE '\\_%' ESCAPE '\\'
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
        session.execute(text("DROP TABLE IF EXISTS _star_module_map"))
        session.commit()
    return stats


def resolve_same_namespace_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using same/parent namespace visibility (DB-backed).

    In C# (and Java/Kotlin), types in the same namespace or a parent namespace
    are visible without an explicit ``using`` directive.
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    target_unit_filter, target_unit_binds = _build_unit_filter(unit_id, "target_def")
    with db.session() as session:
        # Count refs that will be upgraded.
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN def_facts file_def ON file_def.file_id = rf.file_id
                AND file_def.namespace IS NOT NULL
            JOIN def_facts target_def ON target_def.name = rf.token_text
                AND target_def.kind IN {_TYPE_KIND_FILTER}
                AND (
                    target_def.namespace = file_def.namespace
                    OR file_def.namespace LIKE target_def.namespace || '.%'
                )
                {target_unit_filter}
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
                **target_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()
        if stats.refs_matched == 0:
            return stats
        # Perform the upgrade with target_def_uid linking.
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT target_def.def_uid
                    FROM def_facts file_def
                    JOIN def_facts target_def ON target_def.name = ref_facts.token_text
                        AND target_def.kind IN {_TYPE_KIND_FILTER}
                        AND (
                            target_def.namespace = file_def.namespace
                            OR file_def.namespace LIKE target_def.namespace || '.%'
                        )
                        {target_unit_filter}
                    WHERE file_def.file_id = ref_facts.file_id
                        AND file_def.namespace IS NOT NULL
                    ORDER BY target_def.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN def_facts file_def ON file_def.file_id = rf.file_id
                    AND file_def.namespace IS NOT NULL
                JOIN def_facts target_def ON target_def.name = rf.token_text
                    AND target_def.kind IN {_TYPE_KIND_FILTER}
                    AND (
                        target_def.namespace = file_def.namespace
                        OR file_def.namespace LIKE target_def.namespace || '.%'
                    )
                    {target_unit_filter}
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
                **target_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]
        session.commit()
    return stats


def _path_to_python_module(path: str) -> str | None:
    """Convert file path to Python module path."""
    if not path.endswith(".py"):
        return None
    module = path[:-3]
    if module.endswith("/__init__"):
        module = module[:-9]
    module = module.replace("/", ".").replace("\\", ".").lstrip(".")
    return module


def _find_python_module_file(
    source_literal: str,
    _importing_file_id: int,
    module_to_file_id: dict[str, int],
    all_files: Sequence[tuple[int | None, str]],
) -> int | None:
    """Resolve Python import source literal to a file_id."""
    # Direct match
    if source_literal in module_to_file_id:
        return module_to_file_id[source_literal]
    # Try suffix-based matching (handles src/ prefixes etc.)
    # Require a path separator (or start-of-string) before the match to avoid
    # false positives like "afoo.py" matching a search for "foo".
    parts = source_literal.replace(".", "/")
    suffix_py = f"/{parts}.py"
    suffix_init = f"/{parts}/__init__.py"
    for fid, fpath in all_files:
        if fid is None or fpath is None:
            continue
        prefixed = f"/{fpath}"
        if prefixed.endswith(suffix_py) or prefixed.endswith(suffix_init):
            return fid
    return None
