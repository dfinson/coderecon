"""Graph maintenance operations for the index coordinator.

Standalone functions extracted from IndexCoordinatorEngine. Each takes
``engine`` as its first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlmodel import select

from coderecon.index._internal.indexing.structural import _FILE_FACT_TABLES
from coderecon.index.models import File

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


def _remove_structural_facts_for_paths(
    engine: IndexCoordinatorEngine,
    paths: list[str],
    *,
    worktree_id: int | None = None,
) -> None:
    """Remove all structural facts for the given file paths.
    Args:
        paths: Relative file paths to purge.
        worktree_id: If set, only match File rows for this worktree.
    """
    with engine.db.session() as session:
        for str_path in paths:
            stmt = select(File).where(File.path == str_path)
            if worktree_id is not None:
                stmt = stmt.where(File.worktree_id == worktree_id)
            file = session.exec(stmt).first()
            if file and file.id is not None:
                file_id = file.id
                # Remove SPLADE vectors for defs in this file
                # (must run BEFORE def_facts deletion so we can find the UIDs)
                session.exec(
                    text(
                        "DELETE FROM splade_vecs WHERE def_uid IN "
                        "(SELECT def_uid FROM def_facts WHERE file_id = :fid)"
                    ).bindparams(fid=file_id)
                )  # type: ignore[call-overload]
                # Remove test coverage facts targeting defs in this file
                session.exec(
                    text(
                        "DELETE FROM test_coverage_facts WHERE target_def_uid IN "
                        "(SELECT def_uid FROM def_facts WHERE file_id = :fid)"
                    ).bindparams(fid=file_id)
                )  # type: ignore[call-overload]
                # Remove doc cross-refs originating from this file
                session.exec(
                    text(
                        "DELETE FROM doc_cross_refs WHERE source_file_id = :fid"
                    ).bindparams(fid=file_id)
                )  # type: ignore[call-overload]
                # Remove all per-file fact tables
                for fact_model in _FILE_FACT_TABLES:
                    tname = fact_model.__tablename__  # type: ignore[attr-defined]
                    session.exec(
                        text(f"DELETE FROM {tname} WHERE file_id = :fid").bindparams(
                            fid=file_id
                        )
                    )  # type: ignore[call-overload]
        session.commit()


def _invalidate_dangling_refs(
    engine: IndexCoordinatorEngine,
    changed_file_ids: list[int],
    worktree_id: int | None = None,
) -> list[int]:
    """NULL out target_def_uid on refs whose target no longer exists.
    When files are reindexed, their defs may get new UIDs.  Refs in
    *other* files that pointed at the old UIDs become dangling.  This
    method NULLs those out and returns the file_ids that were affected
    so the caller can widen the resolution scope.
    If worktree_id is provided, only considers files in that worktree.
    """
    if not changed_file_ids:
        return []
    with engine.db.session() as session:
        # Find refs in OTHER files that point to defs that were in the
        # changed files but no longer exist with that UID.
        ph = ", ".join(f":cf_{i}" for i in range(len(changed_file_ids)))
        binds: dict[str, int] = {f"cf_{i}": fid for i, fid in enumerate(changed_file_ids)}
        # Refs whose target_def_uid once belonged to a changed file's defs
        # but is no longer present in def_facts.
        wt_clause = ""
        if worktree_id is not None:
            wt_clause = (
                " AND r.file_id IN ("
                "   SELECT id FROM files WHERE worktree_id = :wt_id"
                " )"
            )
            binds["wt_id"] = worktree_id
        affected_rows = session.execute(
            text(
                f"SELECT DISTINCT r.file_id FROM ref_facts r "
                f"WHERE r.target_def_uid IS NOT NULL "
                f"AND r.file_id NOT IN ({ph}) "
                f"{wt_clause} "
                f"AND NOT EXISTS ("
                f"  SELECT 1 FROM def_facts d "
                f"  WHERE d.def_uid = r.target_def_uid"
                f")"
            ),
            binds,
        ).fetchall()
        extra_file_ids = [row[0] for row in affected_rows]
        if extra_file_ids:
            eph = ", ".join(f":ef_{i}" for i in range(len(extra_file_ids)))
            ebinds: dict[str, int] = {
                f"ef_{i}": fid for i, fid in enumerate(extra_file_ids)
            }
            session.execute(
                text(
                    f"UPDATE ref_facts "
                    f"SET target_def_uid = NULL "
                    f"WHERE file_id IN ({eph}) "
                    f"AND target_def_uid IS NOT NULL "
                    f"AND NOT EXISTS ("
                    f"  SELECT 1 FROM def_facts d "
                    f"  WHERE d.def_uid = ref_facts.target_def_uid"
                    f")"
                ),
                ebinds,
            )
            session.commit()
            log.debug(
                "invalidated dangling refs",
                affected_files=len(extra_file_ids),
            )
        return extra_file_ids


def _propagate_def_changes(engine: IndexCoordinatorEngine, worktree_id: int) -> int:
    """Mark files in OTHER worktrees stale when their refs point to
    def_uids that no longer exist.
    After a worktree reindex, defs may have been removed or renamed
    (new UIDs).  Files in sibling worktrees that reference the old
    UIDs need re-reconciliation so their refs can be re-resolved.
    Sets ``content_hash = NULL`` on affected File rows so the next
    reconcile pass picks them up.
    Returns the number of files marked stale.
    """
    with engine.db.session() as session:
        # Find files in OTHER worktrees whose refs point at now-missing defs
        result = session.execute(
            text(
                "UPDATE files SET content_hash = NULL "
                "WHERE worktree_id != :wt "
                "AND id IN ("
                "  SELECT DISTINCT r.file_id FROM ref_facts r "
                "  WHERE r.target_def_uid IS NOT NULL "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM def_facts d "
                "    WHERE d.def_uid = r.target_def_uid"
                "  )"
                ")"
            ),
            {"wt": worktree_id},
        )
        count = result.rowcount  # type: ignore[union-attr]
        if count:
            session.commit()
            log.debug("propagated_def_changes", stale_files=count)
        return count


def _sweep_orphaned_edges(engine: IndexCoordinatorEngine) -> None:
    """Delete semantic_neighbor_facts and test_coverage_facts rows that
    reference def_uids no longer present in def_facts.
    Run after resolution passes so that any newly-created defs are visible.
    """
    with engine.db.session() as session:
        session.execute(
            text(
                "DELETE FROM semantic_neighbor_facts WHERE "
                "source_def_uid NOT IN (SELECT def_uid FROM def_facts) "
                "OR neighbor_def_uid NOT IN (SELECT def_uid FROM def_facts)"
            )
        )
        session.execute(
            text(
                "DELETE FROM test_coverage_facts WHERE "
                "target_def_uid NOT IN (SELECT def_uid FROM def_facts)"
            )
        )
        session.execute(
            text(
                "DELETE FROM doc_cross_refs WHERE "
                "target_def_uid NOT IN (SELECT def_uid FROM def_facts)"
            )
        )
        session.execute(
            text(
                "DELETE FROM doc_code_edge_facts WHERE "
                "target_def_uid NOT IN (SELECT def_uid FROM def_facts)"
            )
        )
        session.commit()


def _mark_coverage_stale(
    engine: IndexCoordinatorEngine, changed_file_ids: list[int]
) -> None:
    """Mark test_coverage_facts as stale for defs in changed files.
    When a source file is reindexed, defs that survived with the same UID
    may have different bodies.  Coverage data is no longer accurate and
    must be re-collected.
    """
    if not changed_file_ids:
        return
    with engine.db.session() as session:
        ph = ", ".join(f":cf_{i}" for i in range(len(changed_file_ids)))
        binds = {f"cf_{i}": fid for i, fid in enumerate(changed_file_ids)}
        session.execute(
            text(
                f"UPDATE test_coverage_facts SET stale = 1 "
                f"WHERE target_def_uid IN ("
                f"  SELECT def_uid FROM def_facts WHERE file_id IN ({ph})"
                f")"
            ),
            binds,
        )
        session.commit()
