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
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import onnxruntime as ort
from sqlalchemy import text
from sqlmodel import Session, col, select
from tokenizers import Tokenizer

from coderecon.index.models import (
    DefFact,
    File,
    SpladeVec,
)

if TYPE_CHECKING:
    from coderecon.index._internal.db.database import Database
    from coderecon.index._internal.indexing.graph import FactQueries

log = logging.getLogger(__name__)

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
            pass

    # 2. Well-known system CUDA paths
    if not lib_dirs:
        for candidate in (
            "/usr/local/cuda/lib64",
            "/usr/lib/x86_64-linux-gnu",
        ):
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
                pass
            break


def _select_onnx_providers() -> list[str]:
    """Select the best available ONNX Runtime execution providers.

    Tries GPU providers first (CUDA, ROCm, CoreML), falls back to CPU.
    Respects CODERECON_ONNX_DEVICE env var to force a specific provider.

    On Linux, auto-patches LD_LIBRARY_PATH for pip-installed CUDA libs.
    """
    import os

    forced = os.environ.get("CODERECON_ONNX_DEVICE", "").lower()
    if forced == "cpu":
        return ["CPUExecutionProvider"]

    # Ensure CUDA libs are findable before querying providers
    _ensure_cuda_lib_path()

    available = set(ort.get_available_providers())
    providers: list[str] = []

    # Prefer CUDA > ROCm > CoreML > CPU
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
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

# Vendored ONNX model + tokenizer (from coderecon-models-splade package)
from coderecon_models_splade import ONNX_PATH as _ONNX_PATH
from coderecon_models_splade import TOKENIZER_PATH as _TOKENIZER_PATH

# ── Identifier splitting ─────────────────────────────────────────

_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+")


def word_split(name: str) -> list[str]:
    """Split camelCase/snake_case into lowercase words."""
    words: list[str] = []
    for part in name.split("_"):
        if not part:
            continue
        camel = _CAMEL_SPLIT.findall(part)
        if camel:
            words.extend(w.lower() for w in camel)
        else:
            words.append(part.lower())
    return words


def _path_to_phrase(file_path: str) -> str:
    """Convert file path to natural-language phrase."""
    p = file_path.replace("\\", "/")
    for prefix in ("src/", "lib/", "app/", "pkg/", "internal/"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    dot = p.rfind(".")
    if dot > 0:
        p = p[:dot]
    parts: list[str] = []
    for segment in p.split("/"):
        parts.extend(word_split(segment))
    return " ".join(parts)


def _compact_sig(name: str, sig: str) -> str:
    """Build compact anglicised signature."""
    words = " ".join(word_split(name))
    if sig:
        compact = sig.replace("self, ", "").replace("self,", "").replace("self", "")
        if compact and compact != "()":
            return f"{words}{compact}"
    return words


_CODE_KINDS = frozenset(
    {
        "function",
        "method",
        "class",
        "struct",
        "interface",
        "trait",
        "enum",
        "property",
        "constant",
        "variable",
        "module",
    }
)


# ── Scaffold builder ─────────────────────────────────────────────


def build_def_scaffold(
    file_path: str,
    *,
    kind: str,
    name: str,
    signature_text: str | None = None,
    qualified_name: str | None = None,
    lexical_path: str | None = None,
    docstring: str | None = None,
    callee_names: list[str] | None = None,
    type_ref_names: list[str] | None = None,
) -> str:
    """Build an anglicised scaffold for a single DefFact.

    Fields present unconditionally.  Order follows measured marginal
    recall contribution from bge-small ablation.
    """
    if not name:
        return ""

    lines: list[str] = []

    path_phrase = _path_to_phrase(file_path)
    if path_phrase:
        lines.append(f"module {path_phrase}")

    sig = signature_text or ""
    if sig:
        lines.append(f"{kind} {_compact_sig(name, sig)}")
    else:
        lines.append(f"{kind} {' '.join(word_split(name))}")

    qualified = qualified_name or lexical_path or ""
    if qualified and "." in qualified:
        parent = qualified.rsplit(".", 1)[0]
        parent_words = " ".join(word_split(parent))
        if parent_words:
            lines.append(f"in {parent_words}")

    if callee_names:
        sorted_calls = sorted({c for c in callee_names if c and len(c) >= 2})
        if sorted_calls:
            lines.append(f"calls {', '.join(sorted_calls)}")

    if type_ref_names:
        callee_set = set(callee_names or [])
        unique_refs = sorted({r for r in type_ref_names if r and r not in callee_set})
        if unique_refs:
            lines.append(f"uses {', '.join(unique_refs)}")

    doc = (docstring or "").strip()
    if doc and len(doc) > 15:
        first = doc.split(".")[0].strip() if "." in doc else doc
        if first:
            lines.append(f"describes {first}")

    return "\n".join(lines) if lines else ""


# ── Scaffold extraction from index DB ─────────────────────────────


def build_scaffolds_for_defs(
    session: Session,
    def_facts: list[DefFact],
) -> dict[str, str]:
    """Build scaffolds for a batch of DefFacts using index data.

    Uses bulk queries for callees and type annotations instead of
    per-def queries — reduces ~2N SQL queries to 2 bulk queries.

    Returns {def_uid: scaffold_text}.
    """
    from coderecon.index.models import RefFact, TypeAnnotationFact

    result: dict[str, str] = {}
    if not def_facts:
        return result

    # Pre-fetch file paths for all defs
    file_ids = list({d.file_id for d in def_facts if d.file_id})
    file_map: dict[int, str] = {}
    if file_ids:
        files = session.exec(
            select(File).where(col(File.id).in_(file_ids))
        ).all()
        file_map = {f.id: f.path for f in files if f.id is not None}

    # ── Bulk callee query ────────────────────────────────────────
    # For each def, find resolved refs whose start_line falls within
    # [def.start_line, def.end_line] in the same file, then join to
    # the target DefFact to get the callee name.
    #
    # Instead of N individual queries, we do one query that returns
    # (caller_def_uid, callee_name, callee_def_uid) for all defs.

    # Build a lookup: (file_id, start_line, end_line) → def_uid
    callees_by_uid: dict[str, list[str]] = {d.def_uid: [] for d in def_facts}
    type_refs_by_uid: dict[str, list[str]] = {d.def_uid: [] for d in def_facts}

    # Process in chunks of file_ids to keep SQL manageable
    for fid_chunk_start in range(0, len(file_ids), 100):
        fid_chunk = file_ids[fid_chunk_start:fid_chunk_start + 100]

        # Get defs in this chunk for range-matching
        chunk_defs = [d for d in def_facts if d.file_id in set(fid_chunk)]

        # Bulk fetch all resolved refs in these files
        refs_with_targets = session.exec(
            select(
                RefFact.file_id,
                RefFact.start_line,
                RefFact.target_def_uid,
            ).where(
                col(RefFact.file_id).in_(fid_chunk),
                RefFact.target_def_uid.is_not(None),  # type: ignore[union-attr]
            )
        ).all()

        # Fetch target def names in bulk
        target_uids = list({r[2] for r in refs_with_targets if r[2]})
        target_names: dict[str, str] = {}
        for uid_start in range(0, len(target_uids), 500):
            uid_batch = target_uids[uid_start:uid_start + 500]
            rows = session.exec(
                select(DefFact.def_uid, DefFact.name).where(
                    col(DefFact.def_uid).in_(uid_batch)
                )
            ).all()
            for uid, name in rows:
                target_names[uid] = name

        # Assign refs to their enclosing defs by range containment
        for d in chunk_defs:
            d_callees: set[str] = set()
            for fid, line, tuid in refs_with_targets:
                if (
                    fid == d.file_id
                    and d.start_line <= line <= d.end_line
                    and tuid != d.def_uid
                    and tuid in target_names
                ):
                    d_callees.add(target_names[tuid])
            callees_by_uid[d.def_uid] = list(d_callees)[:30]

        # Bulk fetch type annotations in these files
        try:
            annotations = session.exec(
                select(
                    TypeAnnotationFact.file_id,
                    TypeAnnotationFact.start_line,
                    TypeAnnotationFact.base_type,
                ).where(
                    col(TypeAnnotationFact.file_id).in_(fid_chunk),
                )
            ).all()

            for d in chunk_defs:
                d_types: set[str] = set()
                for fid, line, btype in annotations:
                    if (
                        fid == d.file_id
                        and btype
                        and d.start_line <= line <= d.end_line
                    ):
                        d_types.add(btype)
                type_refs_by_uid[d.def_uid] = list(d_types)[:20]
        except Exception:
            pass

    # ── Build scaffolds ──────────────────────────────────────────
    for d in def_facts:
        file_path = file_map.get(d.file_id, "")
        if not file_path:
            continue

        scaffold = build_def_scaffold(
            file_path,
            kind=d.kind,
            name=d.name,
            signature_text=d.signature_text,
            qualified_name=d.qualified_name,
            lexical_path=d.lexical_path,
            docstring=d.docstring,
            callee_names=callees_by_uid.get(d.def_uid),
            type_ref_names=type_refs_by_uid.get(d.def_uid),
        )
        if scaffold:
            result[d.def_uid] = scaffold

    return result


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

    def load(self) -> None:
        """Load ONNX session + tokenizer (lazy, auto-detect GPU)."""
        global _gpu_active, BATCH_SIZE  # noqa: PLW0603
        if self._session is not None:
            return
        opts = ort.SessionOptions()
        # Disable memory pattern pre-allocation so varying batch sizes
        # don't hit "shape mismatch attempting to re-use buffer" errors.
        opts.enable_mem_pattern = False
        providers = _select_onnx_providers()
        self._session = ort.InferenceSession(
            str(self.onnx_path),
            sess_options=opts,
            providers=providers,
        )
        active = self._session.get_providers()
        _gpu_active = any(p != "CPUExecutionProvider" for p in active)
        if _gpu_active:
            BATCH_SIZE = BATCH_SIZE_GPU
        log.info("splade.loaded", extra={"providers": active, "gpu": _gpu_active})
        self._tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()

    def _encode_batch(self, texts: list[str]) -> list[dict[int, float]]:
        """Run a batch of texts through the ONNX model → sparse vectors.

        The ONNX model has SPLADE pooling (ReLU → log1p → max-over-seq)
        baked into the graph, outputting (batch, vocab_size) directly.
        """
        assert self._session is not None and self._tokenizer is not None

        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        tids = np.zeros_like(ids)

        (pooled,) = self._session.run(
            None,
            {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": tids,
            },
        )  # (batch, vocab_size)

        results: list[dict[int, float]] = []
        for row in pooled:
            nz = np.nonzero(row)[0]
            results.append({int(idx): float(row[idx]) for idx in nz})
        return results

    def encode_documents(self, texts: list[str], batch_size: int = BATCH_SIZE) -> list[dict[int, float]]:
        """Encode document texts → sparse vectors (batched ONNX inference).

        Sorts texts by tokenized length before batching to minimise
        padding waste, then restores original order.
        """
        self.load()
        if not texts:
            return []
        assert self._tokenizer is not None

        # Sort by token count → similar lengths in each batch → less padding
        indexed = list(enumerate(texts))
        indexed.sort(key=lambda x: len(self._tokenizer.encode(x[1]).ids))

        ordered_vecs: list[tuple[int, dict[int, float]]] = []
        for i in range(0, len(indexed), batch_size):
            batch_items = indexed[i : i + batch_size]
            batch_texts = [t for _, t in batch_items]
            batch_vecs = self._encode_batch(batch_texts)
            for (orig_idx, _), vec in zip(batch_items, batch_vecs):
                ordered_vecs.append((orig_idx, vec))

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


# ── Vector I/O ───────────────────────────────────────────────────


def load_all_vectors_fast(
    db: "Database",
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
    progress_cb: Any | None = None,
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
    log.info("splade.encode_start", extra={"n_defs": len(texts)})
    t0 = time.monotonic()
    all_vecs = encoder.encode_documents(texts, batch_size=BATCH_SIZE)
    elapsed = time.monotonic() - t0
    log.info(
        "splade.encode_done",
        extra={
            "n_defs": len(texts),
            "elapsed_s": round(elapsed, 1),
            "throughput": round(len(texts) / elapsed, 1) if elapsed > 0 else 0,
        },
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

        for uid, vec in zip(uid_order, all_vecs):
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
            if progress_cb and stored % 500 == 0:
                progress_cb(stored, len(uid_order))

        session.commit()

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


def remove_splade_vectors_for_files(
    db: Database,
    file_ids: list[int],
) -> int:
    """Remove SPLADE vectors for defs in the given files."""
    if not file_ids:
        return 0

    with db.session() as session:
        # Get def_uids for these files
        def_uids = list(
            session.exec(
                select(DefFact.def_uid).where(col(DefFact.file_id).in_(file_ids))
            ).all()
        )
        if not def_uids:
            return 0

        removed = 0
        for uid in def_uids:
            existing = session.get(SpladeVec, uid)
            if existing is not None:
                session.delete(existing)
                removed += 1
        session.commit()

    return removed


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
