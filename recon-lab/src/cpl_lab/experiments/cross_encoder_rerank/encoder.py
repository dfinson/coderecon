"""Cross-encoder reranker wrapper — uniform interface for model bakeoff.

Wraps sentence-transformers CrossEncoder to provide a consistent scoring
API across three MS MARCO cross-encoder candidates:
  - cross-encoder/ms-marco-TinyBERT-L-2-v2   (4.4M, speed anchor)
  - cross-encoder/ms-marco-MiniLM-L-6-v2     (22.7M, quality/speed sweet spot)
  - cross-encoder/ms-marco-MiniLM-L-12-v2    (33.4M, quality anchor)

All models take (query, document) pairs and output a single relevance score.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray


# ── Model registry ───────────────────────────────────────────────

MODELS: dict[str, str] = {
    "tinybert-l2": "cross-encoder/ms-marco-TinyBERT-L-2-v2",
    "minilm-l6": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "minilm-l12": "cross-encoder/ms-marco-MiniLM-L-12-v2",
}


@dataclass
class RerankResult:
    """Reranking result for a single query over N candidates."""

    scores: list[float]
    score_secs: float = 0.0
    pairs_per_sec: float = 0.0


@dataclass
class CrossEncoderModel:
    """Thin wrapper around sentence-transformers CrossEncoder."""

    model_name: str
    cache_dir: str | None = None
    max_length: int = 512
    _model: Any = field(default=None, repr=False)

    def load(self) -> None:
        """Load the model (lazy, CPU-only)."""
        if self._model is not None:
            return
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(
            self.model_name,
            device="cpu",
            max_length=self.max_length,
            cache_folder=self.cache_dir,
        )

    def score(
        self,
        query: str,
        documents: list[str],
        batch_size: int = 32,
    ) -> RerankResult:
        """Score (query, document) pairs. Returns one score per document."""
        self.load()
        if not documents:
            return RerankResult(scores=[], score_secs=0.0, pairs_per_sec=0.0)

        pairs = [[query, doc] for doc in documents]
        t0 = time.monotonic()
        raw_scores = self._model.predict(pairs, batch_size=batch_size)
        elapsed = time.monotonic() - t0

        # Normalize: raw_scores may be numpy array or list
        if isinstance(raw_scores, np.ndarray):
            scores = raw_scores.tolist()
        else:
            scores = list(raw_scores)

        return RerankResult(
            scores=scores,
            score_secs=elapsed,
            pairs_per_sec=len(pairs) / elapsed if elapsed > 0 else 0.0,
        )

    def score_batch(
        self,
        queries: list[str],
        documents_per_query: list[list[str]],
        batch_size: int = 32,
    ) -> list[RerankResult]:
        """Score multiple queries, each against their own candidate list.

        Batches all pairs together for efficient inference, then splits
        results back per query.
        """
        self.load()
        if not queries:
            return []

        # Flatten all (query, doc) pairs
        all_pairs: list[list[str]] = []
        boundaries: list[int] = []  # cumulative pair counts per query
        offset = 0
        for query, docs in zip(queries, documents_per_query):
            for doc in docs:
                all_pairs.append([query, doc])
            offset += len(docs)
            boundaries.append(offset)

        if not all_pairs:
            return [
                RerankResult(scores=[], score_secs=0.0, pairs_per_sec=0.0)
                for _ in queries
            ]

        t0 = time.monotonic()
        raw_scores = self._model.predict(all_pairs, batch_size=batch_size)
        elapsed = time.monotonic() - t0

        if isinstance(raw_scores, np.ndarray):
            flat_scores = raw_scores.tolist()
        else:
            flat_scores = list(raw_scores)

        # Split back per query
        results: list[RerankResult] = []
        prev = 0
        for boundary in boundaries:
            chunk = flat_scores[prev:boundary]
            n = len(chunk)
            results.append(RerankResult(
                scores=chunk,
                score_secs=elapsed * (n / len(all_pairs)) if all_pairs else 0.0,
                pairs_per_sec=len(all_pairs) / elapsed if elapsed > 0 else 0.0,
            ))
            prev = boundary

        return results


def rerank_indices(scores: list[float], top_k: int | None = None) -> list[int]:
    """Return candidate indices sorted by descending score.

    If *top_k* is set, return only the top-K indices.
    """
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    if top_k is not None:
        indexed = indexed[:top_k]
    return [idx for idx, _ in indexed]


def ndcg_at_k(
    ranked_relevances: list[int],
    k: int,
) -> float:
    """Compute NDCG@K from a list of binary relevance labels in ranked order.

    ``ranked_relevances[i]`` is 1 if the item at rank i is relevant, else 0.
    """
    if k <= 0:
        return 0.0
    truncated = ranked_relevances[:k]
    dcg = sum(
        rel / np.log2(i + 2)  # i+2 because rank is 1-indexed
        for i, rel in enumerate(truncated)
    )
    # Ideal DCG: all relevant items first
    n_relevant = sum(ranked_relevances)
    ideal = sorted(ranked_relevances, reverse=True)[:k]
    idcg = sum(
        rel / np.log2(i + 2)
        for i, rel in enumerate(ideal)
    )
    if idcg < 1e-12:
        return 0.0
    return float(dcg / idcg)
