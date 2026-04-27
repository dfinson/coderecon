"""Blast-radius test selection — coverage-backed with graduated fallback.

Priority:
1. Direct coverage (confidence=1.0) — TestCoverageFact for changed defs
2. Caller coverage (confidence=0.6-0.8) — tests covering callers of changed defs
3. Scope affinity (confidence=0.4-0.5) — tests for sibling defs in same file/class
4. Import graph BFS (confidence=0.1-0.2) — classic transitive import walk
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

@dataclass(frozen=True, slots=True)
class TestCandidate:
    """A test suggested for execution."""

    test_id: str
    source: str  # "coverage", "caller_coverage", "scope_affinity", "import_graph"
    distance: int  # Hops from changed def
    confidence: float  # 0.0-1.0
    reason: str  # Human-readable explanation

@dataclass(slots=True)
class BlastRadiusResult:
    """Result of blast-radius test selection."""

    candidates: list[TestCandidate] = field(default_factory=list)
    coverage_gaps: list[dict[str, str]] = field(default_factory=list)
    has_coverage_data: bool = False

    @property
    def test_ids(self) -> list[str]:
        """Unique test IDs sorted by confidence descending."""
        seen: set[str] = set()
        result: list[str] = []
        for c in sorted(self.candidates, key=lambda x: x.confidence, reverse=True):
            if c.test_id not in seen:
                seen.add(c.test_id)
                result.append(c.test_id)
        return result

def select_tests_for_changed_defs(
    engine: Engine,
    changed_def_uids: list[str],
    *,
    max_hops: int = 2,
) -> BlastRadiusResult:
    """Select tests for a set of changed defs using graduated fallback.

    1. Direct coverage: tests that cover the changed defs
    2. Caller coverage: tests that cover callers of changed defs
    3. Scope affinity: tests for sibling defs in same file
    4. Import graph: last resort
    """
    result = BlastRadiusResult()

    if not changed_def_uids:
        return result

    with engine.connect() as conn:
        # Check if we have ANY coverage data
        coverage_count = conn.execute(
            text("SELECT COUNT(*) FROM test_coverage_facts")
        ).fetchone()
        result.has_coverage_data = bool(coverage_count and coverage_count[0] > 0)

        # Layer 0: Direct coverage
        direct = _get_direct_coverage(conn, changed_def_uids)
        result.candidates.extend(direct)

        covered_uids = {c.test_id for c in direct}

        # Layer 1: Caller coverage (tests covering defs that call changed defs)
        if max_hops >= 1:
            caller_tests = _get_caller_coverage(conn, changed_def_uids)
            for ct in caller_tests:
                if ct.test_id not in covered_uids:
                    result.candidates.append(ct)
                    covered_uids.add(ct.test_id)

        # Layer 2: Scope affinity (tests for sibling defs)
        if max_hops >= 2:
            scope_tests = _get_scope_affinity(conn, changed_def_uids)
            for st in scope_tests:
                if st.test_id not in covered_uids:
                    result.candidates.append(st)
                    covered_uids.add(st.test_id)

        # Identify coverage gaps: changed defs with zero covering tests
        if result.has_coverage_data:
            for uid in changed_def_uids:
                has_coverage = conn.execute(
                    text(
                        "SELECT 1 FROM test_coverage_facts "
                        "WHERE target_def_uid = :uid LIMIT 1"
                    ),
                    {"uid": uid},
                ).fetchone()

                if not has_coverage:
                    info = conn.execute(
                        text(
                            "SELECT d.name, d.kind, f.path "
                            "FROM def_facts d JOIN files f ON f.id = d.file_id "
                            "WHERE d.def_uid = :uid"
                        ),
                        {"uid": uid},
                    ).fetchone()
                    if info:
                        result.coverage_gaps.append({
                            "def_uid": uid,
                            "name": info[0],
                            "kind": info[1],
                            "file_path": info[2],
                        })

    return result

# Layer implementations

def _get_direct_coverage(
    conn: object, def_uids: list[str]
) -> list[TestCandidate]:
    """Layer 0: Tests that directly cover the changed defs."""
    if not def_uids:
        return []

    placeholders = ", ".join(f":uid{i}" for i in range(len(def_uids)))
    params = {f"uid{i}": uid for i, uid in enumerate(def_uids)}

    rows = conn.execute(  # type: ignore[union-attr]
        text(
            f"SELECT DISTINCT test_id, target_def_uid, line_rate "
            f"FROM test_coverage_facts "
            f"WHERE target_def_uid IN ({placeholders}) AND stale = 0"
        ),
        params,
    ).fetchall()

    return [
        TestCandidate(
            test_id=row[0],
            source="coverage",
            distance=0,
            confidence=1.0,
            reason=f"directly covers {row[1]} ({row[2]:.0%} line rate)",
        )
        for row in rows
    ]

def _get_caller_coverage(
    conn: object, def_uids: list[str]
) -> list[TestCandidate]:
    """Layer 1: Tests covering defs that call the changed defs."""
    if not def_uids:
        return []

    placeholders = ", ".join(f":uid{i}" for i in range(len(def_uids)))
    params = {f"uid{i}": uid for i, uid in enumerate(def_uids)}

    # Find callers: defs whose refs point to changed defs
    # Then find tests covering those callers
    rows = conn.execute(  # type: ignore[union-attr]
        text(
            f"SELECT DISTINCT tc.test_id, sd.def_uid AS caller_uid "
            f"FROM ref_facts r "
            f"JOIN def_facts sd ON sd.file_id = r.file_id "
            f"  AND r.start_line >= sd.start_line AND r.start_line <= sd.end_line "
            f"JOIN test_coverage_facts tc ON tc.target_def_uid = sd.def_uid "
            f"WHERE r.target_def_uid IN ({placeholders}) "
            f"AND sd.def_uid NOT IN ({placeholders}) "
            f"AND tc.stale = 0 "
            f"LIMIT 50"
        ),
        params,
    ).fetchall()

    return [
        TestCandidate(
            test_id=row[0],
            source="caller_coverage",
            distance=1,
            confidence=0.7,
            reason=f"covers caller {row[1]}",
        )
        for row in rows
    ]

def _get_scope_affinity(
    conn: object, def_uids: list[str]
) -> list[TestCandidate]:
    """Layer 2: Tests for sibling defs in same file."""
    if not def_uids:
        return []

    placeholders = ", ".join(f":uid{i}" for i in range(len(def_uids)))
    params = {f"uid{i}": uid for i, uid in enumerate(def_uids)}

    # Find file_ids of changed defs, then find other defs in those files,
    # then find tests covering those sibling defs
    rows = conn.execute(  # type: ignore[union-attr]
        text(
            f"SELECT DISTINCT tc.test_id, sibling.def_uid AS sibling_uid "
            f"FROM def_facts changed "
            f"JOIN def_facts sibling ON sibling.file_id = changed.file_id "
            f"  AND sibling.def_uid != changed.def_uid "
            f"JOIN test_coverage_facts tc ON tc.target_def_uid = sibling.def_uid "
            f"WHERE changed.def_uid IN ({placeholders}) "
            f"AND tc.stale = 0 "
            f"LIMIT 30"
        ),
        params,
    ).fetchall()

    return [
        TestCandidate(
            test_id=row[0],
            source="scope_affinity",
            distance=2,
            confidence=0.4,
            reason=f"covers sibling def {row[1]}",
        )
        for row in rows
    ]
