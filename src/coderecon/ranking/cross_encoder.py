"""ONNX-based cross-encoder reranker (MiniLM-L-6-v2 via onnxruntime + tokenizers).

No torch or sentence-transformers dependency.  Loads a vendored ONNX model
and tokenizer from the package data directory, following the same pattern
as the SPLADE encoder in ``index._internal.indexing.splade``.

Model exported via::

    uv run recon-lab ce-export

then vendored into ``ranking/models/ce_minilm_l6/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import onnxruntime as ort
import structlog
from numpy.typing import NDArray
from tokenizers import Tokenizer

log = structlog.get_logger(__name__)

# ONNX model + tokenizer (from coderecon-models-ce package)
from coderecon_models_ce import ONNX_PATH as _ONNX_PATH
from coderecon_models_ce import TOKENIZER_PATH as _TOKENIZER_PATH


@dataclass
class CrossEncoderScorer:
    """ONNX cross-encoder scorer for (query, document) pairs.

    Produces a single relevance logit per pair — higher = more relevant.
    Designed for CPU-only inference at candidate counts of 50–200.
    """

    onnx_path: Path = _ONNX_PATH
    tokenizer_path: Path = _TOKENIZER_PATH
    max_length: int = 512
    _session: ort.InferenceSession | None = field(default=None, repr=False)
    _tokenizer: Tokenizer | None = field(default=None, repr=False)

    def load(self) -> None:
        """Load ONNX session + tokenizer (lazy, auto-detect GPU)."""
        if self._session is not None:
            return
        if not self.onnx_path.exists():
            raise FileNotFoundError(
                f"Cross-encoder ONNX model not found at {self.onnx_path}. "
                "Export it with: uv run recon-lab ce-export"
            )
        from coderecon.index._internal.indexing.splade import _select_onnx_providers

        providers = _select_onnx_providers()
        opts = ort.SessionOptions()
        # Suppress BFC arena fallback warnings — the allocator
        # intentionally falls back to system malloc when a single
        # allocation exceeds the arena's free capacity.
        opts.log_severity_level = 3  # Error
        self._session = ort.InferenceSession(
            str(self.onnx_path),
            sess_options=opts,
            providers=providers,
        )
        active = self._session.get_providers()
        _gpu = any(p != "CPUExecutionProvider" for p in active)
        self._tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self._tokenizer.enable_truncation(max_length=self.max_length)
        self._tokenizer.enable_padding()
        log.debug("cross_encoder.loaded", extra={"model": str(self.onnx_path), "providers": active, "gpu": _gpu})

    # ONNX Runtime allocates O(batch × seq²) intermediate tensors for
    # attention.  At seq_len=512 that's ~11 MB per candidate — a batch
    # of 1 600 candidates consumes 18 GB.  Micro-batching keeps peak
    # memory bounded: 64 × 11 MB ≈ 700 MB per micro-batch.
    _MICRO_BATCH: int = 64

    def score_pairs(
        self,
        query: str,
        documents: list[str],
        *,
        micro_batch: int | None = None,
    ) -> NDArray[np.float32]:
        """Score (query, document) pairs. Returns array of relevance logits.

        Uses the tokenizer's built-in sequence-pair encoding to produce
        ``[CLS] query [SEP] document [SEP]`` token sequences.

        Inference is split into micro-batches to keep ONNX Runtime's
        intermediate memory bounded.
        """
        self.load()
        assert self._session is not None and self._tokenizer is not None

        if not documents:
            return np.array([], dtype=np.float32)

        bs = micro_batch or self._MICRO_BATCH
        all_scores: list[NDArray[np.float32]] = []

        for start in range(0, len(documents), bs):
            chunk = documents[start : start + bs]

            # Encode pairs — tokenizers lib handles pair encoding
            encodings = self._tokenizer.encode_batch(
                [(query, doc) for doc in chunk],
            )

            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            tids = np.array([e.type_ids for e in encodings], dtype=np.int64)

            (logits,) = self._session.run(
                None,
                {
                    "input_ids": ids,
                    "attention_mask": mask,
                    "token_type_ids": tids,
                },
            )

            # logits shape: (batch, 1) or (batch,) — flatten to 1-D
            scores = logits.squeeze(-1) if logits.ndim == 2 else logits
            all_scores.append(scores.astype(np.float32))

        return np.concatenate(all_scores)

    def score_bulk_pairs(
        self,
        pairs: list[tuple[str, str]],
        *,
        micro_batch: int | None = None,
    ) -> NDArray[np.float32]:
        """Score pre-built (query, document) pairs in bulk.

        Unlike score_pairs() which takes one query and N docs, this
        accepts N arbitrary (query, doc) tuples — suitable for batching
        CE across many different queries at once.
        """
        self.load()
        assert self._session is not None and self._tokenizer is not None

        if not pairs:
            return np.array([], dtype=np.float32)

        bs = micro_batch or self._MICRO_BATCH
        all_scores: list[NDArray[np.float32]] = []

        for start in range(0, len(pairs), bs):
            chunk = pairs[start : start + bs]

            encodings = self._tokenizer.encode_batch(chunk)

            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            tids = np.array([e.type_ids for e in encodings], dtype=np.int64)

            (logits,) = self._session.run(
                None,
                {
                    "input_ids": ids,
                    "attention_mask": mask,
                    "token_type_ids": tids,
                },
            )

            scores = logits.squeeze(-1) if logits.ndim == 2 else logits
            all_scores.append(scores.astype(np.float32))

        return np.concatenate(all_scores)

# ── Singleton ─────────────────────────────────────────────────────

_SCORER: CrossEncoderScorer | None = None

def get_scorer() -> CrossEncoderScorer:
    """Return the singleton cross-encoder scorer (lazy-loaded)."""
    global _SCORER
    if _SCORER is None:
        _SCORER = CrossEncoderScorer()
    return _SCORER

# ── TinyBERT fast scorer ─────────────────────────────────────────

_TINY_SCORER: CrossEncoderScorer | None = None

def get_tiny_scorer() -> CrossEncoderScorer:
    """Return the singleton TinyBERT cross-encoder scorer (lazy-loaded).

    Uses TinyBERT-L-2-v2 (4.4M params, ~1,800 defs/sec) for cheap
    semantic scoring of the full candidate pool before file pruning.
    """
    global _TINY_SCORER
    if _TINY_SCORER is None:
        from coderecon_models_ce_tiny import ONNX_PATH, TOKENIZER_PATH

        _TINY_SCORER = CrossEncoderScorer(
            onnx_path=ONNX_PATH,
            tokenizer_path=TOKENIZER_PATH,
        )
    return _TINY_SCORER
