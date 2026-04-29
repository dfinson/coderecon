"""Coverage ingestion — join coverage reports against DefFact line ranges.

Reads CoverageReport (from any parser) and produces TestCoverageFact rows
by intersecting per-file line hit counts with DefFact start_line..end_line
ranges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from coderecon.testing.coverage.models import CoverageReport

def ingest_coverage(
    engine: Engine,
    report: CoverageReport,
    epoch: int,
    *,
    test_id_prefix: str = "",
    failed_test_ids: set[str] | None = None,
) -> int:
    """Ingest a coverage report into TestCoverageFact rows.

    For each file in the report, finds all DefFacts whose line ranges
    overlap with covered lines, and writes a TestCoverageFact for
    each (test_id, def_uid) pair.

    Args:
        engine: SQLAlchemy engine
        report: Parsed coverage report
        epoch: Current epoch ID
        test_id_prefix: Optional prefix for test IDs extracted from function coverage
        failed_test_ids: Test IDs that failed (used to set test_passed=False).
            If None, test_passed is set to None (unknown).
            If provided, matching IDs get False, others get True.

    Returns:
        Number of facts written
    """
    total_written = 0

    with engine.connect() as conn:
        for file_path, file_cov in report.files.items():
            if not file_cov.lines:
                continue

            # Get all defs in this file
            defs = conn.execute(
                text(
                    "SELECT d.def_uid, d.start_line, d.end_line, f.path "
                    "FROM def_facts d "
                    "JOIN files f ON f.id = d.file_id "
                    "WHERE f.path = :path "
                    "AND d.kind NOT IN ('variable', 'constant')"
                ),
                {"path": file_path},
            ).fetchall()

            if not defs:
                continue

            # Derive test IDs from function coverage if available
            test_ids = _extract_test_ids(file_cov, test_id_prefix)

            for def_uid, start_line, end_line, path in defs:
                # Count covered lines within this def's range
                covered = 0
                total = 0
                for line_no, hits in file_cov.lines.items():
                    if start_line <= line_no <= end_line:
                        total += 1
                        if hits > 0:
                            covered += 1

                if total == 0:
                    continue

                line_rate = covered / total

                # Compute branch rate within def range if branches available
                branch_rate = None
                if file_cov.branches:
                    br_total = 0
                    br_hit = 0
                    for br in file_cov.branches:
                        if start_line <= br.line <= end_line:
                            br_total += 1
                            if br.hits > 0:
                                br_hit += 1
                    if br_total > 0:
                        branch_rate = br_hit / br_total

                # Write one fact per test_id (or a synthetic test_id if none)
                if test_ids:
                    for tid in test_ids:
                        passed = _resolve_test_passed(tid, failed_test_ids)
                        _upsert_coverage_fact(
                            conn, tid, def_uid, path, covered, total,
                            line_rate, branch_rate, epoch, passed,
                        )
                        total_written += 1
                else:
                    # No test ID granularity — use file-level synthetic ID
                    tid = f"__suite__:{file_path}"
                    passed = _resolve_test_passed(tid, failed_test_ids)
                    _upsert_coverage_fact(
                        conn, tid, def_uid, path, covered, total,
                        line_rate, branch_rate, epoch, passed,
                    )
                    total_written += 1

        conn.commit()

    return total_written

def mark_stale_coverage(
    engine: Engine,
    changed_def_uids: list[str],
) -> int:
    """Mark TestCoverageFacts as stale for defs whose body changed.

    Returns number of rows marked stale.
    """
    if not changed_def_uids:
        return 0

    with engine.connect() as conn:
        placeholders = ", ".join(f":uid{i}" for i in range(len(changed_def_uids)))
        params = {f"uid{i}": uid for i, uid in enumerate(changed_def_uids)}
        result = conn.execute(
            text(
                f"UPDATE test_coverage_facts SET stale = 1 "
                f"WHERE target_def_uid IN ({placeholders}) AND stale = 0"
            ),
            params,
        )
        conn.commit()
        return result.rowcount  # type: ignore[return-value]

def get_covering_tests(
    engine: Engine,
    def_uids: list[str],
    *,
    include_stale: bool = True,
) -> dict[str, list[str]]:
    """Get test IDs that cover the given defs.

    Returns:
        {def_uid: [test_id, ...]}
    """
    if not def_uids:
        return {}

    result: dict[str, list[str]] = {uid: [] for uid in def_uids}

    with engine.connect() as conn:
        placeholders = ", ".join(f":uid{i}" for i in range(len(def_uids)))
        params = {f"uid{i}": uid for i, uid in enumerate(def_uids)}

        stale_clause = "" if include_stale else " AND stale = 0"
        rows = conn.execute(
            text(
                f"SELECT target_def_uid, test_id "
                f"FROM test_coverage_facts "
                f"WHERE target_def_uid IN ({placeholders}){stale_clause}"
            ),
            params,
        ).fetchall()

        for def_uid, test_id in rows:
            result[def_uid].append(test_id)

    return result

def get_uncovered_defs(engine: Engine) -> list[dict[str, str]]:
    """Find defs with zero test coverage (full suite has run but no hits).

    Returns list of {def_uid, name, kind, file_path}.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT d.def_uid, d.name, d.kind, f.path "
                "FROM def_facts d "
                "JOIN files f ON f.id = d.file_id "
                "WHERE d.kind NOT IN ('variable', 'constant', 'namespace', 'module') "
                "AND d.def_uid NOT IN ("
                "  SELECT DISTINCT target_def_uid FROM test_coverage_facts"
                ") "
                "ORDER BY f.path, d.start_line "
                "LIMIT 200"
            )
        ).fetchall()
        return [
            {"def_uid": uid, "name": name, "kind": kind, "file_path": path}
            for uid, name, kind, path in rows
        ]

def get_coverage_summary(engine: Engine) -> dict[str, float | int]:
    """Aggregate coverage statistics from TestCoverageFact."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT "
                "  COUNT(DISTINCT target_def_uid) AS defs_covered, "
                "  AVG(line_rate) AS avg_line_rate, "
                "  COUNT(*) AS total_facts, "
                "  SUM(CASE WHEN stale = 1 THEN 1 ELSE 0 END) AS stale_facts "
                "FROM test_coverage_facts"
            )
        ).fetchone()

        total_defs = conn.execute(
            text(
                "SELECT COUNT(*) FROM def_facts "
                "WHERE kind NOT IN ('variable', 'constant', 'namespace', 'module')"
            )
        ).fetchone()

        if row is None or total_defs is None:
            return {"defs_covered": 0, "total_defs": 0, "coverage_rate": 0.0}

        defs_covered = row[0] or 0
        total = total_defs[0] or 0

        return {
            "defs_covered": defs_covered,
            "total_defs": total,
            "coverage_rate": (defs_covered / total) if total > 0 else 0.0,
            "avg_line_rate": round(row[1] or 0.0, 3),
            "total_facts": row[2] or 0,
            "stale_facts": row[3] or 0,
        }

# Internal helpers

def _extract_test_ids(file_cov: object, prefix: str) -> list[str]:
    """Extract individual test IDs from function coverage data."""
    test_ids: list[str] = []
    funcs = getattr(file_cov, "functions", {})
    if funcs:
        for name, fc in funcs.items():
            if fc.hits > 0:
                tid = f"{prefix}{name}" if prefix else name
                test_ids.append(tid)
    return test_ids

def _resolve_test_passed(
    test_id: str, failed_test_ids: set[str] | None,
) -> bool | None:
    """Determine pass/fail status for a test_id.

    Returns True (passed), False (failed), or None (unknown).
    Uses prefix matching: if test_id starts with any failed ID
    (or vice versa), it's considered failed.
    """
    if failed_test_ids is None:
        return None
    if not failed_test_ids:
        return True  # no failures → everything passed
    # Exact match or prefix match (test_id may be more or less specific)
    for fid in failed_test_ids:
        if test_id == fid or test_id.startswith(fid) or fid.startswith(test_id):
            return False
    return True

def _upsert_coverage_fact(
    conn: object,
    test_id: str,
    def_uid: str,
    file_path: str,
    covered: int,
    total: int,
    line_rate: float,
    branch_rate: float | None,
    epoch: int,
    test_passed: bool | None = None,
) -> None:
    """Insert or update a single TestCoverageFact."""
    # Check if exists
    existing = conn.execute(  # type: ignore[union-attr]
        text(
            "SELECT id FROM test_coverage_facts "
            "WHERE test_id = :tid AND target_def_uid = :uid"
        ),
        {"tid": test_id, "uid": def_uid},
    ).fetchone()

    if existing:
        conn.execute(  # type: ignore[union-attr]
            text(
                "UPDATE test_coverage_facts SET "
                "covered_lines = :covered, total_lines = :total, "
                "line_rate = :lr, branch_rate = :br, "
                "epoch = :epoch, stale = 0, test_passed = :tp "
                "WHERE test_id = :tid AND target_def_uid = :uid"
            ),
            {
                "covered": covered, "total": total,
                "lr": line_rate, "br": branch_rate,
                "epoch": epoch, "tp": test_passed,
                "tid": test_id, "uid": def_uid,
            },
        )
    else:
        conn.execute(  # type: ignore[union-attr]
            text(
                "INSERT INTO test_coverage_facts "
                "(test_id, target_def_uid, target_file_path, "
                "covered_lines, total_lines, line_rate, branch_rate, "
                "epoch, stale, test_passed) "
                "VALUES (:tid, :uid, :fp, :covered, :total, :lr, :br, "
                ":epoch, 0, :tp)"
            ),
            {
                "tid": test_id, "uid": def_uid, "fp": file_path,
                "covered": covered, "total": total,
                "lr": line_rate, "br": branch_rate,
                "epoch": epoch, "tp": test_passed,
            },
        )
