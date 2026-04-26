"""Materialize ExportSurface, ExportThunk, and AnchorGroup tables.

After all resolution passes complete, this module populates the SPEC §7.3.6-9
tables from the resolved facts.  These materialized views power:

- ``map_repo._extract_public_api()`` (ExportEntry queries)
- ``graph.get_export_surface()`` / ``list_anchor_groups()`` (FactQueries)
- ``refactor/ops.py`` confidence mapping for ANCHORED refs

The materialization is idempotent: surfaces are hash-compared and skipped
when unchanged, thunks and anchor groups are fully rebuilt per context.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from sqlalchemy import text

from coderecon.index.models import (
    ExportThunkMode,
    RefTier,
)

if TYPE_CHECKING:
    from coderecon.index._internal.db import Database


# Hard cap on exemplar ref_ids per AnchorGroup (SPEC §7.3.9).
_ANCHOR_EXEMPLAR_CAP = 10


def materialize_exports(
    db: Database,
    *,
    context_ids: list[int] | None = None,
) -> int:
    """Populate ExportSurface + ExportEntry from DefFacts and LocalBindFacts.

    Args:
        db: Database instance (post-resolution).
        context_ids: If set, only rebuild surfaces for these contexts.
            None rebuilds all.

    Returns:
        Number of surfaces written (including unchanged skips).
    """
    surfaces_written = 0

    with db.session() as session:
        # Build file_id → unit_id map from def_facts (files belong to the
        # context they were indexed under).
        file_unit_rows = session.execute(
            text("SELECT DISTINCT file_id, unit_id FROM def_facts")
        ).fetchall()
        file_to_unit: dict[int, int] = {}
        for fid, uid in file_unit_rows:
            file_to_unit[fid] = uid

        # Collect all unit_ids to process
        all_unit_ids = set(file_to_unit.values())
        if context_ids is not None:
            all_unit_ids &= set(context_ids)

        # Build per-unit export entries from def_facts (direct defs)
        # and local_bind_facts + import_facts (re-exports).
        for unit_id in sorted(all_unit_ids):
            entries: list[dict[str, str | None]] = []

            # Direct definitions: public, top-level, in files belonging to this unit
            unit_file_ids = [fid for fid, uid in file_to_unit.items() if uid == unit_id]
            if not unit_file_ids:
                continue

            ph = ", ".join(f":uf_{i}" for i in range(len(unit_file_ids)))
            binds = {f"uf_{i}": fid for i, fid in enumerate(unit_file_ids)}

            def_rows = session.execute(
                text(
                    f"SELECT name, def_uid FROM def_facts "
                    f"WHERE file_id IN ({ph}) "
                    f"AND name NOT LIKE '\\_%' ESCAPE '\\'"
                ),
                binds,
            ).fetchall()

            seen_names: set[str] = set()
            for name, def_uid in def_rows:
                if name not in seen_names:
                    entries.append({
                        "exported_name": name,
                        "def_uid": def_uid,
                        "certainty": "certain",
                        "evidence_kind": "def_in_module",
                    })
                    seen_names.add(name)

            # Re-exports: LocalBindFacts with target_kind='import' joined to
            # ImportFacts — these represent "from X import Y" exposed at module level.
            reexport_rows = session.execute(
                text(
                    f"SELECT lb.name, lb.target_uid, i.imported_name, i.source_literal "
                    f"FROM local_bind_facts lb "
                    f"JOIN import_facts i ON lb.target_uid = i.import_uid "
                    f"WHERE lb.file_id IN ({ph}) "
                    f"AND lb.target_kind = 'import' "
                    f"AND lb.name NOT LIKE '\\_%' ESCAPE '\\'"
                ),
                binds,
            ).fetchall()

            for bind_name, _target_uid, _imp_name, _src_lit in reexport_rows:
                if bind_name != "*" and bind_name not in seen_names:
                    entries.append({
                        "exported_name": bind_name,
                        "def_uid": None,
                        "certainty": "uncertain",
                        "evidence_kind": "reexport",
                    })
                    seen_names.add(bind_name)

            # Compute surface hash for change detection
            canonical = json.dumps(
                sorted(entries, key=lambda e: e["exported_name"] or ""),
                sort_keys=True,
            )
            surface_hash = hashlib.sha256(canonical.encode()).hexdigest()

            # Check existing surface
            existing = session.execute(
                text(
                    "SELECT surface_id, surface_hash FROM export_surfaces "
                    "WHERE unit_id = :uid"
                ),
                {"uid": unit_id},
            ).first()

            if existing and existing[1] == surface_hash:
                surfaces_written += 1
                continue  # unchanged

            if existing:
                surface_id = existing[0]
                # Delete old entries
                session.execute(
                    text("DELETE FROM export_entries WHERE surface_id = :sid"),
                    {"sid": surface_id},
                )
                # Update hash
                session.execute(
                    text(
                        "UPDATE export_surfaces SET surface_hash = :h WHERE surface_id = :sid"
                    ),
                    {"h": surface_hash, "sid": surface_id},
                )
            else:
                # Insert new surface
                session.execute(
                    text(
                        "INSERT INTO export_surfaces (unit_id, surface_hash) "
                        "VALUES (:uid, :h)"
                    ),
                    {"uid": unit_id, "h": surface_hash},
                )
                row = session.execute(text("SELECT last_insert_rowid()")).scalar()
                surface_id = row

            # Insert entries
            for entry in entries:
                session.execute(
                    text(
                        "INSERT INTO export_entries "
                        "(surface_id, exported_name, def_uid, certainty, evidence_kind) "
                        "VALUES (:sid, :name, :duid, :cert, :ev)"
                    ),
                    {
                        "sid": surface_id,
                        "name": entry["exported_name"],
                        "duid": entry["def_uid"],
                        "cert": entry["certainty"],
                        "ev": entry["evidence_kind"],
                    },
                )

            surfaces_written += 1

        session.commit()

    return surfaces_written


def materialize_thunks(
    db: Database,
    *,
    context_ids: list[int] | None = None,
) -> int:
    """Populate ExportThunk from re-export ImportFacts.

    Captures JS/TS barrel re-exports (``export * from``, ``export { X } from``)
    and Python re-exports (``from X import Y`` in ``__init__.py``).

    Args:
        db: Database instance.
        context_ids: If set, only rebuild thunks for these source contexts.

    Returns:
        Number of thunks written.
    """
    thunks_written = 0

    with db.session() as session:
        # file_id → unit_id map
        file_unit_rows = session.execute(
            text("SELECT DISTINCT file_id, unit_id FROM def_facts")
        ).fetchall()
        file_to_unit: dict[int, int] = {}
        for fid, uid in file_unit_rows:
            file_to_unit[fid] = uid

        # Also map files from import_facts (some files only import, no defs)
        imp_file_rows = session.execute(
            text("SELECT DISTINCT file_id, unit_id FROM import_facts")
        ).fetchall()
        for fid, uid in imp_file_rows:
            file_to_unit.setdefault(fid, uid)

        # Build file path → file_id for resolved_path lookups
        path_rows = session.execute(
            text("SELECT id, path FROM files")
        ).fetchall()
        path_to_fid: dict[str, int] = {p: fid for fid, p in path_rows if p}

        # JS/TS re-exports
        js_rows = session.execute(
            text(
                "SELECT file_id, imported_name, alias, resolved_path "
                "FROM import_facts WHERE import_kind = 'js_reexport'"
            )
        ).fetchall()

        # Group by (source_unit, target_unit)
        thunk_map: dict[tuple[int, int], list[tuple[str, str | None]]] = {}
        for fid, imp_name, alias, resolved_path in js_rows:
            source_unit = file_to_unit.get(fid)
            if source_unit is None:
                continue
            target_fid = path_to_fid.get(resolved_path or "")
            if target_fid is None:
                continue
            target_unit = file_to_unit.get(target_fid)
            if target_unit is None or target_unit == source_unit:
                continue
            key = (source_unit, target_unit)
            thunk_map.setdefault(key, []).append((imp_name or "*", alias))

        # Python re-exports: LocalBindFact(target_kind=import) + ImportFact
        py_rows = session.execute(
            text(
                "SELECT lb.file_id, lb.name, i.imported_name, i.resolved_path "
                "FROM local_bind_facts lb "
                "JOIN import_facts i ON lb.target_uid = i.import_uid "
                "WHERE lb.target_kind = 'import'"
            )
        ).fetchall()

        for fid, bind_name, imp_name, resolved_path in py_rows:
            source_unit = file_to_unit.get(fid)
            if source_unit is None:
                continue
            target_fid = path_to_fid.get(resolved_path or "")
            if target_fid is None:
                continue
            target_unit = file_to_unit.get(target_fid)
            if target_unit is None or target_unit == source_unit:
                continue
            key = (source_unit, target_unit)
            thunk_map.setdefault(key, []).append((bind_name, None))

        # Filter to requested contexts
        if context_ids is not None:
            ctx_set = set(context_ids)
            thunk_map = {k: v for k, v in thunk_map.items() if k[0] in ctx_set}

        # Delete old thunks for affected source units
        affected_sources = {k[0] for k in thunk_map}
        for src_unit in affected_sources:
            session.execute(
                text("DELETE FROM export_thunks WHERE source_unit = :su"),
                {"su": src_unit},
            )

        # Insert new thunks
        for (source_unit, target_unit), name_pairs in thunk_map.items():
            has_wildcard = any(n == "*" and a is None for n, a in name_pairs)
            explicit = [(n, a) for n, a in name_pairs if n != "*"]
            has_aliases = any(a is not None and a != n for n, a in explicit)

            if has_wildcard and not explicit:
                mode = ExportThunkMode.REEXPORT_ALL.value
                session.execute(
                    text(
                        "INSERT INTO export_thunks "
                        "(source_unit, target_unit, mode) "
                        "VALUES (:su, :tu, :m)"
                    ),
                    {"su": source_unit, "tu": target_unit, "m": mode},
                )
            elif has_aliases:
                mode = ExportThunkMode.ALIAS_MAP.value
                alias_map = {n: (a or n) for n, a in explicit}
                session.execute(
                    text(
                        "INSERT INTO export_thunks "
                        "(source_unit, target_unit, mode, alias_map) "
                        "VALUES (:su, :tu, :m, :am)"
                    ),
                    {
                        "su": source_unit,
                        "tu": target_unit,
                        "m": mode,
                        "am": json.dumps(alias_map),
                    },
                )
            else:
                mode = ExportThunkMode.EXPLICIT_NAMES.value
                names = sorted({n for n, _a in explicit})
                session.execute(
                    text(
                        "INSERT INTO export_thunks "
                        "(source_unit, target_unit, mode, explicit_names) "
                        "VALUES (:su, :tu, :m, :en)"
                    ),
                    {
                        "su": source_unit,
                        "tu": target_unit,
                        "m": mode,
                        "en": json.dumps(names),
                    },
                )
            thunks_written += 1

        session.commit()

    return thunks_written


def materialize_anchor_groups(
    db: Database,
    *,
    context_ids: list[int] | None = None,
) -> int:
    """Populate AnchorGroup from unresolved UNKNOWN-tier refs.

    Groups remaining UNKNOWN refs by ``(unit_id, token_text, receiver_shape)``
    and upgrades them to ANCHORED tier.

    Args:
        db: Database instance (post-resolution).
        context_ids: If set, only rebuild groups for these contexts.

    Returns:
        Number of anchor groups written.
    """
    groups_written = 0

    with db.session() as session:
        # Build context filter
        ctx_filter = ""
        ctx_binds: dict[str, int] = {}
        if context_ids:
            ph = ", ".join(f":ctx_{i}" for i in range(len(context_ids)))
            ctx_binds = {f"ctx_{i}": cid for i, cid in enumerate(context_ids)}
            ctx_filter = f"AND r.unit_id IN ({ph})"

        # Delete old anchor groups for affected contexts
        if context_ids:
            dph = ", ".join(f":dctx_{i}" for i in range(len(context_ids)))
            dbinds = {f"dctx_{i}": cid for i, cid in enumerate(context_ids)}
            session.execute(
                text(f"DELETE FROM anchor_groups WHERE unit_id IN ({dph})"),
                dbinds,
            )
        else:
            session.execute(text("DELETE FROM anchor_groups"))

        # Also reset any previously-ANCHORED refs back to UNKNOWN in the
        # affected contexts so they get re-grouped.
        session.execute(
            text(
                f"UPDATE ref_facts SET ref_tier = :unknown "
                f"WHERE ref_tier = :anchored "
                f"{ctx_filter.replace('r.unit_id', 'unit_id')}"
            ),
            {"unknown": RefTier.UNKNOWN.value, "anchored": RefTier.ANCHORED.value, **ctx_binds},
        )

        # Find unresolved UNKNOWN refs, with optional receiver_name from
        # member_access_facts (joined on file_id + start_line + start_col).
        rows = session.execute(
            text(
                f"SELECT r.ref_id, r.unit_id, r.token_text, r.file_id, "
                f"  r.start_line, r.start_col, m.receiver_name "
                f"FROM ref_facts r "
                f"LEFT JOIN member_access_facts m ON "
                f"  r.file_id = m.file_id AND r.start_line = m.start_line "
                f"  AND r.start_col = m.start_col "
                f"WHERE r.ref_tier = :unknown AND r.target_def_uid IS NULL "
                f"{ctx_filter}"
            ),
            {"unknown": RefTier.UNKNOWN.value, **ctx_binds},
        ).fetchall()

        # Group by (unit_id, token_text, receiver_shape)
        GroupKey = tuple[int, str, str | None]
        groups: dict[GroupKey, list[tuple[int, int, int, int]]] = {}
        for ref_id, unit_id, token_text, file_id, start_line, start_col, receiver_name in rows:
            key: GroupKey = (unit_id, token_text, receiver_name)
            groups.setdefault(key, []).append((ref_id, file_id, start_line, start_col))

        # Insert anchor groups and upgrade refs
        ref_ids_to_anchor: list[int] = []
        for (unit_id, member_token, receiver_shape), members in groups.items():
            # Sort deterministically: (file_id, start_line, start_col)
            members.sort(key=lambda m: (m[1], m[2], m[3]))
            exemplar_ids = [m[0] for m in members[:_ANCHOR_EXEMPLAR_CAP]]

            session.execute(
                text(
                    "INSERT INTO anchor_groups "
                    "(unit_id, member_token, receiver_shape, total_count, exemplar_ids) "
                    "VALUES (:uid, :mt, :rs, :tc, :ex)"
                ),
                {
                    "uid": unit_id,
                    "mt": member_token,
                    "rs": receiver_shape,
                    "tc": len(members),
                    "ex": json.dumps(exemplar_ids),
                },
            )
            ref_ids_to_anchor.extend(m[0] for m in members)
            groups_written += 1

        # Upgrade grouped refs to ANCHORED tier
        if ref_ids_to_anchor:
            # Batch in chunks to stay within SQLite variable limits
            _CHUNK = 500
            for i in range(0, len(ref_ids_to_anchor), _CHUNK):
                chunk = ref_ids_to_anchor[i : i + _CHUNK]
                cph = ", ".join(f":rid_{j}" for j in range(len(chunk)))
                cbinds = {f"rid_{j}": rid for j, rid in enumerate(chunk)}
                session.execute(
                    text(
                        f"UPDATE ref_facts SET ref_tier = :anchored "
                        f"WHERE ref_id IN ({cph})"
                    ),
                    {"anchored": RefTier.ANCHORED.value, **cbinds},
                )

        session.commit()

    return groups_written


def materialize_all(
    db: Database,
    *,
    context_ids: list[int] | None = None,
) -> dict[str, int]:
    """Run all materialization steps.

    Args:
        db: Database instance (post-resolution).
        context_ids: If set, only rebuild for these contexts.

    Returns:
        Dict with counts: surfaces, thunks, anchor_groups.
    """
    return {
        "surfaces": materialize_exports(db, context_ids=context_ids),
        "thunks": materialize_thunks(db, context_ids=context_ids),
        "anchor_groups": materialize_anchor_groups(db, context_ids=context_ids),
    }
