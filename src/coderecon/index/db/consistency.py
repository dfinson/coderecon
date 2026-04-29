"""Derived-signal consistency checks and backfill.

Detects gaps where a derived signal table (e.g. splade_vecs) is missing
rows that should exist based on the primary facts (e.g. def_facts).
Returns the set of file IDs that need backfill so the caller can run
only the affected repair passes without wiping the whole index.

Design:
- Each check is a simple SQL left-join that counts defs missing their
  derived signal.  Cheap: O(1) query, no Python loops.
- Model-version mismatches (e.g. SPLADE model upgraded) are treated
  the same as missing rows — the file IDs with stale vectors are
  returned for re-encoding.
- New signal types are added by appending a SignalCheck to SIGNAL_CHECKS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from coderecon.index.db.database import Database

log = structlog.get_logger(__name__)

# ── Data classes ──────────────────────────────────────────────────

@dataclass
class SignalGap:
    """A detected gap in a derived signal."""

    signal: str  # e.g. "splade_vecs"
    reason: str  # "missing" or "version_mismatch"
    file_ids: list[int]
    gap_count: int  # number of defs/rows affected

@dataclass
class ConsistencyReport:
    """Result of a consistency scan."""

    gaps: list[SignalGap] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return len(self.gaps) == 0

    @property
    def total_gaps(self) -> int:
        return sum(g.gap_count for g in self.gaps)

# ── Individual signal checks ─────────────────────────────────────

def _check_splade_vecs(db: Database, expected_version: str) -> list[SignalGap]:
    """Check for defs missing SPLADE vectors or with stale model version."""
    gaps: list[SignalGap] = []

    with db.session() as session:
        # 1. Defs with NO splade_vec row at all
        result = session.execute(
            text("""
                SELECT DISTINCT d.file_id
                FROM def_facts d
                LEFT JOIN splade_vecs sv ON sv.def_uid = d.def_uid
                WHERE sv.def_uid IS NULL
            """)
        )
        missing_file_ids = [row[0] for row in result]

        if missing_file_ids:
            # Count affected defs for logging
            count_result = session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM def_facts d
                    LEFT JOIN splade_vecs sv ON sv.def_uid = d.def_uid
                    WHERE sv.def_uid IS NULL
                """)
            )
            missing_count = count_result.scalar() or 0
            gaps.append(
                SignalGap(
                    signal="splade_vecs",
                    reason="missing",
                    file_ids=missing_file_ids,
                    gap_count=missing_count,
                )
            )

        # 2. Vectors with wrong model version
        result = session.execute(
            text("""
                SELECT DISTINCT d.file_id
                FROM def_facts d
                JOIN splade_vecs sv ON sv.def_uid = d.def_uid
                WHERE sv.model_version != :expected
            """),
            {"expected": expected_version},
        )
        stale_file_ids = [row[0] for row in result]

        if stale_file_ids:
            count_result = session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM def_facts d
                    JOIN splade_vecs sv ON sv.def_uid = d.def_uid
                    WHERE sv.model_version != :expected
                """),
                {"expected": expected_version},
            )
            stale_count = count_result.scalar() or 0
            gaps.append(
                SignalGap(
                    signal="splade_vecs",
                    reason="version_mismatch",
                    file_ids=stale_file_ids,
                    gap_count=stale_count,
                )
            )

    return gaps

# ── Registry of signal checks ────────────────────────────────────

@dataclass
class SignalCheck:
    """Definition of a signal consistency check."""

    name: str
    run: object  # Callable[[Database], list[SignalGap]]
    backfill: object | None = None  # Callable[[Database, list[int]], int] or None

def _make_splade_check() -> SignalCheck:
    """Create the SPLADE signal check with current model version."""
    from coderecon.index.search.splade import MODEL_VERSION

    def _run(db: Database) -> list[SignalGap]:
        return _check_splade_vecs(db, MODEL_VERSION)

    def _backfill(db: Database, file_ids: list[int]) -> int:
        from coderecon.index.search.splade import index_splade_vectors

        return index_splade_vectors(db, file_ids=file_ids)

    return SignalCheck(name="splade_vecs", run=_run, backfill=_backfill)

def _make_scaffold_text_check() -> SignalCheck:
    """Check for SpladeVec rows missing scaffold_text (pre-migration rows)."""

    def _run(db: Database) -> list[SignalGap]:
        gaps: list[SignalGap] = []
        with db.session() as session:
            # Count splade_vecs rows with NULL scaffold_text
            null_count = session.execute(
                text("SELECT COUNT(*) FROM splade_vecs WHERE scaffold_text IS NULL")
            ).scalar() or 0

            if null_count > 0:
                file_ids = [
                    row[0]
                    for row in session.execute(
                        text("""
                            SELECT DISTINCT d.file_id
                            FROM def_facts d
                            JOIN splade_vecs sv ON sv.def_uid = d.def_uid
                            WHERE sv.scaffold_text IS NULL
                        """)
                    )
                ]
                gaps.append(
                    SignalGap(
                        signal="scaffold_text",
                        reason="missing",
                        file_ids=file_ids,
                        gap_count=null_count,
                    )
                )
        return gaps

    def _backfill(db: Database, file_ids: list[int]) -> int:
        from coderecon.index.search.splade import backfill_scaffold_text

        return backfill_scaffold_text(db, file_ids=file_ids if file_ids else None)

    return SignalCheck(name="scaffold_text", run=_run, backfill=_backfill)

# Add new signal checks here as they are introduced.
# Each entry produces a list of SignalGap when its derived table is
# inconsistent with the primary facts.
def _get_signal_checks() -> list[SignalCheck]:
    """Build the list of signal checks (called once per scan)."""
    return [
        _make_splade_check(),
        _make_scaffold_text_check(),
        _make_doc_chunks_check(),
    ]

def _make_doc_chunks_check() -> SignalCheck:
    """Check for doc/config files missing chunk vectors and edges."""

    _doc_families = (
        "markdown", "rst", "asciidoc", "yaml", "toml", "json",
        "docker", "make", "cmake", "just", "bazel",
    )

    def _run(db: Database) -> list[SignalGap]:
        gaps: list[SignalGap] = []
        with db.session() as session:
            # Count doc files without any chunk vectors
            placeholders = ",".join(f"'{f}'" for f in _doc_families)
            missing = session.execute(
                text(f"""
                    SELECT COUNT(*) FROM files f
                    LEFT JOIN file_chunk_vecs cv ON cv.file_id = f.id
                    WHERE f.language_family IN ({placeholders})
                    AND cv.id IS NULL
                """)
            ).scalar() or 0

            if missing > 0:
                gaps.append(SignalGap(
                    signal="doc_chunks",
                    reason="missing",
                    file_ids=[],
                    gap_count=missing,
                ))

            # Check for chunks without edges (linking not done)
            has_chunks = session.execute(
                text("SELECT COUNT(*) FROM file_chunk_vecs LIMIT 1")
            ).scalar() or 0
            has_edges = session.execute(
                text("SELECT COUNT(*) FROM doc_code_edge_facts LIMIT 1")
            ).scalar() or 0

            if has_chunks > 0 and has_edges == 0:
                gaps.append(SignalGap(
                    signal="doc_chunks",
                    reason="edges_missing",
                    file_ids=[],
                    gap_count=has_chunks,
                ))
        return gaps

    def _backfill(db: Database, _file_ids: list[int]) -> int:
        from coderecon.index.structural.doc_chunks import (
            index_doc_chunk_vectors,
            link_doc_chunks_to_defs,
        )
        chunks = index_doc_chunk_vectors(db)
        edges = link_doc_chunks_to_defs(db)
        return chunks + edges

    return SignalCheck(name="doc_chunks", run=_run, backfill=_backfill)

# ── Public API ────────────────────────────────────────────────────

def check_consistency(db: Database) -> ConsistencyReport:
    """Scan all derived signals for gaps.

    Cheap SQL-only scan — safe to call on every daemon startup.
    Ensures any new derived-signal tables exist before scanning.
    """
    # Ensure new tables (e.g. splade_vecs) exist in older DBs.
    db.create_all()

    report = ConsistencyReport()

    for check in _get_signal_checks():
        try:
            gaps = check.run(db)  # type: ignore[operator]
            report.gaps.extend(gaps)
        except (SQLAlchemyError, ValueError, RuntimeError):
            log.warning("consistency.check_failed", extra={"signal": check.name}, exc_info=True)

    if report.gaps:
        for gap in report.gaps:
            log.info(
                "consistency.gap_detected",
                extra={
                    "signal": gap.signal,
                    "reason": gap.reason,
                    "gap_count": gap.gap_count,
                    "file_ids_count": len(gap.file_ids),
                },
            )
    else:
        log.debug("consistency.all_ok")

    return report

def backfill_gaps(db: Database, report: ConsistencyReport) -> dict[str, int]:
    """Run backfill for each gap in the report.

    Returns a dict of signal_name → number of rows backfilled.
    Signals without a registered backfill function are skipped.
    """
    if report.consistent:
        return {}

    # Build lookup: signal name → backfill callable
    checks = {c.name: c for c in _get_signal_checks()}
    results: dict[str, int] = {}

    for gap in report.gaps:
        check = checks.get(gap.signal)
        if check is None or check.backfill is None:
            log.warning("consistency.no_backfill", extra={"signal": gap.signal})
            continue
        try:
            log.info(
                "consistency.backfill_start",
                extra={
                    "signal": gap.signal,
                    "reason": gap.reason,
                    "gap_count": gap.gap_count,
                    "files": len(gap.file_ids),
                },
            )
            stored = check.backfill(db, gap.file_ids)  # type: ignore[operator]
            results[gap.signal] = results.get(gap.signal, 0) + stored
            log.info(
                "consistency.backfill_done",
                extra={
                    "signal": gap.signal,
                    "stored": stored,
                },
            )
        except (SQLAlchemyError, ValueError, RuntimeError):
            log.error(
                "consistency.backfill_failed",
                extra={"signal": gap.signal},
                exc_info=True,
            )

    return results
