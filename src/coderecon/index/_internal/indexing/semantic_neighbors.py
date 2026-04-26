"""Compute and persist semantic neighbor edges between definitions.

At index time, for every definition with a SPLADE vector, compute dot
products against all other definition vectors and persist the top-N
pairs above a similarity floor as SemanticNeighborFact rows.

These edges capture "similar code" relationships invisible to AST
analysis — e.g. two parsers in different modules, duplicate
implementations, analogous API handlers across languages.

Performance: Uses an inverted index to avoid N² brute force.  Only
pairs sharing at least one non-zero term are evaluated, reducing the
actual comparison count by 100-1000× for typical codebases.
"""

from __future__ import annotations

import heapq
import structlog
import time
from typing import TYPE_CHECKING

from sqlmodel import select

from coderecon.index._internal.indexing.splade import (
    MODEL_VERSION,
    load_all_vectors_fast,
)
from coderecon.index.models import SemanticNeighborFact
from coderecon.config.constants import DB_FLUSH_BATCH_SIZE

if TYPE_CHECKING:
    from coderecon.index._internal.db.database import Database

log = structlog.get_logger(__name__)

# ── Tuning constants ─────────────────────────────────────────────

# Minimum dot-product similarity to persist a neighbor edge.
SIGMA_FLOOR = 15.0  # Calibrate from GT co-touch distributions

# Maximum neighbors per definition.
MAX_NEIGHBORS_PER_DEF = 20


def compute_semantic_neighbors(
    db: Database,
    *,
    sigma_floor: float = SIGMA_FLOOR,
    max_per_def: int = MAX_NEIGHBORS_PER_DEF,
    block_size: int = 2000,
    changed_file_ids: list[int] | None = None,
) -> int:
    """Compute and persist semantic neighbor edges.

    Builds a CSR sparse matrix from SPLADE vectors (memory proportional
    to non-zeros, not n×vocab), then computes similarities in row-blocks
    via sparse matmul: block_i (rows) × M^T → dense similarity slice.
    Peak memory is ~nnz×12 bytes for the matrix + block_size×n×4 per block.

    If changed_file_ids is provided, only recomputes edges for defs in
    those files (incremental mode).  This is O(k×n) where k = changed
    defs, vs O(n²) for full recomputation.

    Returns number of edges written.
    """
    import numpy as np
    from scipy.sparse import csr_matrix

    # Load all vectors (uses binary cache when available)
    all_vecs = load_all_vectors_fast(db)

    if len(all_vecs) < 2:
        return 0

    n = len(all_vecs)
    t0 = time.monotonic()

    uids = sorted(all_vecs.keys())
    uid_to_idx = {uid: i for i, uid in enumerate(uids)}
    sparse_vecs = [all_vecs[uid] for uid in uids]

    # Determine which rows to (re)compute
    if changed_file_ids is not None:
        # Incremental: only recompute rows for defs in changed files
        from coderecon.index.models import DefFact
        with db.session() as session:
            from sqlmodel import col as _col
            changed_uids = set(session.exec(
                select(DefFact.def_uid).where(
                    _col(DefFact.file_id).in_(changed_file_ids)
                )
            ).all())
        changed_indices = sorted(
            uid_to_idx[uid] for uid in changed_uids if uid in uid_to_idx
        )
        if not changed_indices:
            return 0
        log.info("semantic_neighbors.incremental_start",
                 extra={"n_defs": n, "changed": len(changed_indices)})
    else:
        changed_indices = None  # Full recompute
        log.info("semantic_neighbors.start", extra={"n_defs": n})

    # Build compressed column map: only include term IDs actually used
    all_terms: set[int] = set()
    for v in sparse_vecs:
        all_terms.update(v.keys())
    term_list = sorted(all_terms)
    term_to_col = {t: i for i, t in enumerate(term_list)}
    n_cols = len(term_list)

    # Build CSR sparse matrix — memory proportional to non-zeros only
    row_indices: list[int] = []
    col_indices: list[int] = []
    data: list[float] = []
    for i, v in enumerate(sparse_vecs):
        for term_id, weight in v.items():
            row_indices.append(i)
            col_indices.append(term_to_col[term_id])
            data.append(weight)

    M = csr_matrix(
        (np.array(data, dtype=np.float32),
         (np.array(row_indices, dtype=np.int32),
          np.array(col_indices, dtype=np.int32))),
        shape=(n, n_cols),
    )
    # Free intermediate lists
    del row_indices, col_indices, data, sparse_vecs, all_vecs

    nnz = M.nnz
    sparse_mb = round(nnz * 12 / 1e6, 1)
    log.info("semantic_neighbors.matrix_shape",
             extra={"rows": n, "cols": n_cols, "nnz": nnz,
                    "sparse_mb": sparse_mb})

    t_build = time.monotonic()
    log.info("semantic_neighbors.matrix_built",
             extra={"elapsed_s": round(t_build - t0, 2)})

    MT = M.T.tocsc()  # CSC for efficient column slicing in matmul

    if changed_indices is not None:
        # Incremental: only compute rows for changed defs
        # M[changed_indices] @ MT → (k, n) similarity slice
        changed_arr = np.array(changed_indices)
        per_def: dict[int, list[tuple[float, int]]] = {i: [] for i in changed_indices}

        for blk_start in range(0, len(changed_indices), block_size):
            blk_end = min(blk_start + block_size, len(changed_indices))
            blk_idxs = changed_arr[blk_start:blk_end]
            sim = (M[blk_idxs] @ MT).tocsr()  # keep sparse (blk_size, n)

            for local_i, global_i in enumerate(blk_idxs):
                row_start = sim.indptr[local_i]
                row_end = sim.indptr[local_i + 1]
                cols = sim.indices[row_start:row_end]
                vals = sim.data[row_start:row_end]

                # Remove self-similarity
                keep = cols != global_i
                cols = cols[keep]
                vals = vals[keep]

                mask = vals >= sigma_floor
                if not mask.any():
                    continue

                indices = cols[mask]
                scores = vals[mask]

                if len(indices) > max_per_def:
                    top_k_idx = np.argpartition(scores, -max_per_def)[-max_per_def:]
                    indices = indices[top_k_idx]
                    scores = scores[top_k_idx]

                heap = per_def[int(global_i)]
                for j, score in zip(indices, scores):
                    j_int = int(j)
                    score_f = float(score)
                    if len(heap) < max_per_def:
                        heapq.heappush(heap, (score_f, j_int))
                    elif score_f > heap[0][0]:
                        heapq.heapreplace(heap, (score_f, j_int))

        t_score = time.monotonic()
        log.info("semantic_neighbors.scored",
                 extra={"elapsed_s": round(t_score - t_build, 1)})

        # Persist: delete old edges for changed defs, insert new ones
        edges_written = 0
        changed_uid_set = {uids[i] for i in changed_indices}
        with db.session() as session:
            # Delete edges where changed def is source OR neighbor
            from sqlalchemy import or_
            session.exec(  # type: ignore[call-overload]
                SemanticNeighborFact.__table__.delete().where(  # type: ignore[union-attr]
                    or_(
                        SemanticNeighborFact.source_def_uid.in_(changed_uid_set),
                        SemanticNeighborFact.neighbor_def_uid.in_(changed_uid_set),
                    )
                )
            )
            session.commit()

            batch: list[SemanticNeighborFact] = []
            _seen: set[tuple[str, str]] = set()
            for i, heap in per_def.items():
                for score, j in heap:
                    # Write both directions for symmetric access
                    for src_i, tgt_i in [(i, j), (j, i)]:
                        _key = (uids[src_i], uids[tgt_i])
                        if _key in _seen:
                            continue
                        _seen.add(_key)
                        batch.append(SemanticNeighborFact(
                            source_def_uid=uids[src_i],
                            neighbor_def_uid=uids[tgt_i],
                            score=round(score, 3),
                            model_version=MODEL_VERSION,
                        ))
                        edges_written += 1

                    if len(batch) >= DB_FLUSH_BATCH_SIZE:
                        session.add_all(batch)
                        session.commit()
                        batch.clear()

            if batch:
                session.add_all(batch)
                session.commit()

    else:
        # Full recompute: process ALL rows in blocks
        per_def_full: list[list[tuple[float, int]]] = [[] for _ in range(n)]

        for start in range(0, n, block_size):
            end = min(start + block_size, n)
            sim = (M[start:end] @ MT).tocsr()  # keep sparse (blk_size, n)

            for local_i in range(end - start):
                global_i = start + local_i
                row_start = sim.indptr[local_i]
                row_end = sim.indptr[local_i + 1]
                cols = sim.indices[row_start:row_end]
                vals = sim.data[row_start:row_end]

                # Remove self-similarity
                keep = cols != global_i
                cols = cols[keep]
                vals = vals[keep]

                mask = vals >= sigma_floor
                if not mask.any():
                    continue

                indices = cols[mask]
                scores = vals[mask]

                if len(indices) > max_per_def:
                    top_k_idx = np.argpartition(scores, -max_per_def)[-max_per_def:]
                    indices = indices[top_k_idx]
                    scores = scores[top_k_idx]

                for j, score in zip(indices, scores):
                    j_int = int(j)
                    score_f = float(score)
                    for src, tgt in [(global_i, j_int), (j_int, global_i)]:
                        heap = per_def_full[src]
                        if len(heap) < max_per_def:
                            heapq.heappush(heap, (score_f, tgt))
                        elif score_f > heap[0][0]:
                            heapq.heapreplace(heap, (score_f, tgt))

            if start > 0:
                log.info("semantic_neighbors.progress",
                         extra={"done": end, "total": n})

        t_score = time.monotonic()
        log.info("semantic_neighbors.scored",
                 extra={"elapsed_s": round(t_score - t_build, 1)})

        # Persist: full clear + insert
        edges_written = 0
        with db.session() as session:
            session.exec(  # type: ignore[call-overload]
                SemanticNeighborFact.__table__.delete()  # type: ignore[union-attr]
            )
            session.commit()

            batch = []
            _seen_full: set[tuple[str, str]] = set()
            for i, heap in enumerate(per_def_full):
                for score, j in heap:
                    _key = (uids[i], uids[j])
                    if _key in _seen_full:
                        continue
                    _seen_full.add(_key)
                    batch.append(SemanticNeighborFact(
                        source_def_uid=uids[i],
                        neighbor_def_uid=uids[j],
                        score=round(score, 3),
                        model_version=MODEL_VERSION,
                    ))
                    edges_written += 1

                    if len(batch) >= DB_FLUSH_BATCH_SIZE:
                        session.add_all(batch)
                        session.commit()
                        batch.clear()

            if batch:
                session.add_all(batch)
                session.commit()

    elapsed = time.monotonic() - t0
    log.info("semantic_neighbors.done", extra={"edges_written": edges_written,
             "elapsed_s": round(elapsed, 1)})
    return edges_written
