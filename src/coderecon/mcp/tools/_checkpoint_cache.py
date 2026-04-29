"""Checkpoint fact caching — read persisted lint/test facts to skip re-running.

When the background analysis pipeline has already processed the changed files
at the current epoch, checkpoint can read the facts instead of re-running
lint/tests. This turns checkpoint from ~10s+ to near-instant for clean files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

@dataclass(frozen=True, slots=True)
class CachedLintResult:
    """Lint facts read from the DB instead of running lint live."""

    total_errors: int
    total_warnings: int
    total_info: int
    clean: bool
    files_checked: int
    issues: list[dict[str, str | int]]

@dataclass(frozen=True, slots=True)
class CachedTestResult:
    """Test coverage facts read from the DB instead of running tests live."""

    covered_defs: int
    total_defs: int
    average_line_rate: float
    stale_count: int
    test_ids: list[str]

def try_read_lint_facts(
    engine: Engine,
    changed_files: list[str],
    current_epoch: int,
) -> CachedLintResult | None:
    """Try to read fresh LintStatusFacts for all changed files.

    Returns CachedLintResult if ALL changed files have facts at the
    current epoch. Returns None if any file is missing or stale.
    """
    if not changed_files:
        return CachedLintResult(
            total_errors=0,
            total_warnings=0,
            total_info=0,
            clean=True,
            files_checked=0,
            issues=[],
        )

    with engine.connect() as conn:
        # Check that every changed file has at least one fact at current epoch
        placeholders = ", ".join(f":p{i}" for i in range(len(changed_files)))
        params: dict[str, str | int] = {
            f"p{i}": f for i, f in enumerate(changed_files)
        }
        params["epoch"] = current_epoch

        rows = conn.execute(
            text(
                f"SELECT file_path, tool_id, error_count, warning_count, info_count "
                f"FROM lint_status_facts "
                f"WHERE file_path IN ({placeholders}) AND epoch = :epoch"
            ),
            params,
        ).fetchall()

        # Check coverage: every file must have at least one fact
        files_with_facts = {row[0] for row in rows}
        if not all(f in files_with_facts for f in changed_files):
            return None

        total_errors = sum(r[2] for r in rows)
        total_warnings = sum(r[3] for r in rows)
        total_info = sum(r[4] for r in rows)

        issues = []
        for row in rows:
            if row[2] > 0 or row[3] > 0:
                issues.append({
                    "file": row[0],
                    "tool": row[1],
                    "errors": row[2],
                    "warnings": row[3],
                })

        return CachedLintResult(
            total_errors=total_errors,
            total_warnings=total_warnings,
            total_info=total_info,
            clean=total_errors == 0 and total_warnings == 0,
            files_checked=len(files_with_facts),
            issues=issues,
        )

def try_read_test_facts(
    engine: Engine,
    changed_def_uids: list[str],
) -> CachedTestResult | None:
    """Try to read fresh TestCoverageFacts for changed defs.

    Returns CachedTestResult if coverage facts exist and none are stale.
    Returns None if no facts exist or any are stale.
    """
    if not changed_def_uids:
        return None

    with engine.connect() as conn:
        placeholders = ", ".join(f":p{i}" for i in range(len(changed_def_uids)))
        params: dict[str, str | int] = {
            f"p{i}": uid for i, uid in enumerate(changed_def_uids)
        }

        rows = conn.execute(
            text(
                f"SELECT target_def_uid, test_id, line_rate, stale "
                f"FROM test_coverage_facts "
                f"WHERE target_def_uid IN ({placeholders})"
            ),
            params,
        ).fetchall()

        if not rows:
            return None

        stale_count = sum(1 for r in rows if r[3])
        if stale_count > 0:
            return None  # Don't use stale facts

        test_ids = list({r[1] for r in rows})
        covered_uids = {r[0] for r in rows}
        avg_rate = sum(r[2] for r in rows) / len(rows) if rows else 0.0

        return CachedTestResult(
            covered_defs=len(covered_uids),
            total_defs=len(changed_def_uids),
            average_line_rate=avg_rate,
            stale_count=stale_count,
            test_ids=test_ids,
        )
