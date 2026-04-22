"""Deterministic patch-to-definition mapping helpers.

Maps patch hunks to indexed definitions.  Only produces
``minimum_sufficient_defs`` (definitions whose spans overlap a changed
hunk).  Labels are binary: relevant (1) vs irrelevant (0).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Hunk:
    """A changed region in a file."""

    start_line: int
    line_count: int

    @property
    def end_line(self) -> int:
        return self.start_line + max(self.line_count - 1, 0)


@dataclass(frozen=True)
class FileDiff:
    """Parsed diff for a single file."""

    path: str
    hunks: tuple[Hunk, ...]
    is_new_file: bool = False
    is_deleted: bool = False


@dataclass
class DefEntry:
    """A definition from the CodeRecon index."""

    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    reason: str = ""


_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff into per-file hunk lists."""
    files: list[FileDiff] = []
    current_path: str | None = None
    current_hunks: list[Hunk] = []
    is_new = False
    is_deleted = False

    for line in diff_text.split("\n"):
        header_match = _DIFF_HEADER.match(line)
        if header_match:
            if current_path is not None:
                files.append(
                    FileDiff(
                        path=current_path,
                        hunks=tuple(current_hunks),
                        is_new_file=is_new,
                        is_deleted=is_deleted,
                    )
                )
            current_path = header_match.group(1)
            current_hunks = []
            is_new = False
            is_deleted = False
            continue

        if line.startswith("new file mode"):
            is_new = True
        elif line.startswith("deleted file mode"):
            is_deleted = True

        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match and current_path is not None:
            start = int(hunk_match.group(1))
            count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            if count > 0:
                current_hunks.append(Hunk(start_line=start, line_count=count))

    if current_path is not None:
        files.append(
            FileDiff(
                path=current_path,
                hunks=tuple(current_hunks),
                is_new_file=is_new,
                is_deleted=is_deleted,
            )
        )

    return files


def map_hunks_to_defs(
    file_diffs: list[FileDiff],
    index_db: Path,
    worktree_name: str,
) -> list[DefEntry]:
    """Map changed hunks to indexed definitions for a worktree.

    Queries ``def_facts`` from the main repo's index.db, filtered by
    ``worktree_name`` via the ``files.worktree_id`` → ``worktrees.name``
    join.  Returns only minimum_sufficient defs (definitions whose spans
    overlap a changed hunk).
    """
    con = sqlite3.connect(str(index_db))
    cur = con.cursor()

    # Resolve worktree_id — require the specific worktree to be indexed.
    # Do NOT fall back to main: main has HEAD defs, not the PR-commit defs,
    # which would poison training data with wrong line ranges.
    row = cur.execute(
        "SELECT id FROM worktrees WHERE name = ?", (worktree_name,)
    ).fetchone()
    if row is not None:
        has_files = cur.execute(
            "SELECT 1 FROM files WHERE worktree_id = ? LIMIT 1", (row[0],)
        ).fetchone()
        if not has_files:
            con.close()
            return []  # registered but not indexed
    if row is None:
        con.close()
        return []  # worktree not found
    wt_id = row[0]

    # Build lookup: relative path → list of (name, kind, start_line, end_line)
    defs_by_path: dict[str, list[tuple[str, str, int, int]]] = {}
    for name, kind, start_line, end_line, path in cur.execute(
        """
        SELECT d.name, d.kind, d.start_line, d.end_line, f.path
        FROM def_facts d
        JOIN files f ON d.file_id = f.id
        WHERE f.worktree_id = ?
        """,
        (wt_id,),
    ):
        defs_by_path.setdefault(path, []).append((name, kind, start_line, end_line))

    con.close()

    min_suff: list[DefEntry] = []
    changed_def_keys: set[tuple[str, str, int]] = set()

    for fd in file_diffs:
        if fd.is_deleted:
            continue

        defs = defs_by_path.get(fd.path)
        if not defs:
            continue

        for hunk in fd.hunks:
            for name, kind, start_line, end_line in defs:
                if start_line <= hunk.end_line and end_line >= hunk.start_line:
                    key = (name, kind, start_line)
                    if key in changed_def_keys:
                        continue
                    changed_def_keys.add(key)
                    min_suff.append(
                        DefEntry(
                            path=fd.path,
                            name=name,
                            kind=kind,
                            start_line=start_line,
                            end_line=end_line,
                            reason=(
                                "Definition overlaps with changed hunk "
                                f"(lines {hunk.start_line}-{hunk.end_line})"
                            ),
                        )
                    )

    return min_suff


def expand_defs_via_coverage(
    minimum_sufficient: list[DefEntry],
    index_db: Path,
) -> list[DefEntry]:
    """Expand ground truth via deterministic test↔source coverage links.

    Given a set of minimum_sufficient defs, queries ``test_coverage_facts``:
      1. Source defs → covering test files → all defs in those test files
      2. Test defs → covered source defs

    Only non-stale coverage facts are used.  Returns new DefEntry objects
    not already in the minimum_sufficient set.
    """
    if not minimum_sufficient:
        return []

    con = sqlite3.connect(str(index_db))
    cur = con.cursor()

    # Check if coverage table exists and has data
    try:
        has_coverage = cur.execute(
            "SELECT COUNT(*) FROM test_coverage_facts WHERE stale = 0"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        con.close()
        return []

    if not has_coverage:
        con.close()
        return []

    # Build lookup of existing GT keys to avoid duplicates
    existing_keys: set[tuple[str, str, str, int]] = set()
    for d in minimum_sufficient:
        existing_keys.add((d.path, d.kind, d.name, d.start_line))

    # Resolve def_uids for minimum_sufficient defs
    source_uids: list[str] = []
    test_file_paths: list[str] = []

    for d in minimum_sufficient:
        row = cur.execute(
            """
            SELECT d.def_uid, f.path
            FROM def_facts d
            JOIN files f ON d.file_id = f.id
            WHERE f.path = ? AND d.name = ? AND d.kind = ?
              AND d.start_line = ?
            LIMIT 1
            """,
            (d.path, d.name, d.kind, d.start_line),
        ).fetchone()
        if row is None:
            continue
        uid, path = row
        # Classify as test vs source
        parts = path.split("/")
        basename = parts[-1] if parts else ""
        is_test = (
            any(p in ("tests", "test") for p in parts[:-1])
            or basename.startswith("test_")
            or basename.endswith("_test.py")
        )
        if is_test:
            test_file_paths.append(path)
        else:
            source_uids.append(uid)

    expanded: list[DefEntry] = []

    # Direction 1: source defs → covering test files → test defs
    if source_uids:
        placeholders = ",".join("?" * len(source_uids))
        test_ids = cur.execute(
            f"SELECT DISTINCT test_id FROM test_coverage_facts "
            f"WHERE target_def_uid IN ({placeholders}) AND stale = 0",
            source_uids,
        ).fetchall()

        test_paths: set[str] = set()
        for (test_id,) in test_ids:
            if test_id.startswith("__suite__"):
                continue
            fp = test_id.split("::")[0] if "::" in test_id else test_id
            if fp:
                test_paths.add(fp)

        for tp in test_paths:
            defs_in_test = cur.execute(
                """
                SELECT d.name, d.kind, d.start_line, d.end_line, f.path
                FROM def_facts d
                JOIN files f ON d.file_id = f.id
                WHERE f.path = ?
                """,
                (tp,),
            ).fetchall()
            for name, kind, start, end, path in defs_in_test:
                key = (path, kind, name, start)
                if key not in existing_keys:
                    existing_keys.add(key)
                    expanded.append(DefEntry(
                        path=path, name=name, kind=kind,
                        start_line=start, end_line=end,
                        reason=f"test file covers GT source def (coverage-linked)",
                    ))

    # Direction 2: test files → covered source defs
    if test_file_paths:
        covered_uids: set[str] = set()
        for tp in test_file_paths:
            rows = cur.execute(
                "SELECT DISTINCT target_def_uid FROM test_coverage_facts "
                "WHERE test_id LIKE ? AND stale = 0",
                (tp + "::%",),
            ).fetchall()
            for (uid,) in rows:
                covered_uids.add(uid)

        if covered_uids:
            placeholders = ",".join("?" * len(covered_uids))
            defs = cur.execute(
                f"""
                SELECT d.name, d.kind, d.start_line, d.end_line, f.path
                FROM def_facts d
                JOIN files f ON d.file_id = f.id
                WHERE d.def_uid IN ({placeholders})
                """,
                list(covered_uids),
            ).fetchall()
            for name, kind, start, end, path in defs:
                key = (path, kind, name, start)
                if key not in existing_keys:
                    existing_keys.add(key)
                    expanded.append(DefEntry(
                        path=path, name=name, kind=kind,
                        start_line=start, end_line=end,
                        reason=f"source def covered by GT test (coverage-linked)",
                    ))

    con.close()
    return expanded