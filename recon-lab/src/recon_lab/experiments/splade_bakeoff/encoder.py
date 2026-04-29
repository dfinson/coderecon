"""SPLADE sparse encoder wrapper — uniform interface for model bakeoff.

Wraps sentence-transformers SparseEncoder to provide a consistent API
across the three bakeoff candidates:
  - naver/splade-v3-distilbert  (67M, quality anchor)
  - rasyosef/splade-mini        (11M, speed anchor)
  - opensearch-project/opensearch-neural-sparse-encoding-v2-distill  (67M, license-safe)

All models output 30,522-dim sparse vectors (BERT WordPiece vocabulary).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray


# ── Model registry ───────────────────────────────────────────────

MODELS: dict[str, str] = {
    "splade-v3-distilbert": "naver/splade-v3-distilbert",
    "splade-mini": "rasyosef/splade-mini",
    "opensearch-v2": "opensearch-project/opensearch-neural-sparse-encoding-v2-distill",
    "bert-tiny-nq": "sparse-encoder-testing/splade-bert-tiny-nq",
}

VOCAB_DIM = 30_522  # BERT WordPiece vocabulary size


@dataclass
class EncodeResult:
    """Sparse encoding result for a batch of texts."""

    # CSR-like representation: list of {term_index: weight} per text
    sparse_vecs: list[dict[int, float]]
    # Timing
    encode_secs: float = 0.0
    texts_per_sec: float = 0.0


@dataclass
class SpladeEncoder:
    """Thin wrapper around sentence-transformers SparseEncoder."""

    model_name: str
    cache_dir: str | None = None
    _model: Any = field(default=None, repr=False)

    def load(self) -> None:
        """Load the model (lazy, CPU-only).

        Uses ``cache_dir`` if set, otherwise the HF default
        (``~/.cache/huggingface/hub``).
        """
        if self._model is not None:
            return
        from sentence_transformers import SparseEncoder
        self._model = SparseEncoder(
            self.model_name,
            device="cpu",
            cache_folder=self.cache_dir,
        )

    def encode_documents(self, texts: list[str], batch_size: int = 32) -> EncodeResult:
        """Encode document texts → sparse vectors."""
        self.load()
        t0 = time.monotonic()
        raw = self._model.encode_document(texts, batch_size=batch_size)
        elapsed = time.monotonic() - t0
        sparse_vecs = _torch_sparse_to_dicts(raw)
        return EncodeResult(
            sparse_vecs=sparse_vecs,
            encode_secs=elapsed,
            texts_per_sec=len(texts) / elapsed if elapsed > 0 else 0,
        )

    def encode_queries(self, texts: list[str], batch_size: int = 32) -> EncodeResult:
        """Encode query texts → sparse vectors."""
        self.load()
        t0 = time.monotonic()
        raw = self._model.encode_query(texts, batch_size=batch_size)
        elapsed = time.monotonic() - t0
        sparse_vecs = _torch_sparse_to_dicts(raw)
        return EncodeResult(
            sparse_vecs=sparse_vecs,
            encode_secs=elapsed,
            texts_per_sec=len(texts) / elapsed if elapsed > 0 else 0,
        )


def _torch_sparse_to_dicts(tensor: Any) -> list[dict[int, float]]:
    """Convert a PyTorch sparse or dense tensor (N, 30522) to list of {term_idx: weight}.

    sentence-transformers SparseEncoder returns sparse COO tensors on CPU.
    """
    import torch

    if not isinstance(tensor, torch.Tensor):
        # Fallback: numpy array
        result: list[dict[int, float]] = []
        for row in tensor:
            nz = np.nonzero(row)[0]
            result.append({int(idx): float(row[idx]) for idx in nz})
        return result

    # Coalesce sparse COO → get indices and values
    if tensor.is_sparse:
        t = tensor.coalesce()
        indices = t.indices()   # (2, nnz) for 2D
        values = t.values()
        n_rows = t.shape[0]
        result = [{} for _ in range(n_rows)]
        for i in range(indices.shape[1]):
            row_idx = int(indices[0, i])
            col_idx = int(indices[1, i])
            val = float(values[i])
            if val != 0.0:
                result[row_idx][col_idx] = val
        return result

    # Dense tensor
    dense = tensor.detach().cpu().numpy()
    result = []
    for row in dense:
        nz = np.nonzero(row)[0]
        result.append({int(idx): float(row[idx]) for idx in nz})
    return result


def sparse_dot(a: dict[int, float], b: dict[int, float]) -> float:
    """Dot product of two sparse vectors."""
    # Iterate over smaller dict
    if len(a) > len(b):
        a, b = b, a
    total = 0.0
    for idx, val in a.items():
        if idx in b:
            total += val * b[idx]
    return total


def l2_norm_sparse(vec: dict[int, float]) -> dict[int, float]:
    """L2-normalize a sparse vector."""
    norm = sum(v * v for v in vec.values()) ** 0.5
    if norm < 1e-12:
        return vec
    return {k: v / norm for k, v in vec.items()}


def aggregate_file_vector(
    def_vecs: list[dict[int, float]],
    normalize: bool = True,
) -> dict[int, float]:
    """Aggregate per-def sparse vectors into a file-level vector.

    If *normalize* is True, each def vector is L2-normalized before
    summation (prevents large defs from dominating).
    """
    if not def_vecs:
        return {}
    agg: dict[int, float] = {}
    for vec in def_vecs:
        normed = l2_norm_sparse(vec) if normalize else vec
        for idx, val in normed.items():
            agg[idx] = agg.get(idx, 0.0) + val
    return agg


def top_k_terms(
    vec: dict[int, float],
    tokenizer: Any,
    k: int = 20,
) -> list[tuple[str, float]]:
    """Return top-k activated vocabulary terms with their weights."""
    sorted_items = sorted(vec.items(), key=lambda x: x[1], reverse=True)[:k]
    return [(tokenizer.decode([idx]).strip(), weight) for idx, weight in sorted_items]


def active_dims(vec: dict[int, float]) -> int:
    """Count non-zero dimensions in a sparse vector."""
    return len(vec)
