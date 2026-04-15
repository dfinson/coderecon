"""Lint status persistence — write LintStatusFact from LintResult."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def persist_lint_status(
    engine: Engine,
    file_path: str,
    tool_id: str,
    category: str,
    error_count: int,
    warning_count: int,
    info_count: int,
    epoch: int,
) -> None:
    """Write or update a LintStatusFact for a file+tool pair."""
    clean = error_count == 0 and warning_count == 0 and info_count == 0

    with engine.connect() as conn:
        existing = conn.execute(
            text(
                "SELECT id FROM lint_status_facts "
                "WHERE file_path = :fp AND tool_id = :tid"
            ),
            {"fp": file_path, "tid": tool_id},
        ).fetchone()

        if existing:
            conn.execute(
                text(
                    "UPDATE lint_status_facts SET "
                    "error_count = :ec, warning_count = :wc, info_count = :ic, "
                    "clean = :clean, epoch = :epoch "
                    "WHERE file_path = :fp AND tool_id = :tid"
                ),
                {
                    "ec": error_count, "wc": warning_count, "ic": info_count,
                    "clean": clean, "epoch": epoch,
                    "fp": file_path, "tid": tool_id,
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO lint_status_facts "
                    "(file_path, tool_id, category, error_count, warning_count, "
                    "info_count, clean, epoch) "
                    "VALUES (:fp, :tid, :cat, :ec, :wc, :ic, :clean, :epoch)"
                ),
                {
                    "fp": file_path, "tid": tool_id, "cat": category,
                    "ec": error_count, "wc": warning_count, "ic": info_count,
                    "clean": clean, "epoch": epoch,
                },
            )
        conn.commit()


def get_lint_summary(engine: Engine) -> dict[str, int | float]:
    """Aggregate lint status from LintStatusFact."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT "
                "  COUNT(DISTINCT file_path) AS files_checked, "
                "  SUM(error_count) AS total_errors, "
                "  SUM(warning_count) AS total_warnings, "
                "  SUM(info_count) AS total_info, "
                "  SUM(CASE WHEN clean = 1 THEN 1 ELSE 0 END) AS clean_files "
                "FROM lint_status_facts"
            )
        ).fetchone()

        if row is None or row[0] == 0:
            return {
                "files_checked": 0, "total_errors": 0,
                "total_warnings": 0, "clean_files": 0, "clean_rate": 0.0,
            }

        files_checked = row[0] or 0
        clean_files = row[4] or 0

        return {
            "files_checked": files_checked,
            "total_errors": row[1] or 0,
            "total_warnings": row[2] or 0,
            "total_info": row[3] or 0,
            "clean_files": clean_files,
            "clean_rate": round(clean_files / files_checked, 3) if files_checked else 0.0,
        }


def get_file_lint_status(
    engine: Engine, file_path: str
) -> list[dict[str, str | int | bool]]:
    """Get lint status for a specific file across all tools."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tool_id, category, error_count, warning_count, "
                "info_count, clean, epoch "
                "FROM lint_status_facts WHERE file_path = :fp"
            ),
            {"fp": file_path},
        ).fetchall()

        return [
            {
                "tool_id": r[0], "category": r[1],
                "error_count": r[2], "warning_count": r[3],
                "info_count": r[4], "clean": bool(r[5]), "epoch": r[6],
            }
            for r in rows
        ]
