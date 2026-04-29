"""SPLADE vector I/O, batch indexing, scaffold backfill, and query-time retrieval."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlmodel import col, select

from coderecon.index.search.splade import (
    MODEL_VERSION,
    _blob_to_vec,
    _get_encoder,
    _json_to_vec,
    _vec_to_blob,
    _vec_to_json,
    sparse_dot,
)
from coderecon.index.search.splade_scaffold import build_scaffolds_for_defs
from coderecon.index.models import DefFact, SpladeVec

if TYPE_CHECKING:
    from coderecon.index.db.database import Database

log = structlog.get_logger(__name__)

# ── Vector I/O ───────────────────────────────────────────────────

def load_all_vectors_fast(
    db: Database,
) -> dict[str, dict[int, float]]:
    """Load all SPLADE vectors from DB.
    Prefers binary vector_blob column (fast struct unpack).
    Falls back to JSON parse for rows that only have vector_json.
    """
    vecs: dict[str, dict[int, float]] = {}
    with db.session() as session:
        rows = session.exec(select(SpladeVec)).all()
        for row in rows:
            if row.vector_blob is not None:
                vecs[row.def_uid] = _blob_to_vec(row.vector_blob)
            else:
                vecs[row.def_uid] = _json_to_vec(row.vector_json)
    return vecs

def index_splade_vectors(
    db: Database,
    *,
    file_ids: list[int] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """Compute and persist SPLADE vectors for definitions.
    Args:
        db: Database instance.
        file_ids: If provided, only index defs in these files (incremental).
                  If None, index ALL defs (full index).
        progress_cb: Optional callback(encoded, total).
    Returns:
        Number of vectors stored.
    """
    encoder = _get_encoder()
    # Collect defs to encode
    with db.session() as session:
        if file_ids is not None:
            defs = list(
                session.exec(
                    select(DefFact).where(col(DefFact.file_id).in_(file_ids))
                ).all()
            )
        else:
            defs = list(session.exec(select(DefFact)).all())
        if not defs:
            return 0
        # Build scaffolds (needs callees + type refs from DB)
        scaffolds = build_scaffolds_for_defs(session, defs)
    if not scaffolds:
        return 0
    # Order for batch encoding
    uid_order = list(scaffolds.keys())
    texts = [scaffolds[uid] for uid in uid_order]
    # Encode in batches
    log.info("splade.encode_start n_defs=%d", len(texts))
    t0 = time.monotonic()
    all_vecs = encoder.encode_documents(texts)
    elapsed = time.monotonic() - t0
    log.info(
        "splade.encode_done n_defs=%d elapsed=%.1fs throughput=%.1f/s",
        len(texts), elapsed,
        len(texts) / elapsed if elapsed > 0 else 0,
    )
    # Persist to splade_vecs table
    stored = 0
    with db.session() as session:
        # Delete existing vectors for these defs (upsert)
        existing_uids = set(uid_order)
        if file_ids is not None:
            # Incremental: delete only vectors for defs in changed files
            session.exec(  # type: ignore[call-overload]
                select(SpladeVec).where(col(SpladeVec.def_uid).in_(list(existing_uids)))
            )
            for uid in existing_uids:
                existing = session.get(SpladeVec, uid)
                if existing is not None:
                    session.delete(existing)
            session.flush()
        merge_t0 = time.monotonic()
        for uid, vec in zip(uid_order, all_vecs, strict=True):
            if not vec:
                continue
            row = SpladeVec(
                def_uid=uid,
                vector_json=_vec_to_json(vec),
                vector_blob=_vec_to_blob(vec),
                model_version=MODEL_VERSION,
                scaffold_text=scaffolds.get(uid),
            )
            session.merge(row)
            stored += 1
            if stored % 500 == 0:
                log.info(
                    "splade.persist_progress stored=%d/%d merge_elapsed=%.1fs",
                    stored, len(uid_order), time.monotonic() - merge_t0,
                )
                if progress_cb:
                    progress_cb(stored, len(uid_order))
        commit_t0 = time.monotonic()
        log.info("splade.commit_start stored=%d merge_elapsed=%.1fs", stored, commit_t0 - merge_t0)
        session.commit()
        log.info("splade.commit_done commit_elapsed=%.1fs", time.monotonic() - commit_t0)
    log.info("splade.stored", extra={"count": stored})
    return stored

def backfill_scaffold_text(
    db: Database,
    file_ids: list[int] | None = None,
) -> int:
    """Populate scaffold_text on SpladeVec rows where it is NULL.
    Builds scaffolds from the DB (callees + type refs) and writes them
    to existing SpladeVec rows without re-encoding the SPLADE vector.
    Args:
        db: Database instance.
        file_ids: If provided, only backfill defs in these files.
                  If None, backfill ALL rows with NULL scaffold_text.
    Returns:
        Number of rows updated.
    """
    with db.session() as session:
        if file_ids is not None:
            defs = list(
                session.exec(
                    select(DefFact).where(col(DefFact.file_id).in_(file_ids))
                ).all()
            )
            def_uids = [d.def_uid for d in defs]
            # Filter to only those with NULL scaffold_text
            null_uids = set(
                session.exec(
                    select(SpladeVec.def_uid).where(
                        col(SpladeVec.def_uid).in_(def_uids),
                        SpladeVec.scaffold_text.is_(None),  # type: ignore[union-attr]
                    )
                ).all()
            )
            defs = [d for d in defs if d.def_uid in null_uids]
        else:
            null_uids = set(
                session.execute(
                    text("SELECT def_uid FROM splade_vecs WHERE scaffold_text IS NULL")
                ).scalars()
            )
            if not null_uids:
                return 0
            defs = list(
                session.exec(
                    select(DefFact).where(col(DefFact.def_uid).in_(list(null_uids)))
                ).all()
            )
        if not defs:
            return 0
        scaffolds = build_scaffolds_for_defs(session, defs)
        updated = 0
        for uid, text_val in scaffolds.items():
            row = session.get(SpladeVec, uid)
            if row is not None and row.scaffold_text is None:
                row.scaffold_text = text_val
                session.add(row)
                updated += 1
        session.commit()
    log.info("splade.scaffold_backfill_done", extra={"updated": updated})
    return updated

# ── Query-time retrieval ─────────────────────────────────────────

# Score floor: minimum SPLADE dot-product to be considered a candidate.
# With ~127 active dims (p50) and typical query vectors of ~40 dims,
# random overlap produces scores < 0.5.  A floor of 1.0 keeps only
# matches sharing multiple semantically-weighted terms.
_SCORE_FLOOR = 1.0

# Safety cap: prevent pathological queries from flooding the merge pool.
# Aligned with BM25 harvester's effective ceiling (~200 files × defs).
_HARD_CAP = 500

def retrieve_splade(
    db: Database,
    query_text: str,
    *,
    score_floor: float = _SCORE_FLOOR,
    hard_cap: int = _HARD_CAP,
) -> dict[str, float]:
    """Retrieve defs by SPLADE sparse dot-product.
    Selection strategy (no arbitrary K):
      1. Sparsity gate — most defs share zero active dims with the query,
         producing score=0.  These are never candidates.
      2. Score floor — low-overlap noise (score < ``score_floor``) is
         discarded.  Default 1.0 keeps only multi-term semantic matches.
      3. Hard cap — safety valve.  If more than ``hard_cap`` defs pass
         the floor, keep the top ``hard_cap`` by score.  This prevents
         pathological broad queries from flooding the merge pool.
    Returns {def_uid: splade_score}.
    """
    encoder = _get_encoder()
    query_vecs = encoder.encode_queries([query_text])
    if not query_vecs or not query_vecs[0]:
        return {}
    q_vec = query_vecs[0]
    # Score all stored vectors (uses binary cache when available)
    all_vecs = load_all_vectors_fast(db)
    scores: dict[str, float] = {}
    for uid, doc_vec in all_vecs.items():
        score = sparse_dot(q_vec, doc_vec)
        if score >= score_floor:
            scores[uid] = score
    # Hard cap (safety only — sparsity + floor do the real filtering)
    if len(scores) > hard_cap:
        sorted_items = sorted(scores.items(), key=lambda x: -x[1])[:hard_cap]
        return dict(sorted_items)
    return scores
