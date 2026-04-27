"""SPLADE sparse retrieval — scaffold building, encoding, and storage.

Uses splade-mini (rasyosef/splade-mini, 11M params, Apache-2.0) via a
pre-exported ONNX model to produce sparse term-weight vectors for each
DefFact.  Vectors are stored in SQLite as compact JSON and queried via
sparse dot-product at retrieval time.

Runtime dependencies: ``onnxruntime`` + ``tokenizers`` (~55 MB total).
No torch or sentence-transformers required.

The scaffold builder converts structured index facts into anglicised text
that SPLADE can encode effectively.  Field order follows measured marginal
recall from the recon-lab bakeoff (calls +4.8%, uses, mentions +1.5%,
sig +0.7%, doc +0.2%).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
import structlog
from tokenizers import Tokenizer

from coderecon.config.constants import BYTES_PER_MB

log = structlog.get_logger(__name__)

# ── ONNX provider selection ──────────────────────────────────────

# Cache: None = not checked, True/False = result
_gpu_active: bool | None = None

def _ensure_cuda_lib_path() -> None:
    """Make cuDNN / cuBLAS findable for onnxruntime-gpu.
    On Linux, ``onnxruntime-gpu`` needs ``libcudnn.so.9`` at runtime.
    We search pip-installed nvidia packages and well-known system paths,
    then **preload** the library via ctypes so ``dlopen`` finds it even
    when LD_LIBRARY_PATH wasn't set at process start.
    This must be called **before** the first ``InferenceSession`` creation.
    """
    import ctypes
    import os
    import sys
    if sys.platform != "linux":
        return
    lib_dirs: list[str] = []
    # 1. Pip-installed nvidia packages (canonical for venv-based installs)
    for pkg in ("nvidia.cudnn", "nvidia.cublas", "nvidia.cuda_nvrtc"):
        try:
            mod = __import__(pkg, fromlist=["__path__"])
            lib_dir = str(Path(mod.__path__[0]) / "lib")
            if Path(lib_dir).is_dir():
                lib_dirs.append(lib_dir)
        except ImportError:
            log.debug("cuda_pkg_not_available", pkg=pkg)
    # 2. Well-known system CUDA paths
    if not lib_dirs:
        candidates = [
            "/usr/local/cuda/lib64",
            "/usr/lib/x86_64-linux-gnu",
        ]
        # Ollama bundles cuDNN 9 + CUDA libs; search its known directories
        for p in sorted(Path("/usr/local/lib/ollama").glob("*cuda*"), reverse=True):
            if p.is_dir():
                candidates.insert(0, str(p))
        for candidate in candidates:
            cudnn = Path(candidate) / "libcudnn.so.9"
            if cudnn.exists():
                lib_dirs.append(candidate)
                break
    if not lib_dirs:
        return
    # Update LD_LIBRARY_PATH for any child processes
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    existing_parts = set(existing.split(":")) if existing else set()
    new_parts = [d for d in lib_dirs if d not in existing_parts]
    if new_parts:
        os.environ["LD_LIBRARY_PATH"] = ":".join(new_parts + ([existing] if existing else []))
    # Preload libcudnn.so.9 so dlopen() finds it in the current process
    for d in lib_dirs:
        cudnn_path = Path(d) / "libcudnn.so.9"
        if cudnn_path.exists():
            try:
                ctypes.CDLL(str(cudnn_path), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                log.debug("cudnn_preload_failed", path=str(cudnn_path), exc_info=True)
            break

def _select_onnx_providers(
    vram_bytes: int | None = None,
) -> list[str | tuple[str, dict[str, Any]]]:
    """Select the best available ONNX Runtime execution providers.
    Tries GPU providers first (CUDA, ROCm, CoreML), falls back to CPU.
    Respects CODERECON_ONNX_DEVICE env var to force a specific provider.
    When *vram_bytes* is given and CUDA is available, configures the BFC
    arena to use ``kSameAsRequested`` (no power-of-two overshoot) and
    caps ``gpu_mem_limit`` at 85 % of VRAM so the arena never grabs
    everything.
    """
    import os
    forced = os.environ.get("CODERECON_ONNX_DEVICE", "").lower()
    if forced == "cpu":
        return ["CPUExecutionProvider"]
    # Ensure CUDA libs are findable before querying providers
    _ensure_cuda_lib_path()
    available = set(ort.get_available_providers())
    providers: list[str | tuple[str, dict[str, Any]]] = []
    # Prefer CUDA > ROCm > CoreML > CPU
    if "CUDAExecutionProvider" in available:
        cuda_opts: dict[str, Any] = {
            # kSameAsRequested = 1: allocate exactly the amount needed,
            # not next-power-of-two.  Prevents the arena from claiming
            # all VRAM on a single large allocation attempt.
            "arena_extend_strategy": "kSameAsRequested",
        }
        if vram_bytes:
            cuda_opts["gpu_mem_limit"] = int(vram_bytes * 0.85)
        providers.append(("CUDAExecutionProvider", cuda_opts))
    if "ROCMExecutionProvider" in available:
        providers.append("ROCMExecutionProvider")
    if "CoreMLExecutionProvider" in available:
        providers.append("CoreMLExecutionProvider")
    # Always include CPU as fallback
    providers.append("CPUExecutionProvider")
    return providers

def is_gpu_active() -> bool:
    """Return True if the last ONNX session loaded a GPU provider.
    Only valid after at least one encoder has been loaded.
    """
    return _gpu_active is True

# ── Constants ────────────────────────────────────────────────────

MODEL_VERSION = "splade-mini-onnx-v1"
BATCH_SIZE_CPU = 16
BATCH_SIZE_GPU = 64
# Resolved at first encoder load
BATCH_SIZE = BATCH_SIZE_CPU

# ── Adaptive GPU batch sizing ────────────────────────────────────
#
# Empirical: splade-mini (distilbert-6L-768H) peak VRAM per sample
# at a given sequence length.  Derived from observed OOM:
#   55 samples × 340 tokens → 9.1 GB CUDA allocation request
#   → ~165 MB/sample at 340 tokens → ~486 KB/sample/token.
# The quadratic attention term means this *overestimates* for short
# sequences, but conservative is correct — OOM fallback handles outliers.
_VRAM_BYTES_PER_SAMPLE_PER_TOKEN = 500_000  # 500 KB — measured from CUDA allocation at 55 samples × 340 tokens
_VRAM_MODEL_OVERHEAD_BYTES = 2500 * BYTES_PER_MB  # 2.5 GB  (model + ORT runtime + arena fragmentation)
_VRAM_UTILIZATION = 0.90  # use at most 90% of total VRAM

def _query_gpu_vram_bytes() -> int | None:
    """Query total GPU VRAM in bytes via nvidia-smi.
    Returns None if nvidia-smi is unavailable or fails.
    """
    import subprocess
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output is in MiB, one line per GPU — take the first
            mib = int(result.stdout.strip().split("\n")[0])
            return mib * BYTES_PER_MB
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
        log.debug("nvidia_smi_query_failed", exc_info=True)
    return None

def _compute_gpu_batch_size(max_seq_len: int, vram_bytes: int) -> int:
    """Derive a safe batch size for *max_seq_len* tokens given *vram_bytes* of GPU memory."""
    usable = int(vram_bytes * _VRAM_UTILIZATION) - _VRAM_MODEL_OVERHEAD_BYTES
    if usable <= 0:
        return 1
    bytes_per_sample = max(1, max_seq_len) * _VRAM_BYTES_PER_SAMPLE_PER_TOKEN
    batch = max(1, usable // bytes_per_sample)
    return min(batch, BATCH_SIZE_GPU)

# Vendored ONNX model + tokenizer (from coderecon-models-splade package)
from coderecon_models_splade import ONNX_PATH as _ONNX_PATH
from coderecon_models_splade import TOKENIZER_PATH as _TOKENIZER_PATH

# ── Encoder wrapper ──────────────────────────────────────────────

@dataclass
class SpladeEncoder:
    """ONNX-based SPLADE encoder (splade-mini via onnxruntime + tokenizers).
    No torch dependency.  Loads the vendored ONNX model and tokenizer
    from the package data directory.
    """
    onnx_path: Path = _ONNX_PATH
    tokenizer_path: Path = _TOKENIZER_PATH
    _session: ort.InferenceSession | None = field(default=None, repr=False)
    _tokenizer: Tokenizer | None = field(default=None, repr=False)
    _cpu_session: ort.InferenceSession | None = field(default=None, repr=False)
    _vram_bytes: int | None = field(default=None, repr=False)
    def _make_gpu_session(
        self,
        vram_bytes: int | None = None,
    ) -> ort.InferenceSession:
        """Create a CUDA InferenceSession with arena-safe settings.
        Separated from :meth:`load` so we can build a *fresh* session
        after an OOM (the BFC arena resets with a new session).
        """
        opts = ort.SessionOptions()
        opts.enable_mem_pattern = False
        # Suppress BFC arena fallback warnings — the allocator
        # intentionally falls back to system malloc when a single
        # allocation exceeds the arena's free capacity.
        opts.log_severity_level = 3  # Error
        providers = _select_onnx_providers(vram_bytes=vram_bytes)
        return ort.InferenceSession(
            str(self.onnx_path),
            sess_options=opts,
            providers=providers,
        )
    @staticmethod
    def _validate_model_batch_axis(onnx_path: Path) -> None:
        """Verify the ONNX model has a dynamic batch axis on its output.
        Models with a hardcoded batch dimension (e.g. ``[1, seq, vocab]``)
        cause CUDA buffer-reuse failures when batch > 1.  Fail fast with
        a clear message instead of surfacing a cryptic shape-mismatch
        error at inference time.
        """
        try:
            import onnx
        except ImportError:
            return
        try:
            model = onnx.load(str(onnx_path), load_external_data=False)
        except (OSError, RuntimeError, ValueError):
            log.debug("onnx_model_load_failed", exc_info=True)
            return
        for out in model.graph.output:
            dims = out.type.tensor_type.shape.dim
            if not dims:
                continue
            first = dims[0]
            if first.dim_value and first.dim_value > 0:
                raise RuntimeError(
                    f"SPLADE ONNX model has hardcoded batch dimension "
                    f"({first.dim_value}) on output '{out.name}'. "
                    f"The model must use a dynamic batch axis (dim_param). "
                    f"Re-export the model or update the coderecon-models-splade package."
                )
    def load(self) -> None:
        """Load ONNX session + tokenizer (lazy, auto-detect GPU)."""
        global _gpu_active, BATCH_SIZE  # noqa: PLW0603
        if self._session is not None:
            return
        self._validate_model_batch_axis(self.onnx_path)
        # Query VRAM *before* session creation so we can configure
        # gpu_mem_limit on the first session.
        self._vram_bytes = _query_gpu_vram_bytes()
        self._session = self._make_gpu_session(vram_bytes=self._vram_bytes)
        active = self._session.get_providers()
        _gpu_active = any(p != "CPUExecutionProvider" for p in active)
        if _gpu_active:
            BATCH_SIZE = BATCH_SIZE_GPU
            if self._vram_bytes:
                log.info(
                    "splade.gpu_vram",
                    extra={"vram_mib": self._vram_bytes // BYTES_PER_MB},
                )
        log.info("splade.loaded", extra={"providers": active, "gpu": _gpu_active})
        self._tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()
    def _encode_batch(
        self,
        texts: list[str],
        session: ort.InferenceSession | None = None,
    ) -> list[dict[int, float]]:
        """Run a batch of texts through the ONNX model → sparse vectors.
        The ONNX model has SPLADE pooling (ReLU → log1p → max-over-seq)
        baked into the graph, outputting (batch, vocab_size) directly.
        *session* overrides the default session (used by CPU fallback).
        """
        sess = session or self._session
        if sess is None or self._tokenizer is None:
            raise RuntimeError("SPLADE model not loaded: call load() first")
        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        tids = np.zeros_like(ids)
        (raw,) = sess.run(
            None,
            {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": tids,
            },
        )
        # SPLADE pooling: ReLU → log1p → max-over-sequence
        if raw.ndim == 3:
            pooled = np.log1p(np.maximum(raw, 0)).max(axis=1)
        else:
            pooled = raw  # already (batch, vocab_size)
        results: list[dict[int, float]] = []
        for row in pooled:
            nz = np.nonzero(row)[0]
            results.append({int(idx): float(row[idx]) for idx in nz})
        return results
    def _get_cpu_session(self) -> ort.InferenceSession:
        """Lazily create a CPU-only ONNX session for OOM fallback."""
        if self._cpu_session is None:
            opts = ort.SessionOptions()
            opts.enable_mem_pattern = False
            # Suppress BFC arena fallback warnings — the CPU arena
            # intentionally falls back to system malloc when a single
            # allocation exceeds the arena's free capacity.
            opts.log_severity_level = 3  # Error
            self._cpu_session = ort.InferenceSession(
                str(self.onnx_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
        return self._cpu_session
    def _encode_batch_safe(self, texts: list[str]) -> list[dict[int, float]]:
        """Encode with OOM recovery: fresh GPU session, then CPU fallback.
        On a CUDA OOM error the BFC arena's internal free-lists may be
        fragmented.  Retrying on the *same* session often cascades into
        repeated failures even for smaller batches.
        Strategy:
        1. Try the batch on the current GPU session.
        2. On OOM → create a **fresh** GPU session (new BFC arena),
           halve the batch, and retry on the fresh session.
        3. If a single item still OOMs on a fresh GPU session → CPU.
        """
        try:
            return self._encode_batch(texts)
        except (RuntimeError, MemoryError) as exc:
            err_msg = str(exc).lower()
            is_oom = (
                "out of memory" in err_msg
                or "failed to allocate memory" in err_msg
                or "smaller than requested" in err_msg
            )
            # ONNX buffer reuse fails when batch/sequence dims change
            # between runs.  Treat as transient and halve → CPU-fallback.
            is_shape = "shape mismatch" in err_msg
            if not _gpu_active or not (is_oom or is_shape):
                raise
            log.warning(
                "splade.gpu_oom",
                extra={"batch_size": len(texts), "error": str(exc)[:200]},
            )
            # Replace the current session with a fresh one (resets BFC arena)
            self._session = self._make_gpu_session(vram_bytes=self._vram_bytes)
            if len(texts) > 1:
                mid = len(texts) // 2
                return self._encode_batch_safe(texts[:mid]) + self._encode_batch_safe(
                    texts[mid:]
                )
            # Single item OOM on GPU — fall back to CPU
            log.warning("splade.cpu_fallback", extra={"text_len": len(texts[0])})
            return self._encode_batch(texts, session=self._get_cpu_session())
    def encode_documents(self, texts: list[str], batch_size: int = BATCH_SIZE) -> list[dict[int, float]]:
        """Encode document texts → sparse vectors (batched ONNX inference).
        Sorts texts by tokenized length before batching.  When GPU is
        active and VRAM is known, batch size is computed per-chunk from
        the longest sequence in that chunk so that the estimated peak
        memory stays within VRAM.  OOM errors are caught and recovered
        by halving the batch, with a final CPU fallback for single items.
        """
        self.load()
        if not texts:
            return []
        if self._tokenizer is None:
            raise RuntimeError("SPLADE tokenizer not loaded after load()")
        # Sort by token count → similar lengths in each batch → less padding
        indexed = list(enumerate(texts))
        tok_lengths = [len(self._tokenizer.encode(t).ids) for _, t in indexed]
        indexed.sort(key=lambda x: tok_lengths[x[0]])
        ordered_vecs: list[tuple[int, dict[int, float]]] = []
        pos = 0
        batch_num = 0
        encode_t0 = time.monotonic()
        while pos < len(indexed):
            remaining = len(indexed) - pos
            if _gpu_active and self._vram_bytes:
                # Items are sorted ascending by length.  The longest in a
                # candidate batch of size N is at pos+N-1.  Use the longest
                # in the maximum possible batch to derive a safe batch size.
                max_possible = min(remaining, BATCH_SIZE_GPU)
                longest_seq = tok_lengths[indexed[pos + max_possible - 1][0]]
                bs = max(1, min(max_possible, _compute_gpu_batch_size(longest_seq, self._vram_bytes)))
            else:
                bs = min(remaining, batch_size)
            batch_items = indexed[pos : pos + bs]
            batch_texts = [t for _, t in batch_items]
            bt0 = time.monotonic()
            batch_vecs = self._encode_batch_safe(batch_texts) if _gpu_active else self._encode_batch(batch_texts)
            bt1 = time.monotonic()
            batch_num += 1
            log.info(
                "splade.batch batch=%d size=%d pos=%d/%d longest_tok=%d elapsed=%.3fs cumul=%.1fs",
                batch_num, bs, pos, len(indexed),
                tok_lengths[batch_items[-1][0]],
                bt1 - bt0, bt1 - encode_t0,
            )
            for (orig_idx, _), vec in zip(batch_items, batch_vecs):
                ordered_vecs.append((orig_idx, vec))
            pos += bs
        # Restore original order
        ordered_vecs.sort(key=lambda x: x[0])
        return [vec for _, vec in ordered_vecs]
    def encode_queries(self, texts: list[str], batch_size: int = BATCH_SIZE) -> list[dict[int, float]]:
        """Encode query texts → sparse vectors.
        For splade-mini there is no separate query encoder; the same
        model is used for both documents and queries.
        """
        return self.encode_documents(texts, batch_size=batch_size)
def sparse_dot(a: dict[int, float], b: dict[int, float]) -> float:
    """Dot product of two sparse vectors."""
    if len(a) > len(b):
        a, b = b, a
    total = 0.0
    for idx, val in a.items():
        if idx in b:
            total += val * b[idx]
    return total

# ── Batch index + storage ────────────────────────────────────────

_encoder_singleton: SpladeEncoder | None = None

def _get_encoder() -> SpladeEncoder:
    """Get or create the singleton encoder."""
    global _encoder_singleton
    if _encoder_singleton is None:
        _encoder_singleton = SpladeEncoder()
    return _encoder_singleton

def _vec_to_json(vec: dict[int, float]) -> str:
    """Compact JSON serialisation of a sparse vector."""
    return json.dumps({str(k): round(v, 4) for k, v in vec.items()})

def _json_to_vec(blob: str) -> dict[int, float]:
    """Deserialise a sparse vector from JSON."""
    raw = json.loads(blob)
    return {int(k): float(v) for k, v in raw.items()}

import struct

_PAIR_FMT = "If"  # uint32 term_id + float32 weight
_PAIR_SIZE = struct.calcsize(_PAIR_FMT)

def _vec_to_blob(vec: dict[int, float]) -> bytes:
    """Pack a sparse vector as binary: sequence of (uint32, float32) pairs."""
    parts = []
    for k, v in vec.items():
        parts.append(struct.pack(_PAIR_FMT, k, v))
    return b"".join(parts)

def _blob_to_vec(data: bytes) -> dict[int, float]:
    """Unpack a binary sparse vector to dict."""
    n_pairs = len(data) // _PAIR_SIZE
    result: dict[int, float] = {}
    for i in range(n_pairs):
        tid, w = struct.unpack_from(_PAIR_FMT, data, i * _PAIR_SIZE)
        result[tid] = w
    return result

# ── Re-exports for backward compatibility ────────────────────────

from coderecon.index._internal.indexing.splade_db import (  # noqa: E402, F401
    backfill_scaffold_text,
    index_splade_vectors,
    load_all_vectors_fast,
    retrieve_splade,
)
from coderecon.index._internal.indexing.splade_scaffold import (  # noqa: E402, F401
    _compact_sig,
    _path_to_phrase,
    build_def_scaffold,
    build_scaffolds_for_defs,
    word_split,
)
