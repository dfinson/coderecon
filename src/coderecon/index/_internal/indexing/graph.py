"""Bounded fact query operations for Tier 1 index.

This module provides BOUNDED queries over fact tables. All queries require limits.
No semantic resolution, no call graph, no transitive closure.

See SPEC.md §7.8 for the bounded query API contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import col, select

from coderecon.index.models import (
    AnchorGroup,
    DefFact,
    DocCrossRef,
    EndpointFact,
    ExportEntry,
    ExportSurface,
    File,
    ImportFact,
    InterfaceImplFact,
    LocalBindFact,
    RefFact,
    RefTier,
    ScopeFact,
    TestCoverageFact,
)

if TYPE_CHECKING:
    from sqlmodel import Session


class FactQueries:
    """Bounded fact queries for the Tier 1 index.

    All queries require explicit limits. No unbounded returns.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # -------------------------------------------------------------------------
    # Definition lookups
    # -------------------------------------------------------------------------

    def get_def(self, def_uid: str) -> DefFact | None:
        """Get a definition by its stable UID."""
        return self._session.get(DefFact, def_uid)

    def batch_get_defs(self, def_uids: list[str]) -> dict[str, DefFact]:
        """Get multiple definitions by UID in a single query.

        Returns a dict mapping def_uid → DefFact for found UIDs.
        Missing UIDs are silently omitted.
        """
        if not def_uids:
            return {}
        stmt = select(DefFact).where(col(DefFact.def_uid).in_(def_uids))
        results = list(self._session.exec(stmt).all())
        return {d.def_uid: d for d in results}

    def list_defs_by_name(self, unit_id: int, name: str, *, limit: int = 100) -> list[DefFact]:
        """List definitions by simple name within a build unit."""
        stmt = select(DefFact).where(DefFact.unit_id == unit_id, DefFact.name == name).limit(limit)
        return list(self._session.exec(stmt).all())

    def list_defs_in_file(self, file_id: int, *, limit: int = 1000) -> list[DefFact]:
        """List all definitions in a file, ordered by source position."""
        stmt = (
            select(DefFact)
            .where(DefFact.file_id == file_id)
            .order_by(col(DefFact.start_line), col(DefFact.def_uid))
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Reference lookups
    # -------------------------------------------------------------------------

    def list_refs_by_def_uid(
        self,
        def_uid: str,
        *,
        tier: RefTier | None = None,
        limit: int = 250,
        offset: int = 0,
    ) -> list[RefFact]:
        """List references to a definition with pagination.

        Args:
            def_uid: Stable definition UID.
            tier: Optional tier filter.
            limit: Maximum results per page (default 250).
            offset: Number of rows to skip for pagination.

        Returns:
            List of RefFact objects for the requested page.
        """
        stmt = select(RefFact).where(RefFact.target_def_uid == def_uid)
        if tier is not None:
            stmt = stmt.where(RefFact.ref_tier == tier.value)
        stmt = stmt.order_by(col(RefFact.ref_id)).offset(offset).limit(limit)
        return list(self._session.exec(stmt).all())

    def list_all_refs_by_def_uid(
        self,
        def_uid: str,
        *,
        tier: RefTier | None = None,
        page_size: int = 250,
    ) -> list[RefFact]:
        """Exhaustively list ALL references to a definition.

        Paginates internally to avoid unbounded single queries while
        guaranteeing completeness.  Use this for mutation operations
        (rename, delete) that **must** see every reference.

        Args:
            def_uid: Stable definition UID.
            tier: Optional tier filter.
            page_size: Internal page size (default 250).

        Returns:
            Complete list of RefFact objects.
        """
        all_refs: list[RefFact] = []
        offset = 0
        while True:
            page = self.list_refs_by_def_uid(def_uid, tier=tier, limit=page_size, offset=offset)
            all_refs.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return all_refs

    def list_proven_refs(self, def_uid: str, *, limit: int = 100) -> list[RefFact]:
        """List PROVEN references to a definition (convenience)."""
        return self.list_refs_by_def_uid(def_uid, tier=RefTier.PROVEN, limit=limit)

    def list_refs_in_file(self, file_id: int, *, limit: int = 1000) -> list[RefFact]:
        """List all references in a file."""
        stmt = select(RefFact).where(RefFact.file_id == file_id).limit(limit)
        return list(self._session.exec(stmt).all())

    def list_callees_in_scope(
        self,
        file_id: int,
        start_line: int,
        end_line: int,
        *,
        limit: int = 100,
    ) -> list[DefFact]:
        """List definitions referenced (called/used) within a line range.

        Joins ref_facts → def_facts for refs whose scope falls within
        the given line range. This answers "what does this function call?"

        Args:
            file_id: File containing the scope.
            start_line: Start line of the scope (inclusive).
            end_line: End line of the scope (inclusive).
            limit: Maximum results.

        Returns:
            Deduplicated list of DefFact objects referenced in the scope.
        """
        stmt = (
            select(DefFact)
            .join(RefFact, onclause=col(RefFact.target_def_uid) == col(DefFact.def_uid))
            .where(
                RefFact.file_id == file_id,
                RefFact.start_line >= start_line,
                RefFact.start_line <= end_line,
                RefFact.target_def_uid.is_not(None),  # type: ignore[union-attr]
            )
            .distinct()
            .order_by(DefFact.def_uid)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    def count_callers(self, def_uid: str) -> int:
        """Count distinct files that reference a definition (hub inbound score)."""
        from sqlalchemy import func

        stmt = select(func.count(func.distinct(RefFact.file_id))).where(
            RefFact.target_def_uid == def_uid
        )
        result = self._session.exec(stmt).one()
        return int(result) if result else 0

    def batch_count_callers(self, def_uids: list[str]) -> dict[str, int]:
        """Count distinct caller files for multiple defs in a single query.

        Returns a dict mapping def_uid → caller count. UIDs with zero
        callers are included with count 0.
        """
        if not def_uids:
            return {}
        from sqlalchemy import func

        stmt = (
            select(
                RefFact.target_def_uid,
                func.count(func.distinct(RefFact.file_id)),
            )
            .where(col(RefFact.target_def_uid).in_(def_uids))
            .group_by(RefFact.target_def_uid)
        )
        rows = list(self._session.exec(stmt).all())
        result = {uid: 0 for uid in def_uids}
        for uid, count in rows:
            result[uid] = int(count)
        return result

    def list_refs_by_token(
        self, unit_id: int, token_text: str, *, limit: int = 100
    ) -> list[RefFact]:
        """List references by token text within a build unit."""
        stmt = (
            select(RefFact)
            .where(RefFact.unit_id == unit_id, RefFact.token_text == token_text)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Scope lookups
    # -------------------------------------------------------------------------

    def get_scope(self, scope_id: int) -> ScopeFact | None:
        """Get a scope by ID."""
        return self._session.get(ScopeFact, scope_id)

    def list_scopes_in_file(self, file_id: int) -> list[ScopeFact]:
        """List all scopes in a file (typically bounded by file size)."""
        stmt = select(ScopeFact).where(ScopeFact.file_id == file_id)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Binding lookups
    # -------------------------------------------------------------------------

    def get_local_bind(self, scope_id: int, name: str) -> LocalBindFact | None:
        """Get a local binding by scope and name."""
        stmt = select(LocalBindFact).where(
            LocalBindFact.scope_id == scope_id, LocalBindFact.name == name
        )
        return self._session.exec(stmt).first()

    def list_binds_in_scope(self, scope_id: int, *, limit: int = 100) -> list[LocalBindFact]:
        """List all bindings in a scope."""
        stmt = select(LocalBindFact).where(LocalBindFact.scope_id == scope_id).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Import lookups
    # -------------------------------------------------------------------------

    def list_imports(self, file_id: int, *, limit: int = 100) -> list[ImportFact]:
        """List all imports in a file, ordered by source position."""
        stmt = (
            select(ImportFact)
            .where(ImportFact.file_id == file_id)
            .order_by(col(ImportFact.start_line), col(ImportFact.import_uid))
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    def get_import(self, import_uid: str) -> ImportFact | None:
        """Get an import by its UID."""
        return self._session.get(ImportFact, import_uid)

    # -------------------------------------------------------------------------
    # Export lookups
    # -------------------------------------------------------------------------

    def get_export_surface(self, unit_id: int) -> ExportSurface | None:
        """Get the export surface for a build unit."""
        stmt = select(ExportSurface).where(ExportSurface.unit_id == unit_id)
        return self._session.exec(stmt).first()

    def list_export_entries(self, surface_id: int, *, limit: int = 1000) -> list[ExportEntry]:
        """List export entries for a surface."""
        stmt = select(ExportEntry).where(ExportEntry.surface_id == surface_id).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Anchor group lookups
    # -------------------------------------------------------------------------

    def get_anchor_group(
        self, unit_id: int, member_token: str, receiver_shape: str | None
    ) -> AnchorGroup | None:
        """Get an anchor group by token and receiver shape."""
        stmt = select(AnchorGroup).where(
            AnchorGroup.unit_id == unit_id,
            AnchorGroup.member_token == member_token,
        )
        if receiver_shape is not None:
            stmt = stmt.where(AnchorGroup.receiver_shape == receiver_shape)
        else:
            stmt = stmt.where(AnchorGroup.receiver_shape.is_(None))  # type: ignore[union-attr]
        return self._session.exec(stmt).first()

    def list_anchor_groups(self, unit_id: int, *, limit: int = 100) -> list[AnchorGroup]:
        """List anchor groups in a build unit."""
        stmt = select(AnchorGroup).where(AnchorGroup.unit_id == unit_id).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # File lookups
    # -------------------------------------------------------------------------

    def get_file(self, file_id: int) -> File | None:
        """Get a file by ID."""
        return self._session.get(File, file_id)

    def batch_get_files(self, file_ids: list[int]) -> dict[int, File]:
        """Get multiple files by ID in a single query.

        Returns a dict mapping file_id → File for found IDs.
        """
        if not file_ids:
            return {}
        stmt = select(File).where(col(File.id).in_(file_ids))
        results = list(self._session.exec(stmt).all())
        return {f.id: f for f in results if f.id is not None}

    def get_file_by_path(self, path: str) -> File | None:
        """Get a file by path."""
        stmt = select(File).where(File.path == path)
        return self._session.exec(stmt).first()

    def batch_get_files_by_paths(self, paths: list[str]) -> dict[str, File]:
        """Get multiple files by path in a single query.

        Returns a dict mapping path → File for found paths.
        """
        if not paths:
            return {}
        stmt = select(File).where(col(File.path).in_(paths))
        results = list(self._session.exec(stmt).all())
        return {f.path: f for f in results}

    def list_files(self, *, limit: int = 10000) -> list[File]:
        """List all indexed files."""
        stmt = select(File).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Seed finding (recon-dedicated)
    # -------------------------------------------------------------------------

    def find_defs_matching_term(
        self,
        term: str,
        *,
        limit: int = 200,
    ) -> list[DefFact]:
        """Find definitions whose name, qualified_name, or docstring contain a term.

        Uses SQL LIKE for case-insensitive substring matching directly on
        the structural index — no BM25, no Tantivy.

        Results are ordered by ``def_uid`` for deterministic output across
        index rebuilds.

        Args:
            term: Lowercase search term (minimum 2 chars).
            limit: Maximum results.

        Returns:
            List of DefFact objects matching the term.
        """
        if len(term) < 2:
            return []
        pattern = f"%{term}%"
        stmt = (
            select(DefFact)
            .where(
                (col(DefFact.name).ilike(pattern))
                | (col(DefFact.qualified_name).ilike(pattern))
                | (col(DefFact.docstring).ilike(pattern))
                | (col(DefFact.lexical_path).ilike(pattern))
            )
            .order_by(DefFact.def_uid)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    def find_files_matching_term(
        self,
        term: str,
        *,
        limit: int = 100,
    ) -> list[File]:
        """Find files whose path contains a term.

        Args:
            term: Lowercase search term (minimum 2 chars).
            limit: Maximum results.

        Returns:
            List of File objects whose path matches.
        """
        if len(term) < 2:
            return []
        pattern = f"%{term}%"
        stmt = select(File).where(col(File.path).ilike(pattern)).order_by(File.path).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Interface / type hierarchy lookups (Tier 2)
    # -------------------------------------------------------------------------

    def list_implementors(
        self, interface_name: str, *, limit: int = 100
    ) -> list[InterfaceImplFact]:
        """List all types implementing a given interface/trait name."""
        stmt = (
            select(InterfaceImplFact)
            .where(InterfaceImplFact.interface_name == interface_name)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    def list_interfaces_of(
        self, implementor_def_uid: str, *, limit: int = 50
    ) -> list[InterfaceImplFact]:
        """List all interfaces/traits implemented by a given type."""
        stmt = (
            select(InterfaceImplFact)
            .where(InterfaceImplFact.implementor_def_uid == implementor_def_uid)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    def list_co_implementors(
        self, def_uid: str, *, limit: int = 100
    ) -> list[str]:
        """Find other types that implement the same interfaces as *def_uid*.

        Returns a list of implementor def_uids (deduplicated, excluding *def_uid*).
        """
        ifaces = self.list_interfaces_of(def_uid)
        if not ifaces:
            return []
        iface_names = [i.interface_name for i in ifaces]
        stmt = (
            select(InterfaceImplFact.implementor_def_uid)
            .where(col(InterfaceImplFact.interface_name).in_(iface_names))
            .where(InterfaceImplFact.implementor_def_uid != def_uid)
            .distinct()
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # DocCrossRef lookups
    # -------------------------------------------------------------------------

    def list_doc_xrefs_from(
        self, source_def_uid: str, *, limit: int = 50
    ) -> list[DocCrossRef]:
        """List cross-references originating from a def's docstring."""
        stmt = (
            select(DocCrossRef)
            .where(DocCrossRef.source_def_uid == source_def_uid)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    def list_doc_xrefs_to(
        self, target_def_uid: str, *, limit: int = 50
    ) -> list[DocCrossRef]:
        """List cross-references pointing TO a def."""
        stmt = (
            select(DocCrossRef)
            .where(DocCrossRef.target_def_uid == target_def_uid)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Endpoint lookups
    # -------------------------------------------------------------------------

    def batch_get_endpoints(
        self, handler_def_uids: list[str]
    ) -> dict[str, EndpointFact]:
        """Get endpoint facts for multiple handler def_uids in one query.

        Returns a dict mapping handler_def_uid → EndpointFact (first match).
        """
        if not handler_def_uids:
            return {}
        stmt = (
            select(EndpointFact)
            .where(col(EndpointFact.handler_def_uid).in_(handler_def_uids))
        )
        results = list(self._session.exec(stmt).all())
        out: dict[str, EndpointFact] = {}
        for ep in results:
            if ep.handler_def_uid and ep.handler_def_uid not in out:
                out[ep.handler_def_uid] = ep
        return out

    # -------------------------------------------------------------------------
    # Test coverage lookups
    # -------------------------------------------------------------------------

    def batch_count_test_coverage(
        self, def_uids: list[str]
    ) -> dict[str, int]:
        """Count distinct tests covering each def_uid.

        Returns a dict mapping def_uid → test count.  UIDs with zero
        coverage are included with count 0.
        """
        if not def_uids:
            return {}
        from sqlalchemy import func

        stmt = (
            select(
                TestCoverageFact.target_def_uid,
                func.count(func.distinct(TestCoverageFact.test_id)),
            )
            .where(
                col(TestCoverageFact.target_def_uid).in_(def_uids),
                TestCoverageFact.stale == False,  # noqa: E712
            )
            .group_by(TestCoverageFact.target_def_uid)
        )
        rows = list(self._session.exec(stmt).all())
        result = {uid: 0 for uid in def_uids}
        for uid, count in rows:
            result[uid] = int(count)
        return result

    def batch_get_covering_test_file_paths(
        self, def_uids: list[str],
    ) -> set[str]:
        """Get test file paths that cover any of the given def_uids.

        Parses ``test_id`` (e.g. ``tests/test_auth.py::test_login``) to
        extract the file path.  Skips synthetic ``__suite__`` IDs.
        Returns the set of unique test file paths.
        """
        if not def_uids:
            return set()
        stmt = (
            select(TestCoverageFact.test_id)
            .where(
                col(TestCoverageFact.target_def_uid).in_(def_uids),
                TestCoverageFact.stale == False,  # noqa: E712
            )
            .distinct()
        )
        rows = list(self._session.exec(stmt).all())
        paths: set[str] = set()
        for test_id in rows:
            if test_id.startswith("__suite__"):
                continue
            file_path = test_id.split("::")[0] if "::" in test_id else test_id
            if file_path:
                paths.add(file_path)
        return paths

    def batch_get_covered_def_uids(
        self, test_file_paths: list[str],
    ) -> set[str]:
        """Get def_uids covered by tests in the given test files.

        Matches ``test_id`` values that start with any of the given file
        paths (prefix match against the ``test_id`` index).
        """
        if not test_file_paths:
            return set()
        from sqlalchemy import or_, text

        # Build prefix conditions: test_id LIKE 'path::%' OR test_id = 'path'
        conditions = []
        for path in test_file_paths:
            conditions.append(
                col(TestCoverageFact.test_id).startswith(path + "::")
            )
        stmt = (
            select(TestCoverageFact.target_def_uid)
            .where(
                or_(*conditions),
                TestCoverageFact.stale == False,  # noqa: E712
            )
            .distinct()
        )
        rows = list(self._session.exec(stmt).all())
        return set(rows)


# Re-export for backwards compatibility during migration
# These will be removed once all consumers are updated
SymbolGraph = FactQueries
