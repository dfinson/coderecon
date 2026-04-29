"""Semantic resolution of unresolved graph edges via SPLADE + cross-encoder.

Plugs three categories of graph gaps:

1. Unresolved RefFact edges (target_def_uid IS NULL) — identifier references
   that deterministic Pass 2 couldn't trace.
2. Unresolved MemberAccessFact chains (final_target_def_uid IS NULL) — member
   access chains where the receiver type is unknown.
3. Unresolved ReceiverShapeFact (best_match_type IS NULL) — duck-type shapes
   that structural matching couldn't resolve.

Pipeline: SPLADE retrieval (candidate pool ~50) → cross-encoder rerank →
persist edge only if CE score exceeds a calibrated confidence threshold.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np
import structlog
from sqlmodel import col, select

from coderecon._core.languages import TYPE_KINDS
from coderecon.index.search.splade import (
    _get_encoder,
    build_def_scaffold,
    load_all_vectors_fast,
    word_split,
)
from coderecon.index.models import (
    DefFact,
    File,
    MemberAccessFact,
    ReceiverShapeFact,
    RefFact,
    RefTier,
    ResolutionMethod,
    Role,
)
from coderecon.ranking.cross_encoder import get_tiny_scorer

if TYPE_CHECKING:
    from coderecon.index.db.database import Database

log = structlog.get_logger(__name__)

# Minimum CE score to persist a semantic edge.  MiniLM-L6-v2 logits
# are unbounded; typical relevant pairs score 2-6, irrelevant <0.
# Start conservative — false edges are worse than missing edges.
TAU_REF = 2.0  # RefFact resolution
TAU_ACCESS = 2.5  # MemberAccessFact resolution (more ambiguous)
TAU_SHAPE = 2.0  # ReceiverShapeFact resolution

# SPLADE retrieval pool size per unresolved edge.
_CANDIDATE_POOL = 50

# Batch size for CE scoring.
_CE_BATCH = 64

def _load_all_vecs(db: Database) -> dict[str, dict[int, float]]:
    """Load all SPLADE vectors into memory (uses binary cache when available)."""
    return load_all_vectors_fast(db)

def _batch_splade_retrieve(
    queries: list[str],
    all_vecs: dict[str, dict[int, float]],
    *,
    pool_size: int = _CANDIDATE_POOL,
) -> list[list[tuple[str, float]]]:
    """Batch-encode queries and retrieve top candidates for each.
    Encodes all queries in one batched GPU call, then computes all dot
    products as a single sparse matrix multiply using scipy.
    Returns a list of candidate lists, one per query.
    """
    if not queries or not all_vecs:
        return [[] for _ in queries]
    from scipy.sparse import csr_matrix
    encoder = _get_encoder()
    q_vecs = encoder.encode_queries(queries)
    # Build sparse matrices for vectorized dot product
    uid_list = list(all_vecs.keys())
    # Find vocabulary size (max term id + 1)
    max_tid = 0
    for v in all_vecs.values():
        if v:
            max_tid = max(max_tid, max(v.keys()))
    for v in q_vecs:
        if v:
            max_tid = max(max_tid, max(v.keys()))
    vocab_size = max_tid + 1
    # Build doc matrix (n_docs × vocab)
    doc_rows, doc_cols, doc_vals = [], [], []
    for i, uid in enumerate(uid_list):
        vec = all_vecs[uid]
        for tid, w in vec.items():
            doc_rows.append(i)
            doc_cols.append(tid)
            doc_vals.append(w)
    doc_matrix = csr_matrix(
        (doc_vals, (doc_rows, doc_cols)),
        shape=(len(uid_list), vocab_size),
        dtype=np.float32,
    )
    # Build query matrix (n_queries × vocab)
    q_rows, q_cols, q_vals = [], [], []
    for i, qv in enumerate(q_vecs):
        if qv:
            for tid, w in qv.items():
                q_rows.append(i)
                q_cols.append(tid)
                q_vals.append(w)
    query_matrix = csr_matrix(
        (q_vals, (q_rows, q_cols)),
        shape=(len(queries), vocab_size),
        dtype=np.float32,
    )
    # scores: (n_queries × n_docs) — keep sparse to avoid OOM on large repos
    scores_sparse = (query_matrix @ doc_matrix.T).tocsr()
    log.info("semantic_resolver.batch_retrieve queries=%d docs=%d", len(queries), len(uid_list))
    results: list[list[tuple[str, float]]] = []
    for qi in range(len(queries)):
        row_start = scores_sparse.indptr[qi]
        row_end = scores_sparse.indptr[qi + 1]
        cols = scores_sparse.indices[row_start:row_end]
        vals = scores_sparse.data[row_start:row_end]
        # Get indices where score > 0.5
        mask = vals > 0.5
        if not mask.any():
            results.append([])
            continue
        idxs = cols[mask]
        filtered_vals = vals[mask]
        scored = [(uid_list[j], float(v)) for j, v in zip(idxs, filtered_vals, strict=True)]
        scored.sort(key=lambda x: -x[1])
        results.append(scored[:pool_size])
    return results

def resolve_unresolved_refs(db: Database, *, file_ids: list[int] | None = None) -> int:
    """Find unresolved refs and attempt SPLADE+CE resolution.
    When *file_ids* is given, only resolve refs belonging to those files
    (incremental mode).  Otherwise resolves across the entire repo.
    Returns number of refs resolved.
    """
    all_vecs = _load_all_vecs(db)
    if not all_vecs:
        return 0
    # Load unresolved refs
    with db.session() as session:
        stmt = (
            select(RefFact)
            .where(RefFact.target_def_uid.is_(None))  # type: ignore[union-attr]
            .where(RefFact.role == Role.REFERENCE)
        )
        if file_ids is not None:
            stmt = stmt.where(col(RefFact.file_id).in_(file_ids))
        unresolved = list(session.exec(stmt.limit(10000)).all())
        if not unresolved:
            return 0
        # Pre-fetch file paths and enclosing defs for context
        file_ids = list({r.file_id for r in unresolved})
        files = {f.id: f for f in session.exec(
            select(File).where(col(File.id).in_(file_ids))
        ).all() if f.id is not None}
        # Build scaffold cache for CE
        all_def_uids = list(all_vecs.keys())
        def_map: dict[str, DefFact] = {}
        scaffold_cache: dict[str, str] = {}
        for batch_start in range(0, len(all_def_uids), 500):
            batch_uids = all_def_uids[batch_start:batch_start + 500]
            defs = list(session.exec(
                select(DefFact).where(col(DefFact.def_uid).in_(batch_uids))
            ).all())
            for d in defs:
                def_map[d.def_uid] = d
                file_path = files.get(d.file_id)
                fp = file_path.path if file_path else ""
                scaffold_cache[d.def_uid] = build_def_scaffold(
                    fp, kind=d.kind, name=d.name,
                    signature_text=d.signature_text,
                    qualified_name=d.qualified_name,
                    docstring=d.docstring,
                )
    log.info("semantic_resolver.refs_start count=%d", len(unresolved))
    t0 = time.monotonic()
    # Build all queries upfront
    queries: list[str] = []
    for ref in unresolved:
        file = files.get(ref.file_id)
        file_path = file.path if file else ""
        queries.append(f"{' '.join(word_split(ref.token_text))} in {_path_to_phrase(file_path)}")
    # Batch SPLADE retrieval — one GPU call for all queries
    all_candidates = _batch_splade_retrieve(queries, all_vecs, pool_size=_CANDIDATE_POOL)
    log.info("semantic_resolver.refs_splade_done elapsed=%.1fs", time.monotonic() - t0)
    # Collect CE pairs — restrict to same-file candidates only.
    # Cross-file semantic edges are redundant with live SPLADE retrieval
    # at query time and dominated by test↔test noise (90%+ on httpx).
    ce_pairs: list[tuple[str, str]] = []
    ce_meta: list[tuple[int, str]] = []  # (item_idx, def_uid)
    for i, candidates in enumerate(all_candidates):
        if not candidates:
            continue
        ref_file_id = unresolved[i].file_id
        for uid, _ in candidates:
            d = def_map.get(uid)
            if d is None or d.file_id != ref_file_id:
                continue
            scaffold = scaffold_cache.get(uid, "")
            if not scaffold:
                continue
            ce_pairs.append((queries[i], scaffold))
            ce_meta.append((i, uid))
    ce_scores = get_tiny_scorer().score_bulk_pairs(ce_pairs) if ce_pairs else np.array([])
    log.info("semantic_resolver.refs_ce_done pairs=%d elapsed=%.1fs",
             len(ce_pairs), time.monotonic() - t0)
    # Best CE match per item above threshold
    item_results: dict[int, list[tuple[str, float]]] = {}
    for (idx, uid), score in zip(ce_meta, ce_scores, strict=True):
        s = float(score)
        if s >= TAU_REF:
            item_results.setdefault(idx, []).append((uid, s))
    resolved = 0
    with db.session() as session:
        for idx, matches in item_results.items():
            matches.sort(key=lambda x: -x[1])
            best_uid, best_score = matches[0]
            ref = unresolved[idx]
            db_ref = session.get(RefFact, ref.ref_id)
            if db_ref is not None:
                db_ref.target_def_uid = best_uid
                db_ref.ref_tier = RefTier.SEMANTIC.value
                db_ref.certainty = str(round(best_score, 3))
                session.add(db_ref)
                resolved += 1
                if resolved % 500 == 0:
                    session.commit()
        session.commit()
    elapsed = time.monotonic() - t0
    log.info("semantic_resolver.refs_done resolved=%d total=%d elapsed=%.1fs",
             resolved, len(unresolved), elapsed)
    return resolved

def resolve_unresolved_accesses(db: Database, *, file_ids: list[int] | None = None) -> int:
    """Find unresolved member access chains and attempt SPLADE+CE resolution.
    When *file_ids* is given, only resolve accesses belonging to those files
    (incremental mode).  Otherwise resolves across the entire repo.
    Returns number of accesses resolved.
    """
    all_vecs = _load_all_vecs(db)
    if not all_vecs:
        return 0
    with db.session() as session:
        stmt = (
            select(MemberAccessFact)
            .where(MemberAccessFact.final_target_def_uid.is_(None))  # type: ignore[union-attr]
        )
        if file_ids is not None:
            stmt = stmt.where(col(MemberAccessFact.file_id).in_(file_ids))
        unresolved = list(session.exec(stmt.limit(10000)).all())
        if not unresolved:
            return 0
        file_ids = list({a.file_id for a in unresolved})
        files = {f.id: f for f in session.exec(
            select(File).where(col(File.id).in_(file_ids))
        ).all() if f.id is not None}
        # Build scaffold cache
        all_def_uids = list(all_vecs.keys())
        def_map: dict[str, DefFact] = {}
        scaffold_cache: dict[str, str] = {}
        for batch_start in range(0, len(all_def_uids), 500):
            batch_uids = all_def_uids[batch_start:batch_start + 500]
            defs = list(session.exec(
                select(DefFact).where(col(DefFact.def_uid).in_(batch_uids))
            ).all())
            for d in defs:
                def_map[d.def_uid] = d
                fp = files.get(d.file_id)
                scaffold_cache[d.def_uid] = build_def_scaffold(
                    fp.path if fp else "", kind=d.kind, name=d.name,
                    signature_text=d.signature_text,
                    qualified_name=d.qualified_name,
                    docstring=d.docstring,
                )
        # Build name→def_uid index for final_member filtering
        name_to_uids: dict[str, list[str]] = {}
        for uid, d in def_map.items():
            name_to_uids.setdefault(d.name, []).append(uid)
    log.info("semantic_resolver.accesses_start count=%d", len(unresolved))
    t0 = time.monotonic()
    # Build all queries upfront
    queries: list[str] = []
    for access in unresolved:
        file = files.get(access.file_id)
        file_path = file.path if file else ""
        parts = [' '.join(word_split(access.final_member))]
        if access.receiver_declared_type:
            parts.append(f"on {' '.join(word_split(access.receiver_declared_type))}")
        parts.append(f"in {_path_to_phrase(file_path)}")
        if access.receiver_name:
            parts.append(f"receiver {' '.join(word_split(access.receiver_name))}")
        queries.append(" ".join(parts))
    # Batch SPLADE retrieval — one GPU call for all queries
    all_candidates = _batch_splade_retrieve(queries, all_vecs, pool_size=_CANDIDATE_POOL)
    log.info("semantic_resolver.accesses_splade_done elapsed=%.1fs", time.monotonic() - t0)
    # Collect ALL CE pairs and bulk-score with TinyBERT
    # Also build per-query uid→splade_rank map for diagnostics
    ce_pairs: list[tuple[str, str]] = []
    ce_meta: list[tuple[int, str]] = []  # (item_idx, def_uid)
    splade_rank_map: dict[int, dict[str, int]] = {}  # item_idx → {uid → rank}
    for i, candidates in enumerate(all_candidates):
        if not candidates:
            continue
        uid_ranks = {uid: rank for rank, (uid, _) in enumerate(candidates)}
        splade_rank_map[i] = uid_ranks
        for uid, _ in candidates:
            scaffold = scaffold_cache.get(uid, "")
            if not scaffold:
                continue
            ce_pairs.append((queries[i], scaffold))
            ce_meta.append((i, uid))
    ce_scores = get_tiny_scorer().score_bulk_pairs(ce_pairs) if ce_pairs else np.array([])
    log.info("semantic_resolver.accesses_ce_done pairs=%d elapsed=%.1fs",
             len(ce_pairs), time.monotonic() - t0)
    # Best CE match per item above threshold
    item_results: dict[int, list[tuple[str, float]]] = {}
    for (idx, uid), score in zip(ce_meta, ce_scores, strict=True):
        s = float(score)
        if s >= TAU_ACCESS:
            item_results.setdefault(idx, []).append((uid, s))
    # Log SPLADE rank of CE winners
    winner_ranks: list[int] = []
    for idx, matches in item_results.items():
        matches.sort(key=lambda x: -x[1])
        best_uid = matches[0][0]
        ranks = splade_rank_map.get(idx, {})
        rank = ranks.get(best_uid)
        if rank is not None:
            winner_ranks.append(rank)
    if winner_ranks:
        r_arr = np.array(winner_ranks)
        pcts = np.percentile(r_arr, [50, 75, 90, 95, 99])
        log.info(
            "semantic_resolver.accesses_ce_winner_splade_rank n=%d "
            "p50=%d p75=%d p90=%d p95=%d p99=%d max=%d",
            len(r_arr), *[int(p) for p in pcts], int(r_arr.max()),
        )
    # Persist
    resolved = 0
    with db.session() as session:
        for idx, matches in item_results.items():
            matches.sort(key=lambda x: -x[1])
            best_uid, best_score = matches[0]
            access = unresolved[idx]
            db_access = session.get(MemberAccessFact, access.access_id)
            if db_access is not None:
                db_access.final_target_def_uid = best_uid
                db_access.resolution_method = ResolutionMethod.SEMANTIC.value
                db_access.resolution_confidence = round(best_score, 3)
                session.add(db_access)
                resolved += 1
                if resolved % 500 == 0:
                    session.commit()
        session.commit()
    elapsed = time.monotonic() - t0
    log.info("semantic_resolver.accesses_done resolved=%d total=%d elapsed=%.1fs pairs=%d",
             resolved, len(unresolved), elapsed, len(ce_pairs))
    return resolved

def resolve_unresolved_shapes(db: Database, *, file_ids: list[int] | None = None) -> int:
    """Find unresolved receiver shapes and attempt SPLADE+CE type matching.
    When *file_ids* is given, only resolve shapes belonging to those files
    (incremental mode).  Otherwise resolves across the entire repo.
    Returns number of shapes resolved.
    """
    all_vecs = _load_all_vecs(db)
    if not all_vecs:
        return 0
    with db.session() as session:
        stmt = (
            select(ReceiverShapeFact)
            .where(ReceiverShapeFact.best_match_type.is_(None))  # type: ignore[union-attr]
        )
        if file_ids is not None:
            stmt = stmt.where(col(ReceiverShapeFact.file_id).in_(file_ids))
        unresolved = list(session.exec(stmt.limit(5000)).all())
        if not unresolved:
            return 0
        file_ids = list({s.file_id for s in unresolved})
        files = {f.id: f for f in session.exec(
            select(File).where(col(File.id).in_(file_ids))
        ).all() if f.id is not None}
        # Only consider type-declaration defs as type candidates
        type_kinds = TYPE_KINDS
        all_def_uids = list(all_vecs.keys())
        def_map: dict[str, DefFact] = {}
        scaffold_cache: dict[str, str] = {}
        type_vecs: dict[str, dict[int, float]] = {}
        for batch_start in range(0, len(all_def_uids), 500):
            batch_uids = all_def_uids[batch_start:batch_start + 500]
            defs = list(session.exec(
                select(DefFact).where(col(DefFact.def_uid).in_(batch_uids))
            ).all())
            for d in defs:
                def_map[d.def_uid] = d
                if d.kind in type_kinds:
                    type_vecs[d.def_uid] = all_vecs[d.def_uid]
                    fp = files.get(d.file_id)
                    scaffold_cache[d.def_uid] = build_def_scaffold(
                        fp.path if fp else "", kind=d.kind, name=d.name,
                        signature_text=d.signature_text,
                        qualified_name=d.qualified_name,
                        docstring=d.docstring,
                    )
    if not type_vecs:
        return 0
    log.info("semantic_resolver.shapes_start count=%d type_candidates=%d",
             len(unresolved), len(type_vecs))
    t0 = time.monotonic()
    # Build all queries upfront
    queries: list[str] = []
    valid_indices: list[int] = []  # indices into unresolved that have non-empty queries
    for idx, shape in enumerate(unresolved):
        members = shape.get_observed_members()
        fields = members.get("fields", [])
        methods = members.get("methods", [])
        query_parts = []
        if fields:
            query_parts.append(f"fields {' '.join(fields[:10])}")
        if methods:
            query_parts.append(f"methods {' '.join(methods[:10])}")
        if shape.receiver_name:
            query_parts.append(f"receiver {' '.join(word_split(shape.receiver_name))}")
        query = " ".join(query_parts)
        if query:
            queries.append(query)
            valid_indices.append(idx)
    if not queries:
        return 0
    # Batch SPLADE retrieval against type defs only
    all_candidates = _batch_splade_retrieve(queries, type_vecs, pool_size=_CANDIDATE_POOL)
    log.info("semantic_resolver.shapes_splade_done elapsed=%.1fs", time.monotonic() - t0)
    # Collect ALL CE pairs and bulk-score with TinyBERT
    ce_pairs: list[tuple[str, str]] = []
    ce_meta: list[tuple[int, str]] = []  # (q_idx, def_uid)
    for q_idx, candidates in enumerate(all_candidates):
        if not candidates:
            continue
        for uid, _ in candidates:
            scaffold = scaffold_cache.get(uid, "")
            if not scaffold:
                continue
            ce_pairs.append((queries[q_idx], scaffold))
            ce_meta.append((q_idx, uid))
    ce_scores = get_tiny_scorer().score_bulk_pairs(ce_pairs) if ce_pairs else np.array([])
    log.info("semantic_resolver.shapes_ce_done pairs=%d elapsed=%.1fs",
             len(ce_pairs), time.monotonic() - t0)
    # Best CE match per item above threshold
    item_results: dict[int, list[tuple[str, float]]] = {}
    for (q_idx, uid), score in zip(ce_meta, ce_scores, strict=True):
        s = float(score)
        if s >= TAU_SHAPE:
            item_results.setdefault(q_idx, []).append((uid, s))
    resolved = 0
    with db.session() as session:
        for q_idx, matches in item_results.items():
            matches.sort(key=lambda x: -x[1])
            best_uid, best_score = matches[0]
            matched_def = def_map.get(best_uid)
            if matched_def is None:
                continue
            orig_idx = valid_indices[q_idx]
            shape = unresolved[orig_idx]
            db_shape = session.get(ReceiverShapeFact, shape.shape_id)
            if db_shape is not None:
                db_shape.best_match_type = matched_def.qualified_name or matched_def.name
                db_shape.match_confidence = round(best_score, 3)
                session.add(db_shape)
                resolved += 1
                if resolved % 500 == 0:
                    session.commit()
        session.commit()
    elapsed = time.monotonic() - t0
    log.info("semantic_resolver.shapes_done resolved=%d total=%d elapsed=%.1fs",
             resolved, len(unresolved), elapsed)
    return resolved

def _path_to_phrase(file_path: str) -> str:
    """Convert file path to natural-language phrase (reuse from splade module)."""
    from coderecon.index.search.splade import _path_to_phrase as _ptp
    return _ptp(file_path)
