"""Content-hash deduplication for cross-worktree indexing.

When indexing a worktree, files with matching content_hash in another
worktree can reuse facts without re-parsing.
"""

from __future__ import annotations

import time

import structlog
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from coderecon.config.constants import MS_PER_SEC


log = structlog.get_logger(__name__)


def find_reusable_files(
    engine: Engine,
    worktree_id: int,
    file_hashes: dict[str, str],
) -> dict[str, int]:
    """Find files in other worktrees with matching content hashes.

    Args:
        engine: SQLAlchemy engine for the repo's index.db.
        worktree_id: The target worktree being indexed.
        file_hashes: Mapping of {relative_path: content_hash} for the target worktree.

    Returns:
        Mapping of {relative_path: source_file_id} for files that can reuse facts.
    """
    if not file_hashes:
        return {}

    hashes = set(file_hashes.values())
    if not hashes:
        return {}

    # Find files in other worktrees with matching hashes
    placeholders = ",".join(f"'{h}'" for h in hashes)
    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT id, path, content_hash FROM files "  # noqa: S608
            f"WHERE worktree_id != {worktree_id} "
            f"AND content_hash IN ({placeholders})"
        )).fetchall()

    # Build hash → file_id mapping from other worktrees
    hash_to_source: dict[str, int] = {}
    for row in rows:
        file_id, _, content_hash = row
        if content_hash not in hash_to_source:
            hash_to_source[content_hash] = file_id

    # Map back to our paths
    reusable: dict[str, int] = {}
    for path, content_hash in file_hashes.items():
        if content_hash in hash_to_source:
            reusable[path] = hash_to_source[content_hash]

    if reusable:
        log.info(
            "content_hash_dedup",
            extra={
                "worktree_id": worktree_id,
                "total_files": len(file_hashes),
                "reusable_files": len(reusable),
                "saved_pct": round(100 * len(reusable) / len(file_hashes), 1),
            },
        )

    return reusable


def clone_facts_from_source(
    engine: Engine,
    target_file_id: int,
    source_file_id: int,
    target_unit_id: int,
) -> int:
    """Clone all facts from a source file to a target file.

    Copies def_facts, ref_facts, import_facts, scope_facts, and local_bind_facts
    from source_file_id to target_file_id. Used when content_hash matches
    across worktrees.

    Returns the number of facts cloned.
    """
    t0 = time.monotonic()
    total = 0

    fact_tables = [
        ("def_facts", "def_uid, file_id, unit_id, kind, name, qualified_name, "
         "lexical_path, start_line, start_col, end_line, end_col, "
         "docstring, decorators, base_classes, signature"),
        ("ref_facts", "file_id, unit_id, target_def_uid, ref_tier, token_text, "
         "role, certainty, start_line, start_col, end_line, end_col"),
        ("import_facts", "import_uid, file_id, unit_id, source_literal, "
         "resolved_path, imported_name, import_kind, certainty, "
         "start_line, start_col, end_line, end_col"),
        ("scope_facts", "file_id, unit_id, parent_def_uid, child_def_uid, depth"),
        ("local_bind_facts", "file_id, unit_id, name, kind, def_uid, "
         "start_line, start_col, end_line, end_col"),
    ]

    with engine.connect() as conn:
        for table, columns in fact_tables:
            # Build column list replacing file_id and unit_id
            # Build proper SQL with substitution
            col_list = [c.strip() for c in columns.split(",")]
            select_cols = []
            for c in col_list:
                if c == "file_id":
                    select_cols.append(str(target_file_id))
                elif c == "unit_id":
                    select_cols.append(str(target_unit_id))
                else:
                    select_cols.append(c)

            select_clause = ", ".join(select_cols)
            try:
                result = conn.execute(text(
                    f"INSERT INTO {table} ({columns}) "  # noqa: S608
                    f"SELECT {select_clause} FROM {table} "
                    f"WHERE file_id = {source_file_id}"
                ))
                total += result.rowcount
            except (SQLAlchemyError, ValueError):  # noqa: BLE001
                # Table might not have all columns — skip silently
                log.debug("clone_facts_skip", extra={"table": table}, exc_info=True)

        conn.commit()

    elapsed = time.monotonic() - t0
    log.debug(
        "facts_cloned",
        extra={
            "source_file_id": source_file_id,
            "target_file_id": target_file_id,
            "facts": total,
            "elapsed_ms": round(elapsed * MS_PER_SEC),
        },
    )
    return total
