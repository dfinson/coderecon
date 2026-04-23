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

import logging
import time
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import text
from sqlmodel import col, select

from coderecon.index._internal.indexing.splade import (
    _get_encoder,
    build_def_scaffold,
    load_all_vectors_fast,
    sparse_dot,
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
    SpladeVec,
)
from coderecon.ranking.cross_encoder import get_scorer

if TYPE_CHECKING:
    from coderecon.index._internal.db.database import Database

log = logging.getLogger(__name__)

# ── Thresholds (initial values — calibrate from GT data) ─────────

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


# ── Helpers ──────────────────────────────────────────────────────


def _load_all_vecs(db: Database) -> dict[str, dict[int, float]]:
    """Load all SPLADE vectors into memory (uses binary cache when available)."""
    return load_all_vectors_fast(db)


def _splade_retrieve(
    query_text: str,
    all_vecs: dict[str, dict[int, float]],
    *,
    pool_size: int = _CANDIDATE_POOL,
    exclude_uids: set[str] | None = None,
    filter_names: set[str] | None = None,
) -> list[tuple[str, float]]:
    """Retrieve top candidates by SPLADE dot product.

    Args:
        query_text: Natural language query to encode.
        all_vecs: Pre-loaded {def_uid: sparse_vec} map.
        pool_size: Max candidates to return.
        exclude_uids: Def UIDs to skip (e.g. self).
        filter_names: If set, only consider defs whose uid contains one of these names.
    """
    encoder = _get_encoder()
    q_vecs = encoder.encode_queries([query_text])
    if not q_vecs or not q_vecs[0]:
        return []
    q_vec = q_vecs[0]

    scored: list[tuple[str, float]] = []
    for uid, doc_vec in all_vecs.items():
        if exclude_uids and uid in exclude_uids:
            continue
        score = sparse_dot(q_vec, doc_vec)
        if score > 0.5:  # Loose floor — CE does the real filtering
            scored.append((uid, score))

    scored.sort(key=lambda x: -x[1])
    return scored[:pool_size]


def _ce_rerank(
    query: str,
    candidates: list[tuple[str, str]],  # (def_uid, scaffold_text)
    threshold: float,
) -> list[tuple[str, float]]:
    """CE rerank candidates, return those above threshold.

    Returns [(def_uid, ce_score)] sorted by score descending.
    """
    if not candidates:
        return []

    scorer = get_scorer()
    scaffolds = [scaffold for _, scaffold in candidates]
    scores = scorer.score_pairs(query, scaffolds)

    results: list[tuple[str, float]] = []
    for (uid, _), score in zip(candidates, scores):
        if float(score) >= threshold:
            results.append((uid, float(score)))

    results.sort(key=lambda x: -x[1])
    return results


# ── #1: Unresolved RefFact resolution ────────────────────────────


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
            .where(RefFact.role == "REFERENCE")
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
                file_path = files.get(d.file_id, None)
                fp = file_path.path if file_path else ""
                scaffold_cache[d.def_uid] = build_def_scaffold(
                    fp, kind=d.kind, name=d.name,
                    signature_text=d.signature_text,
                    qualified_name=d.qualified_name,
                    docstring=d.docstring,
                )

    log.info("semantic_resolver.refs_start", extra={"count": len(unresolved)})
    t0 = time.monotonic()
    resolved = 0

    with db.session() as session:
        for ref in unresolved:
            # Build query from ref context
            file = files.get(ref.file_id)
            file_path = file.path if file else ""
            query = f"{' '.join(word_split(ref.token_text))} in {_path_to_phrase(file_path)}"

            # SPLADE retrieve
            candidates = _splade_retrieve(query, all_vecs, pool_size=_CANDIDATE_POOL)
            if not candidates:
                continue

            # Build CE pairs
            ce_pairs = [
                (uid, scaffold_cache.get(uid, ""))
                for uid, _ in candidates
                if uid in scaffold_cache
            ]

            # CE rerank
            matches = _ce_rerank(query, ce_pairs, TAU_REF)
            if not matches:
                continue

            best_uid, best_score = matches[0]

            # Persist
            db_ref = session.get(RefFact, ref.ref_id)
            if db_ref is not None:
                db_ref.target_def_uid = best_uid
                db_ref.ref_tier = RefTier.SEMANTIC.value
                db_ref.certainty = str(round(best_score, 3))
                session.add(db_ref)
                resolved += 1

                if resolved % 100 == 0:
                    session.commit()

        session.commit()

    elapsed = time.monotonic() - t0
    log.info("semantic_resolver.refs_done", extra={"resolved": resolved,
             "total": len(unresolved), "elapsed_s": round(elapsed, 1)})
    return resolved


# ── #2: Unresolved MemberAccessFact resolution ──────────────────


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

    log.info("semantic_resolver.accesses_start", extra={"count": len(unresolved)})
    t0 = time.monotonic()
    resolved = 0

    with db.session() as session:
        for access in unresolved:
            file = files.get(access.file_id)
            file_path = file.path if file else ""

            # Build query from access context
            parts = [' '.join(word_split(access.final_member))]
            if access.receiver_declared_type:
                parts.append(f"on {' '.join(word_split(access.receiver_declared_type))}")
            parts.append(f"in {_path_to_phrase(file_path)}")
            if access.receiver_name:
                parts.append(f"receiver {' '.join(word_split(access.receiver_name))}")
            query = " ".join(parts)

            # SPLADE retrieve — prefer defs matching final_member name
            candidates = _splade_retrieve(query, all_vecs, pool_size=_CANDIDATE_POOL)
            if not candidates:
                continue

            ce_pairs = [
                (uid, scaffold_cache.get(uid, ""))
                for uid, _ in candidates
                if uid in scaffold_cache
            ]

            matches = _ce_rerank(query, ce_pairs, TAU_ACCESS)
            if not matches:
                continue

            best_uid, best_score = matches[0]

            db_access = session.get(MemberAccessFact, access.access_id)
            if db_access is not None:
                db_access.final_target_def_uid = best_uid
                db_access.resolution_method = ResolutionMethod.SEMANTIC.value
                db_access.resolution_confidence = round(best_score, 3)
                session.add(db_access)
                resolved += 1

                if resolved % 100 == 0:
                    session.commit()

        session.commit()

    elapsed = time.monotonic() - t0
    log.info("semantic_resolver.accesses_done", extra={"resolved": resolved,
             "total": len(unresolved), "elapsed_s": round(elapsed, 1)})
    return resolved


# ── #3: Unresolved ReceiverShapeFact resolution ─────────────────


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

        # Only consider class/struct/interface defs as type candidates
        type_kinds = {"class", "struct", "interface", "trait"}
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

    log.info("semantic_resolver.shapes_start", extra={"count": len(unresolved),
             "type_candidates": len(type_vecs)})
    t0 = time.monotonic()
    resolved = 0

    with db.session() as session:
        for shape in unresolved:
            # Build query from observed shape
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
            if not query:
                continue

            # SPLADE retrieve against type defs only
            candidates = _splade_retrieve(
                query, type_vecs, pool_size=_CANDIDATE_POOL
            )
            if not candidates:
                continue

            ce_pairs = [
                (uid, scaffold_cache.get(uid, ""))
                for uid, _ in candidates
                if uid in scaffold_cache
            ]

            matches = _ce_rerank(query, ce_pairs, TAU_SHAPE)
            if not matches:
                continue

            best_uid, best_score = matches[0]
            matched_def = def_map.get(best_uid)
            if matched_def is None:
                continue

            db_shape = session.get(ReceiverShapeFact, shape.shape_id)
            if db_shape is not None:
                db_shape.best_match_type = matched_def.qualified_name or matched_def.name
                db_shape.match_confidence = round(best_score, 3)
                session.add(db_shape)
                resolved += 1

                if resolved % 100 == 0:
                    session.commit()

        session.commit()

    elapsed = time.monotonic() - t0
    log.info("semantic_resolver.shapes_done", extra={"resolved": resolved,
             "total": len(unresolved), "elapsed_s": round(elapsed, 1)})
    return resolved


def _path_to_phrase(file_path: str) -> str:
    """Convert file path to natural-language phrase (reuse from splade module)."""
    from coderecon.index._internal.indexing.splade import _path_to_phrase as _ptp
    return _ptp(file_path)
