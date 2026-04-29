"""Cross-file reference resolution for the structural index.

This module implements "Pass 2" of indexing - resolving cross-file references
by following ImportFact chains to find the actual target definitions.

The structural indexer (Pass 1) creates RefFacts with:
- PROVEN tier + target_def_uid for same-file references
- STRONG tier + target_def_uid=None for import-based references

This module resolves the STRONG refs by:
1. Finding the ImportFact that introduced the name
2. Looking up the source module's DefFact
3. Updating the RefFact's target_def_uid

Per SPEC.md §7.9, this is a best-effort heuristic resolution.
Certainty is marked appropriately when resolution is ambiguous.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

from coderecon.index.resolution import resolver_cache as _cache
from coderecon.index.models import (
    BindTargetKind,
    Certainty,
    RefTier,
)

if TYPE_CHECKING:
    from coderecon.index.db import Database

@dataclass
class ResolutionStats:
    """Statistics from reference resolution."""
    refs_processed: int = 0
    refs_resolved: int = 0
    refs_unresolved: int = 0
    refs_ambiguous: int = 0
class ReferenceResolver:
    """Resolves cross-file references by following import chains.
    This implements the "STRONG" tier resolution per SPEC.md §7.3.2:
    - STRONG refs have an ImportFact trace but need def_uid lookup
    - Resolution follows: RefFact -> LocalBindFact -> ImportFact -> DefFact
    All lookups are done in-memory from pre-built caches for speed.
    The resolution loop runs in parallel across multiple threads.
    Usage after structural indexing::
        resolver = ReferenceResolver(db)
        stats = resolver.resolve_all()
    """
    def __init__(self, db: Database, worktree_id: int | None = None) -> None:
        self._db = db
        self._worktree_id = worktree_id
        # Cache module path -> file_id mapping
        self._module_to_file: dict[str, int] = {}
        # Cache file_id -> file path
        self._file_paths: dict[int, str] = {}
        # Cache file_id -> exported symbols
        self._file_exports: dict[int, dict[str, str]] = {}  # name -> def_uid
        # Cache (file_id, name) -> list of binds with scope context.
        # Each entry: (scope_id, target_kind, target_uid).
        # Sorted innermost-first by scope_id descending so the first match
        # at or above the ref's scope is the correct one.
        self._bind_cache: dict[tuple[int, str], list[tuple[int, str, str]]] = {}
        # Cache file_id -> list of import_uids for wildcard imports (from X import *)
        self._wildcard_imports: dict[int, list[str]] = {}
        # Cache import_uid -> ImportFact fields
        self._import_cache: dict[str, tuple[int, str, str, str]] = {}  # -> (file_id, source_literal, imported_name, resolved_path)
        # Scope parent chain: scope_id -> parent_scope_id (0 = module-level)
        self._path_to_file: dict[str, int] = {}
        self._scope_parents: dict[int, int] = {}
    def resolve_all(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ResolutionStats:
        """Resolve all unresolved STRONG-tier references.
        Pre-builds all caches, resolves refs in parallel threads,
        then batch-commits results via raw SQL.
        Args:
            on_progress: Optional callback(processed, total) for progress updates
        Returns:
            ResolutionStats with counts
        """
        stats = ResolutionStats()
        with self._db.session() as session:
            # Load unresolved refs as lightweight tuples (avoid ORM overhead)
            rows = session.execute(
                text(
                    "SELECT ref_id, file_id, token_text, scope_id "
                    "FROM ref_facts "
                    "WHERE ref_tier = :tier AND target_def_uid IS NULL"
                ),
                {"tier": RefTier.STRONG.value},
            ).fetchall()
            stats.refs_processed = len(rows)
            total = len(rows)
            if not rows:
                return stats
            # Build all caches
            self._build_scope_parents(session)
            self._build_module_cache(session)
            self._build_export_cache(session)
            self._build_bind_cache(session)
            self._build_import_cache(session)
        # Resolve in-memory — all lookups are dict operations, no DB needed
        resolved_updates: list[tuple[str, str, int]] = []  # (def_uid, certainty, ref_id)
        for i, (ref_id, file_id, token_text, scope_id) in enumerate(rows):
            result = self._resolve_ref_inmem(file_id, token_text, scope_id)
            if result is not None:
                def_uid, certainty = result
                resolved_updates.append((def_uid, certainty, ref_id))
                stats.refs_resolved += 1
            else:
                stats.refs_unresolved += 1
            if on_progress and (i + 1) % 50 == 0:
                on_progress(i + 1, total)
        # Batch-commit resolved refs via raw SQL
        if resolved_updates:
            with self._db.session() as session:
                # Use executemany for efficient batch updates
                session.execute(
                    text(
                        "UPDATE ref_facts "
                        "SET target_def_uid = :def_uid, certainty = :certainty "
                        "WHERE ref_id = :ref_id"
                    ),
                    [
                        {"def_uid": uid, "certainty": cert, "ref_id": rid}
                        for uid, cert, rid in resolved_updates
                    ],
                )
                session.commit()
        # Second pass: resolve UNKNOWN refs in files with wildcard imports.
        # Wildcard imports (from X import *) don't create per-name binds,
        # so refs stay at UNKNOWN tier.  We only scan files that actually
        # have wildcard imports to avoid processing the full UNKNOWN set.
        if self._wildcard_imports:
            wc_fids = list(self._wildcard_imports.keys())
            with self._db.session() as session:
                placeholders = ", ".join(f":wf_{i}" for i in range(len(wc_fids)))
                binds = {f"wf_{i}": fid for i, fid in enumerate(wc_fids)}
                wc_rows = session.execute(
                    text(
                        f"SELECT ref_id, file_id, token_text "
                        f"FROM ref_facts "
                        f"WHERE ref_tier = :tier AND target_def_uid IS NULL "
                        f"AND file_id IN ({placeholders})"
                    ),
                    {"tier": RefTier.UNKNOWN.value, **binds},
                ).fetchall()
            wc_updates: list[tuple[str, str, int]] = []
            for ref_id, file_id, token_text in wc_rows:
                for imp_uid in self._wildcard_imports.get(file_id, ()):
                    result = self._resolve_wildcard_inmem(imp_uid, token_text)
                    if result is not None:
                        wc_updates.append((result[0], result[1], ref_id))
                        stats.refs_resolved += 1
                        break
            if wc_updates:
                with self._db.session() as session:
                    session.execute(
                        text(
                            "UPDATE ref_facts "
                            "SET target_def_uid = :def_uid, certainty = :certainty "
                            "WHERE ref_id = :ref_id"
                        ),
                        [
                            {"def_uid": uid, "certainty": cert, "ref_id": rid}
                            for uid, cert, rid in wc_updates
                        ],
                    )
                    session.commit()
        if on_progress and total > 0:
            on_progress(total, total)
        return stats
    def _resolve_ref_inmem(
        self, file_id: int, token_text: str, scope_id: int | None = None,
    ) -> tuple[str, str] | None:
        """Resolve a single ref using only in-memory caches.
        When *scope_id* is provided, walks the scope chain from innermost
        to outermost (module-level = 0) to find the closest matching bind.
        Falls back to any bind in the file when scope walk finds nothing.
        Returns (target_def_uid, certainty) or None.
        """
        binds = self._bind_cache.get((file_id, token_text))
        if binds is not None:
            # Scope-aware lookup: walk from the ref's scope outward
            match = self._find_bind_in_scope(binds, scope_id)
            if match is not None:
                target_kind, target_uid = match
                if target_kind == BindTargetKind.DEF.value:
                    return (target_uid, Certainty.CERTAIN.value)
                if target_kind == BindTargetKind.IMPORT.value:
                    return self._resolve_import_inmem(file_id, target_uid)
        # Fallback: check wildcard imports (from X import *, import * from)
        # The bind for "*" can't match individual names, so we try each
        # wildcard source module's exports for this token.
        for imp_uid in self._wildcard_imports.get(file_id, ()):
            result = self._resolve_wildcard_inmem(imp_uid, token_text)
            if result is not None:
                return result
        return None
    def _find_bind_in_scope(
        self,
        binds: list[tuple[int, str, str]],
        scope_id: int | None,
    ) -> tuple[str, str] | None:
        """Walk from *scope_id* outward to find the innermost matching bind.
        Each bind is ``(bind_scope_id, target_kind, target_uid)``.
        Returns ``(target_kind, target_uid)`` or None.
        """
        if len(binds) == 1:
            # Fast path: only one bind, no need to walk scopes.
            return (binds[0][1], binds[0][2])
        if scope_id is None:
            # No scope info on the ref — prefer module-level (0) bind,
            # then fall back to the first bind.
            for bid, tk, tu in binds:
                if bid == 0:
                    return (tk, tu)
            return (binds[0][1], binds[0][2])
        # Build set of scope ids from ref scope to module-level
        chain: list[int] = []
        current = scope_id
        _visited: set[int] = set()
        while current and current not in _visited:
            chain.append(current)
            _visited.add(current)
            current = self._scope_parents.get(current, 0)
        chain.append(0)  # module-level sentinel
        # Index binds by scope_id for O(1) lookup per chain step
        bind_by_scope: dict[int, tuple[str, str]] = {}
        for bid, tk, tu in binds:
            bind_by_scope.setdefault(bid, (tk, tu))
        for sid in chain:
            if sid in bind_by_scope:
                return bind_by_scope[sid]
        # No match on the chain — fall back to first bind
        return (binds[0][1], binds[0][2])
    def _resolve_wildcard_inmem(
        self, import_uid: str, token_text: str
    ) -> tuple[str, str] | None:
        """Resolve a name via wildcard import (from X import *)."""
        imp = self._import_cache.get(import_uid)
        if imp is None:
            return None
        imp_file_id, source_literal, _imported_name, resolved_path = imp
        if not source_literal:
            return None
        target_file_id = (
            self._path_to_file.get(resolved_path) if resolved_path else None
        )
        if target_file_id is None:
            target_file_id = self._find_module_file(
                source_literal, importing_file_id=imp_file_id
            )
        if target_file_id is None:
            return None
        exports = self._file_exports.get(target_file_id, {})
        if token_text in exports:
            return (exports[token_text], Certainty.UNCERTAIN.value)
        return None
    def _resolve_import_inmem(
        self, importing_file_id: int, import_uid: str
    ) -> tuple[str, str] | None:
        """Resolve a ref via import chain, in-memory only."""
        imp = self._import_cache.get(import_uid)
        if imp is None:
            return None
        imp_file_id, source_literal, imported_name, resolved_path = imp
        if not source_literal:
            return None
        # Prefer resolved_path from ImportPathResolver (handles all
        # language-specific resolution: extension remapping, tsconfig paths,
        # go.mod, cargo aliases, etc.)
        target_file_id = (
            self._path_to_file.get(resolved_path) if resolved_path else None
        )
        if target_file_id is None:
            target_file_id = self._find_module_file(
                source_literal, importing_file_id=imp_file_id
            )
        if target_file_id is None:
            return None
        exports = self._file_exports.get(target_file_id, {})
        if imported_name in exports:
            return (exports[imported_name], Certainty.CERTAIN.value)
        return None
    def resolve_for_files(
        self,
        file_ids: list[int],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ResolutionStats:
        """Resolve references only for specific files.
        Use this for incremental updates after re-indexing specific files.
        Args:
            file_ids: List of file IDs to resolve
            on_progress: Optional callback(processed, total) for progress updates
        """
        stats = ResolutionStats()
        with self._db.session() as session:
            # Build file_id IN clause
            placeholders = ", ".join(f":fid_{i}" for i in range(len(file_ids)))
            binds = {f"fid_{i}": fid for i, fid in enumerate(file_ids)}
            rows = session.execute(
                text(
                    f"SELECT ref_id, file_id, token_text, scope_id "
                    f"FROM ref_facts "
                    f"WHERE ref_tier = :tier AND target_def_uid IS NULL "
                    f"AND file_id IN ({placeholders})"
                ),
                {"tier": RefTier.STRONG.value, **binds},
            ).fetchall()
            stats.refs_processed = len(rows)
            total = len(rows)
            if not rows:
                return stats
            self._build_scope_parents(session)
            self._build_module_cache(session)
            self._build_export_cache(session)
            self._build_bind_cache(session, file_ids=file_ids)
            self._build_import_cache(session)
        # Resolve in-memory
        resolved_updates: list[tuple[str, str, int]] = []
        for i, (ref_id, file_id, token_text, scope_id) in enumerate(rows):
            result = self._resolve_ref_inmem(file_id, token_text, scope_id)
            if result is not None:
                def_uid, certainty = result
                resolved_updates.append((def_uid, certainty, ref_id))
                stats.refs_resolved += 1
            else:
                stats.refs_unresolved += 1
            if on_progress and (i + 1) % 50 == 0:
                on_progress(i + 1, total)
        if resolved_updates:
            with self._db.session() as session:
                session.execute(
                    text(
                        "UPDATE ref_facts "
                        "SET target_def_uid = :def_uid, certainty = :certainty "
                        "WHERE ref_id = :ref_id"
                    ),
                    [
                        {"def_uid": uid, "certainty": cert, "ref_id": rid}
                        for uid, cert, rid in resolved_updates
                    ],
                )
                session.commit()
        # Second pass: wildcard imports in the target files
        wc_fids = [fid for fid in file_ids if fid in self._wildcard_imports]
        if wc_fids:
            with self._db.session() as session:
                ph = ", ".join(f":wf_{i}" for i in range(len(wc_fids)))
                wc_binds = {f"wf_{i}": fid for i, fid in enumerate(wc_fids)}
                wc_rows = session.execute(
                    text(
                        f"SELECT ref_id, file_id, token_text "
                        f"FROM ref_facts "
                        f"WHERE ref_tier = :tier AND target_def_uid IS NULL "
                        f"AND file_id IN ({ph})"
                    ),
                    {"tier": RefTier.UNKNOWN.value, **wc_binds},
                ).fetchall()
            wc_updates: list[tuple[str, str, int]] = []
            for ref_id, file_id, token_text in wc_rows:
                for imp_uid in self._wildcard_imports.get(file_id, ()):
                    result = self._resolve_wildcard_inmem(imp_uid, token_text)
                    if result is not None:
                        wc_updates.append((result[0], result[1], ref_id))
                        stats.refs_resolved += 1
                        break
            if wc_updates:
                with self._db.session() as session:
                    session.execute(
                        text(
                            "UPDATE ref_facts "
                            "SET target_def_uid = :def_uid, certainty = :certainty "
                            "WHERE ref_id = :ref_id"
                        ),
                        [
                            {"def_uid": uid, "certainty": cert, "ref_id": rid}
                            for uid, cert, rid in wc_updates
                        ],
                    )
                    session.commit()
        if on_progress and total > 0:
            on_progress(total, total)
        return stats

    def _build_scope_parents(self, session: object) -> None:
        _cache.build_scope_parents(self, session)

    def _build_bind_cache(
        self, session: object, *, file_ids: list[int] | None = None,
    ) -> None:
        _cache.build_bind_cache(self, session, file_ids=file_ids)

    def _build_import_cache(self, session: object) -> None:
        _cache.build_import_cache(self, session)

    def _build_module_cache(self, session: object) -> None:
        _cache.build_module_cache(self, session)

    def _build_export_cache(self, session: object) -> None:
        _cache.build_export_cache(self, session)

    def _find_module_file(
        self,
        source_literal: str,
        importing_file_id: int | None = None,
    ) -> int | None:
        return _cache.find_module_file(self, source_literal, importing_file_id)

    def _resolve_relative_source(
        self,
        source_literal: str,
        importing_file_id: int,
    ) -> int | None:
        return _cache._resolve_relative_source(self, source_literal, importing_file_id)

    def _resolve_python_relative(self, source_literal: str, importing_path: str) -> int | None:
        return _cache._resolve_python_relative(self, source_literal, importing_path)

    def _resolve_js_relative(self, source_literal: str, importing_path: str) -> int | None:
        return _cache._resolve_js_relative(self, source_literal, importing_path)

    def _resolve_rust_relative(self, source_literal: str, importing_path: str) -> int | None:
        return _cache._resolve_rust_relative(self, source_literal, importing_path)

    def _path_to_module(self, path: str) -> str | None:
        return _cache.path_to_module(path)

def resolve_references(
    db: Database,
    file_ids: list[int] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    worktree_id: int | None = None,
) -> ResolutionStats:
    """Convenience function to resolve cross-file references.
    Args:
        db: Database instance
        file_ids: Optional list of file IDs to resolve (None = all)
        on_progress: Optional callback(processed, total) for progress updates
        worktree_id: Optional worktree_id to scope resolver caches
    Returns:
        ResolutionStats
    """
    resolver = ReferenceResolver(db, worktree_id=worktree_id)
    if file_ids:
        return resolver.resolve_for_files(file_ids, on_progress)
    return resolver.resolve_all(on_progress)

# Pass 1.5: DB-backed cross-file resolution
#


# Re-exports for backward compatibility — these were moved to separate modules
# but existing code imports them from here.
from coderecon.index.resolution.crossfile import (  # noqa: E402, F401
    _TYPE_KIND_FILTER,
    _TYPE_KINDS,
    CrossFileResolutionStats,
    ResolutionPassFn,
    _build_file_filter,
    _build_unit_filter,
    _find_python_module_file,
    _path_to_python_module,
    resolve_namespace_refs,
    resolve_same_namespace_refs,
    resolve_star_import_refs,
)
from coderecon.index.resolution.crossfile_lang import (  # noqa: E402, F401
    _find_go_package_file,
    _find_rust_module_file,
    _path_to_rust_module,
    _register_resolution_passes,
    resolve_go_dot_import_refs,
    resolve_java_star_import_refs,
    resolve_rust_glob_import_refs,
    run_pass_1_5,
)
