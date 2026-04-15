"""Type-traced reference resolution (Pass 3).

Resolves member accesses by tracing through type annotations.
For example: ctx.mutation_ops.write_source() -> MutationOps.write_source def_uid.

This module runs AFTER Pass 2 (import resolution) and uses:
- TypeAnnotationFact: What type a variable/parameter has
- TypeMemberFact: What members a class/struct has
- MemberAccessFact: What member chains appear in code

The output is updated MemberAccessFact records with:
- resolved_type_path: e.g., "AppContext.MutationOps.write_source"
- final_target_def_uid: The def_uid of the final member
- resolution_method: "type_traced"
- resolution_confidence: 1.0 (type-traced is authoritative)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel import col, select

from coderecon.index.models import (
    MemberAccessFact,
    RefFact,
    RefTier,
    TypeAnnotationFact,
    TypeMemberFact,
)

if TYPE_CHECKING:
    from coderecon.index._internal.db import Database


@dataclass
class TypeTracedStats:
    """Statistics from type-traced resolution."""

    accesses_processed: int = 0
    accesses_resolved: int = 0
    accesses_partial: int = 0  # Resolved partway through chain
    accesses_unresolved: int = 0
    refs_upgraded: int = 0  # RefFacts upgraded to PROVEN


class TypeTracedResolver:
    """Resolves member accesses by following type annotation chains.

    Resolution Algorithm:
    1. For each MemberAccessFact without final_target_def_uid:
       a. Look up receiver type from TypeAnnotationFact in scope
       b. Walk each member in the chain:
          - Look up TypeMemberFact for (parent_type, member_name)
          - Get the member's type for next iteration
       c. For the final member, get its def_uid
    2. Update the MemberAccessFact with resolution info
    3. Optionally upgrade corresponding RefFact to PROVEN

    Usage::

        resolver = TypeTracedResolver(db)
        stats = resolver.resolve_all()
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        # Cache: (name, scope_id) -> base_type
        self._type_map: dict[tuple[str, int | None], str] = {}
        # Cache: (parent_type, member_name) -> TypeMemberFact
        self._member_map: dict[tuple[str, str], TypeMemberFact] = {}

    def resolve_all(
        self,
        *,
        limit: int = 10000,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> TypeTracedStats:
        """Resolve all unresolved member accesses.

        Pre-builds caches, resolves in parallel threads, batch-commits results.

        Args:
            limit: Maximum accesses to process in one batch
            on_progress: Optional callback(processed, total) for progress updates

        Returns:
            TypeTracedStats with resolution counts
        """
        stats = TypeTracedStats()

        with self._db.session() as session:
            # Load unresolved accesses as lightweight tuples
            rows = session.execute(
                text(
                    "SELECT access_id, receiver_name, receiver_declared_type, "
                    "scope_id, member_chain, file_id, start_line "
                    "FROM member_access_facts "
                    "WHERE final_target_def_uid IS NULL "
                    "LIMIT :lim"
                ),
                {"lim": limit},
            ).fetchall()
            stats.accesses_processed = len(rows)
            total = len(rows)

            if not rows:
                return stats

            self._build_type_cache(session)
            self._build_member_cache(session)

        # Resolve in-memory — all lookups are dict operations, no DB needed
        access_updates: list[dict] = []
        ref_upgrades: list[dict] = []

        for i, row in enumerate(rows):
            access_id, receiver_name, receiver_declared_type, scope_id, member_chain, file_id, start_line = row
            result = self._resolve_access_inmem(
                receiver_name, receiver_declared_type, scope_id, member_chain
            )
            if result is None:
                stats.accesses_unresolved += 1
                continue

            type_path, final_def_uid, confidence, status = result
            access_updates.append({
                "access_id": access_id,
                "resolved_type_path": type_path,
                "final_target_def_uid": final_def_uid,
                "resolution_method": "type_traced",
                "resolution_confidence": confidence,
            })
            if status == "resolved":
                stats.accesses_resolved += 1
                chain_parts = member_chain.split(".")
                ref_upgrades.append({
                    "file_id": file_id,
                    "start_line": start_line,
                    "token": chain_parts[-1],
                    "def_uid": final_def_uid,
                })
            else:
                stats.accesses_partial += 1

            if on_progress and (i + 1) % 50 == 0:
                on_progress(i + 1, total)

        # Batch-commit
        if access_updates or ref_upgrades:
            with self._db.session() as session:
                if access_updates:
                    session.execute(
                        text(
                            "UPDATE member_access_facts "
                            "SET resolved_type_path = :resolved_type_path, "
                            "final_target_def_uid = :final_target_def_uid, "
                            "resolution_method = :resolution_method, "
                            "resolution_confidence = :resolution_confidence "
                            "WHERE access_id = :access_id"
                        ),
                        access_updates,
                    )
                for r in ref_upgrades:
                    if r["def_uid"]:
                        session.execute(
                            text(
                                "UPDATE ref_facts "
                                "SET target_def_uid = :def_uid, ref_tier = :tier "
                                "WHERE file_id = :file_id AND start_line = :start_line "
                                "AND token_text = :token"
                            ),
                            {
                                "def_uid": r["def_uid"],
                                "tier": RefTier.PROVEN.value,
                                "file_id": r["file_id"],
                                "start_line": r["start_line"],
                                "token": r["token"],
                            },
                        )
                        stats.refs_upgraded += 1
                session.commit()

        if on_progress and total > 0:
            on_progress(total, total)

        return stats

    def resolve_for_files(
        self,
        file_ids: list[int],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> TypeTracedStats:
        """Resolve accesses only for specific files.

        Use for incremental updates after re-indexing.
        """
        stats = TypeTracedStats()

        with self._db.session() as session:
            stmt = select(MemberAccessFact).where(
                col(MemberAccessFact.file_id).in_(file_ids),
                MemberAccessFact.final_target_def_uid == None,  # noqa: E711
            )
            unresolved = list(session.exec(stmt).all())
            stats.accesses_processed = len(unresolved)
            total = len(unresolved)

            if not unresolved:
                return stats

            self._build_type_cache(session)
            self._build_member_cache(session)

            for i, access in enumerate(unresolved):
                result = self._resolve_access(session, access)
                if result == "resolved":
                    stats.accesses_resolved += 1
                elif result == "partial":
                    stats.accesses_partial += 1
                else:
                    stats.accesses_unresolved += 1
                if on_progress and (i + 1) % 50 == 0:
                    on_progress(i + 1, total)

            if on_progress and total > 0:
                on_progress(total, total)
            session.commit()

        return stats

    def _resolve_access_inmem(
        self,
        receiver_name: str,
        receiver_declared_type: str | None,
        scope_id: int | None,
        member_chain: str,
    ) -> tuple[str, str | None, float, str] | None:
        """Resolve a single member access chain in-memory.

        Returns (type_path, final_def_uid, confidence, "resolved"|"partial") or None.
        """
        receiver_type = receiver_declared_type
        if not receiver_type:
            receiver_type = self._type_map.get((receiver_name, scope_id))
            if not receiver_type:
                receiver_type = self._type_map.get((receiver_name, None))

        if not receiver_type:
            return None

        current_type = receiver_type
        chain_parts = member_chain.split(".")
        type_path = [receiver_type]
        resolved_depth = 0

        for i, member_name in enumerate(chain_parts):
            member = self._member_map.get((current_type, member_name))
            if not member:
                break

            resolved_depth = i + 1
            type_path.append(member_name)

            if i == len(chain_parts) - 1:
                return (
                    ".".join(type_path),
                    member.member_def_uid,
                    1.0,
                    "resolved",
                )

            if member.base_type:
                current_type = member.base_type
            elif member.member_kind in ("method", "static_method", "class_method"):
                break
            else:
                break

        if resolved_depth > 0:
            return (
                ".".join(type_path[: resolved_depth + 1]),
                None,
                resolved_depth / len(chain_parts),
                "partial",
            )

        return None

    def _resolve_access(
        self, session: object, access: MemberAccessFact
    ) -> str:
        """ORM-based single access resolution (used by resolve_for_files)."""
        result = self._resolve_access_inmem(
            access.receiver_name,
            access.receiver_declared_type,
            access.scope_id,
            access.member_chain,
        )
        if result is None:
            return "unresolved"

        type_path, final_def_uid, confidence, status = result
        access.resolved_type_path = type_path
        access.final_target_def_uid = final_def_uid
        access.resolution_method = "type_traced"
        access.resolution_confidence = confidence

        if status == "resolved" and final_def_uid:
            stmt = select(RefFact).where(
                RefFact.file_id == access.file_id,
                RefFact.start_line == access.start_line,
                RefFact.token_text == access.member_chain.split(".")[-1],
            )
            ref = session.exec(stmt).first()  # type: ignore[attr-defined]
            if ref:
                ref.target_def_uid = final_def_uid
                ref.ref_tier = RefTier.PROVEN.value

        return status

    def _build_type_cache(self, session: object) -> None:
        """Build (name, scope_id) -> base_type mapping."""
        self._type_map = {}

        stmt = select(TypeAnnotationFact)
        annotations = session.exec(stmt).all()  # type: ignore[attr-defined]

        for ann in annotations:
            # Add with scope
            self._type_map[(ann.target_name, ann.scope_id)] = ann.base_type
            # Also add without scope for fallback
            if ann.scope_id is not None:
                existing = self._type_map.get((ann.target_name, None))
                if not existing:
                    self._type_map[(ann.target_name, None)] = ann.base_type

    def _build_member_cache(self, session: object) -> None:
        """Build (parent_type, member_name) -> TypeMemberFact mapping."""
        self._member_map = {}

        stmt = select(TypeMemberFact)
        members = session.exec(stmt).all()  # type: ignore[attr-defined]

        for member in members:
            key = (member.parent_type_name, member.member_name)
            self._member_map[key] = member


def resolve_type_traced(
    db: Database,
    file_ids: list[int] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> TypeTracedStats:
    """Convenience function to run type-traced resolution.

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to resolve (None = all)
        on_progress: Optional callback(processed, total) for progress updates

    Returns:
        TypeTracedStats
    """
    resolver = TypeTracedResolver(db)
    if file_ids:
        return resolver.resolve_for_files(file_ids, on_progress)
    return resolver.resolve_all(on_progress=on_progress)
