#!/usr/bin/env python3
"""Purge generated-file entries from existing CodeRecon indexes.

Removes tool-generated files (lockfiles, baselines, codegen output,
amalgamations, minified assets) from both SQLite structural facts and
Tantivy lexical indexes.  This is a one-off cleanup; going forward the
indexer's exclude list prevents these files from being indexed.

Safety:
    - Dry-run by default (--dry-run / no flag).  Pass --commit to write.
    - Opens SQLite in WAL mode with foreign_keys=ON so CASCADE deletes
      propagate to def_facts, ref_facts, scope_facts, etc.
    - Uses a single transaction per database; rolls back on any error.
    - Tantivy deletions are committed only after SQLite succeeds.
    - Logs every file it would/does delete.

Usage:
    # Dry-run: show what would be deleted
    python scripts/purge_generated_from_indexes.py

    # Actually delete
    python scripts/purge_generated_from_indexes.py --commit

    # Target specific directories
    python scripts/purge_generated_from_indexes.py --commit --roots /path/to/repo
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Generated-file patterns
# ---------------------------------------------------------------------------
# Criterion: file content is produced by a tool, not a human.
# Organized by category; each entry is a regex matched against the
# repo-relative file path.

_LOCKFILES = [
    # JS/TS
    r"(^|/)package-lock\.json$",
    r"(^|/)pnpm-lock\.yaml$",
    r"(^|/)yarn\.lock$",
    r"(^|/)npm-shrinkwrap\.json$",
    # Python
    r"(^|/)poetry\.lock$",
    r"(^|/)uv\.lock$",
    r"(^|/)Pipfile\.lock$",
    r"(^|/)pdm\.lock$",
    # Ruby
    r"(^|/)Gemfile\.lock$",
    # PHP
    r"(^|/)composer\.lock$",
    # Rust
    r"(^|/)Cargo\.lock$",
    # Go
    r"(^|/)go\.sum$",
    # Elixir
    r"(^|/)mix\.lock$",
    # Dart/Flutter
    r"(^|/)pubspec\.lock$",
    # Swift/iOS
    r"(^|/)Podfile\.lock$",
    r"(^|/)Package\.resolved$",
    # .NET
    r"(^|/)packages\.lock\.json$",
    # Gradle
    r"(^|/)gradle\.lockfile$",
    # Nix
    r"(^|/)flake\.lock$",
    # GitHub Actions pinning tools (*.lock.yml / *.lock.yaml)
    r"\.lock\.(yml|yaml)$",
]

_GENERATED_ARTIFACTS = [
    r"\.baseline\.json$",
    r"\.snap$",
    r"\.snapshot$",
    r"(^|/)__snapshots__/",
    r"\.approved\.txt$",
    r"(^|/)cypress/timings\.json$",
]

_CODEGEN = [
    # Protobuf
    r"\.(pb\.go|pb\.cc|pb\.h|pb\.swift|pb\.rs)$",
    r"_pb2(_grpc)?\.py$",
    # .NET source generators
    r"\.g\.cs$",
    r"\.generated\.cs$",
    r"\.Designer\.cs$",
    # Go generate
    r"_generated\.go$",
    r"\.gen\.go$",
    # Dart build_runner
    r"\.(g|freezed|auto)\.dart$",
    # Thrift
    r"(^|/)gen-[a-z]+/",
    # SWIG
    r"_wrap\.(c|cxx|go)$",
    # Lex/Yacc/Bison
    r"\.(tab|yy)\.(c|h)$",
    r"(^|/)lex\.yy\.c$",
    # Qt MOC/UIC/RCC
    r"(^|/)moc_.*\.cpp$",
    r"(^|/)ui_.*\.h$",
    r"(^|/)qrc_.*\.cpp$",
    # GraphQL codegen
    r"\.graphql\.(ts|js)$",
    r"(^|/)generated\.(ts|js)$",
]

_AMALGAMATIONS = [
    r"(^|/)singleheader/",
    r"\.min\.(js|css)$",
]

GENERATED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in _LOCKFILES + _GENERATED_ARTIFACTS + _CODEGEN + _AMALGAMATIONS
]


def is_generated(path: str) -> bool:
    """Return True if the repo-relative path matches a generated-file pattern."""
    for pat in GENERATED_PATTERNS:
        if pat.search(path):
            return True
    return False


# ---------------------------------------------------------------------------
# Index discovery
# ---------------------------------------------------------------------------

def find_recon_dirs(roots: list[str]) -> list[Path]:
    """Find all .recon directories containing an index.db."""
    recon_dirs: list[Path] = []
    for root in roots:
        root_path = Path(root).expanduser().resolve()
        for dirpath, dirnames, filenames in os.walk(root_path):
            dp = Path(dirpath)
            if dp.name == ".recon" and "index.db" in filenames:
                recon_dirs.append(dp)
                dirnames.clear()  # don't recurse into .recon
                continue
            # Don't recurse into tantivy segment dirs
            dirnames[:] = [d for d in dirnames if d != "tantivy"]
    return sorted(recon_dirs)


# ---------------------------------------------------------------------------
# SQLite cleanup
# ---------------------------------------------------------------------------

def purge_sqlite(
    db_path: Path, *, commit: bool
) -> list[tuple[int, str, str]]:
    """Delete generated files from SQLite.  Returns list of (file_id, path, worktree_name).

    CASCADE on files.id handles def_facts, ref_facts, scope_facts,
    local_bind_facts, import_facts, dynamic_access_sites, splade_vecs,
    test_coverage_facts, doc_cross_refs, and everything else with
    an FK to files(id).
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")

    try:
        rows = conn.execute(
            "SELECT f.id, f.path, w.name "
            "FROM files f JOIN worktrees w ON f.worktree_id = w.id"
        ).fetchall()

        to_delete: list[tuple[int, str, str]] = []
        for file_id, path, wt_name in rows:
            if is_generated(path):
                to_delete.append((file_id, path, wt_name))

        if not to_delete:
            return []

        if commit:
            file_ids = [fid for fid, _, _ in to_delete]
            # Batch delete in chunks to avoid SQLite variable limit
            chunk_size = 500
            for i in range(0, len(file_ids), chunk_size):
                chunk = file_ids[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                conn.execute(
                    f"DELETE FROM files WHERE id IN ({placeholders})",
                    chunk,
                )
            conn.commit()
        return to_delete
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tantivy cleanup
# ---------------------------------------------------------------------------

def purge_tantivy(
    tantivy_path: Path,
    files: list[tuple[int, str, str]],
    *,
    commit: bool,
) -> int:
    """Delete generated-file documents from Tantivy.

    Uses the compound key ``{worktree}:{path}`` on the ``path_exact``
    field (raw tokenizer, exact match).

    Returns number of documents deleted.
    """
    if not files:
        return 0
    if not tantivy_path.is_dir():
        return 0

    try:
        import tantivy as _tantivy  # noqa: F811
    except ImportError:
        print(
            "  WARNING: tantivy-py not installed; skipping Tantivy cleanup.",
            file=sys.stderr,
        )
        return 0

    # Check schema version — only handle v2 (compound key).
    version_file = tantivy_path / "schema_version.json"
    if version_file.exists():
        import json

        try:
            version = json.loads(version_file.read_text())["version"]
        except (OSError, KeyError, ValueError):
            version = 0
        if version != 2:
            print(
                f"  WARNING: Tantivy schema version {version} != 2; "
                f"skipping (index will be rebuilt on next daemon start).",
                file=sys.stderr,
            )
            return 0

    if not commit:
        return len(files)

    # Build schema matching LexicalIndex v2
    schema_builder = _tantivy.SchemaBuilder()
    schema_builder.add_text_field("path", stored=True, tokenizer_name="default")
    schema_builder.add_text_field("path_exact", stored=False, tokenizer_name="raw")
    schema_builder.add_text_field("content", stored=True, tokenizer_name="default")
    schema_builder.add_text_field("symbols", stored=True, tokenizer_name="default")
    schema_builder.add_integer_field("context_id", stored=True, indexed=True)
    schema_builder.add_integer_field("file_id", stored=True, indexed=True)
    schema_builder.add_text_field("worktree", stored=True, tokenizer_name="raw")
    schema = schema_builder.build()

    index = _tantivy.Index(schema, path=str(tantivy_path))
    writer = index.writer()

    deleted = 0
    try:
        for _file_id, path, wt_name in files:
            compound_key = f"{wt_name}:{path}"
            n = writer.delete_documents("path_exact", compound_key)
            deleted += n
        writer.commit()
    except Exception:
        # tantivy-py writer doesn't expose rollback; on error the
        # uncommitted changes are simply dropped when writer is GC'd.
        raise

    return deleted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Purge generated-file entries from CodeRecon indexes.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually delete (default is dry-run).",
    )
    parser.add_argument(
        "--roots",
        nargs="*",
        help="Root directories to scan for .recon/index.db. "
        "Defaults to ~/wsl-repos and ~/.recon/recon-lab/clones.",
    )
    args = parser.parse_args()

    roots = args.roots or [
        os.path.expanduser("~/wsl-repos"),
        os.path.expanduser("~/.recon/recon-lab/clones"),
    ]

    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"=== purge_generated_from_indexes [{mode}] ===")
    print()

    recon_dirs = find_recon_dirs(roots)
    print(f"Found {len(recon_dirs)} index databases.")
    print()

    grand_files = 0
    grand_defs_before = 0
    grand_tantivy = 0
    errors = 0

    for recon_dir in recon_dirs:
        db_path = recon_dir / "index.db"
        tantivy_path = recon_dir / "tantivy"

        # Identify repo from parent directory
        repo_dir = recon_dir.parent
        label = str(repo_dir)

        try:
            # Get def count before (for reporting)
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.execute("PRAGMA journal_mode=WAL")
            total_defs = conn.execute(
                "SELECT count(*) FROM def_facts"
            ).fetchone()[0]
            conn.close()

            # Find and delete generated files from SQLite
            to_delete = purge_sqlite(db_path, commit=args.commit)

            if not to_delete:
                continue

            # Count defs that will/did cascade-delete
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.execute("PRAGMA journal_mode=WAL")
            if args.commit:
                # After commit, count remaining
                remaining = conn.execute(
                    "SELECT count(*) FROM def_facts"
                ).fetchone()[0]
                deleted_defs = total_defs - remaining
            else:
                # Dry-run: count defs in files we'd delete
                file_ids = [fid for fid, _, _ in to_delete]
                deleted_defs = 0
                chunk_size = 500
                for i in range(0, len(file_ids), chunk_size):
                    chunk = file_ids[i : i + chunk_size]
                    placeholders = ",".join("?" * len(chunk))
                    deleted_defs += conn.execute(
                        f"SELECT count(*) FROM def_facts WHERE file_id IN ({placeholders})",
                        chunk,
                    ).fetchone()[0]
            conn.close()

            # Delete from Tantivy
            tv_deleted = purge_tantivy(
                tantivy_path, to_delete, commit=args.commit
            )

            pct = (
                f"{100 * deleted_defs / total_defs:.1f}%"
                if total_defs > 0
                else "N/A"
            )
            print(
                f"{label}: {len(to_delete)} files, "
                f"{deleted_defs}/{total_defs} defs ({pct}), "
                f"{tv_deleted} tantivy docs"
            )
            for _fid, path, wt in to_delete[:5]:
                print(f"  [{wt}] {path}")
            if len(to_delete) > 5:
                print(f"  ... and {len(to_delete) - 5} more")

            grand_files += len(to_delete)
            grand_defs_before += deleted_defs
            grand_tantivy += tv_deleted

        except Exception as e:
            print(f"{label}: ERROR {e}", file=sys.stderr)
            errors += 1

    print()
    print(f"TOTAL: {grand_files} files, {grand_defs_before} defs, {grand_tantivy} tantivy docs")
    if errors:
        print(f"ERRORS: {errors}", file=sys.stderr)
    if not args.commit:
        print()
        print("This was a dry run. Pass --commit to apply changes.")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
