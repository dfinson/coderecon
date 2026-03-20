"""Deterministic patch-to-definition mapping helpers."""

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
) -> tuple[list[DefEntry], list[DefEntry], list[DefEntry]]:
    """Map changed hunks to definitions in the index."""
    con = sqlite3.connect(str(index_db))
    cur = con.cursor()

    min_suff: list[DefEntry] = []
    thrash_prev: list[DefEntry] = []
    excluded: list[DefEntry] = []

    changed_paths = {fd.path for fd in file_diffs}

    for fd in file_diffs:
        if fd.is_deleted:
            continue

        all_defs_in_file = cur.execute(
            """
            SELECT d.name, d.kind, d.start_line, d.end_line
            FROM def_facts d
            JOIN files f ON d.file_id = f.id
            WHERE f.path = ?
            ORDER BY d.start_line
            """,
            (fd.path,),
        ).fetchall()

        if not all_defs_in_file:
            continue

        changed_def_keys: set[tuple[str, str, int]] = set()

        for hunk in fd.hunks:
            for name, kind, start, end in all_defs_in_file:
                if start <= hunk.end_line and end >= hunk.start_line:
                    key = (name, kind, start)
                    if key in changed_def_keys:
                        continue
                    changed_def_keys.add(key)
                    min_suff.append(
                        DefEntry(
                            path=fd.path,
                            name=name,
                            kind=kind,
                            start_line=start,
                            end_line=end,
                            reason=(
                                "Definition overlaps with changed hunk "
                                f"(lines {hunk.start_line}-{hunk.end_line})"
                            ),
                        )
                    )

        for name, kind, start, end in all_defs_in_file:
            key = (name, kind, start)
            if key in changed_def_keys:
                continue
            thrash_prev.append(
                DefEntry(
                    path=fd.path,
                    name=name,
                    kind=kind,
                    start_line=start,
                    end_line=end,
                    reason="Same-file context def (not in changed hunk)",
                )
            )

    _add_test_file_defs(cur, changed_paths, thrash_prev)
    _add_doc_and_config_defs(cur, changed_paths, min_suff, thrash_prev)

    con.close()
    return min_suff, thrash_prev, excluded


def _add_test_file_defs(
    cur: sqlite3.Cursor,
    changed_paths: set[str],
    thrash_prev: list[DefEntry],
) -> None:
    existing_paths = {d.path for d in thrash_prev}

    for src_path in changed_paths:
        for test_path in _guess_test_paths(src_path):
            if test_path in existing_paths:
                continue
            rows = cur.execute(
                """
                SELECT d.name, d.kind, d.start_line, d.end_line, f.path
                FROM def_facts d
                JOIN files f ON d.file_id = f.id
                WHERE f.path = ?
                ORDER BY d.start_line
                """,
                (test_path,),
            ).fetchall()
            for name, kind, start, end, path in rows:
                thrash_prev.append(
                    DefEntry(
                        path=path,
                        name=name,
                        kind=kind,
                        start_line=start,
                        end_line=end,
                        reason=f"Test file for changed source {src_path}",
                    )
                )
            if rows:
                existing_paths.add(test_path)


_DOC_DIR_PREFIXES = ("docs/", "doc/", "documentation/", "guide/", "wiki/")
_ALWAYS_RELEVANT_NAMES = frozenset(
    {"README.md", "readme.md", "README.rst", "CHANGELOG.md", "CHANGES.rst", "CHANGES.md", "CONTRIBUTING.md"}
)


def _add_doc_and_config_defs(
    cur: sqlite3.Cursor,
    changed_paths: set[str],
    min_suff: list[DefEntry],
    thrash_prev: list[DefEntry],
) -> None:
    existing_paths = {d.path for d in min_suff} | {d.path for d in thrash_prev}
    changed_dirs: set[str] = set()
    changed_stems: set[str] = set()
    for path in changed_paths:
        parts = path.split("/")
        changed_stems.add(parts[-1].rsplit(".", 1)[0].lower())
        for idx in range(1, len(parts)):
            changed_dirs.add("/".join(parts[:idx]))

    doc_files = cur.execute(
        """
        SELECT f.id, f.path FROM files f
        WHERE f.language_family IN ('markdown', 'toml', 'yaml', 'json', 'makefile', 'restructuredtext')
           OR f.path LIKE '%.md'
           OR f.path LIKE '%.rst'
           OR f.path LIKE '%.toml'
           OR f.path LIKE '%.yaml'
           OR f.path LIKE '%.yml'
           OR f.path LIKE '%.cfg'
        """
    ).fetchall()

    matched_doc_paths: list[tuple[str, str]] = []
    for _file_id, doc_path in doc_files:
        if doc_path in existing_paths:
            continue

        doc_name = doc_path.split("/")[-1]
        doc_dir = "/".join(doc_path.split("/")[:-1])
        doc_stem = doc_name.rsplit(".", 1)[0].lower()

        if doc_name in _ALWAYS_RELEVANT_NAMES and doc_dir in changed_dirs:
            matched_doc_paths.append((doc_path, "Sibling doc in same directory as changed source"))
            continue

        if any(doc_path.startswith(prefix) for prefix in _DOC_DIR_PREFIXES):
            if any(stem in doc_stem or doc_stem in stem for stem in changed_stems if len(stem) > 2):
                matched_doc_paths.append((doc_path, "Documentation file name relates to changed module"))
                continue

        if "/" not in doc_path and doc_name in (
            "pyproject.toml", "setup.cfg", "setup.py", "Cargo.toml", "package.json",
            "go.mod", "Makefile", "CMakeLists.txt", "build.gradle", "pom.xml", "composer.json",
        ):
            matched_doc_paths.append((doc_path, "Root config file may contain related build or dependency context"))

    for doc_path, reason in matched_doc_paths:
        rows = cur.execute(
            """
            SELECT d.name, d.kind, d.start_line, d.end_line
            FROM def_facts d
            JOIN files f ON d.file_id = f.id
            WHERE f.path = ?
            ORDER BY d.start_line
            """,
            (doc_path,),
        ).fetchall()

        if rows:
            for name, kind, start, end in rows:
                thrash_prev.append(
                    DefEntry(
                        path=doc_path,
                        name=name,
                        kind=kind,
                        start_line=start,
                        end_line=end,
                        reason=reason,
                    )
                )
        else:
            thrash_prev.append(
                DefEntry(
                    path=doc_path,
                    name=doc_path.split("/")[-1],
                    kind="heading",
                    start_line=1,
                    end_line=1,
                    reason=reason,
                )
            )
        existing_paths.add(doc_path)


def _guess_test_paths(src_path: str) -> list[str]:
    candidates: list[str] = []
    parts = src_path.split("/")
    filename = parts[-1]
    stem, ext = (filename.rsplit(".", 1) + [""])[:2]

    if ext == "py":
        for test_dir in ("tests", "test"):
            test_name = f"test_{stem}.{ext}"
            candidates.append(
                "/".join(parts[:-1]).replace("src/", f"{test_dir}/", 1).replace(stem, "") + test_name
                if "src/" in src_path
                else f"{test_dir}/{test_name}"
            )
        if parts[0] == "src" and len(parts) > 2:
            candidates.append(f"tests/{parts[2]}/test_{stem}.py")
        elif len(parts) >= 2:
            candidates.append(f"tests/test_{stem}.py")
            candidates.append(f"tests/{parts[0]}/test_{stem}.py")
    elif ext in ("ts", "tsx", "js", "jsx"):
        candidates.append(src_path.replace(f".{ext}", f".test.{ext}"))
        candidates.append(src_path.replace(f".{ext}", f".spec.{ext}"))
        base_dir = "/".join(parts[:-1])
        candidates.append(f"{base_dir}/__tests__/{stem}.test.{ext}")
    elif ext == "java":
        candidates.append(src_path.replace("/main/", "/test/").replace(f"{stem}.java", f"{stem}Test.java"))
    elif ext == "go":
        candidates.append(src_path.replace(f".{ext}", f"_test.{ext}"))
    elif ext == "rs":
        if "src/" in src_path:
            candidates.append(src_path.replace("src/", "tests/"))
    elif ext == "cs":
        for suffix in ("Tests", "Test"):
            candidates.append(
                src_path.replace("Src/", "Tests/")
                .replace("src/", "tests/")
                .replace(f"{stem}.cs", f"{stem}{suffix}.cs")
            )
    elif ext == "php":
        candidates.append(src_path.replace("src/", "tests/").replace(f"{stem}.php", f"{stem}Test.php"))
    elif ext == "rb":
        candidates.append(src_path.replace("lib/", "spec/").replace(f"{stem}.rb", f"{stem}_spec.rb"))
        candidates.append(src_path.replace("lib/", "test/").replace(f"{stem}.rb", f"test_{stem}.rb"))

    return candidates