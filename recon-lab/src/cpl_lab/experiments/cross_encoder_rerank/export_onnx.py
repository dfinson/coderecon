"""Export cross-encoder/ms-marco-MiniLM-L-6-v2 to ONNX format.

Produces two files ready for vendoring into coderecon:
  - model.onnx       (~90 MB)
  - tokenizer.json   (~700 KB)

Requirements (recon-lab only, NOT needed in coderecon):
  - torch
  - transformers
  - tokenizers

Usage
-----
    uv run python -m cpl_lab.experiments.cross_encoder_rerank.export_onnx [--output-dir DIR]

Or via the recon-lab CLI:
    uv run recon-lab ce-export [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

log = logging.getLogger(__name__)

MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEFAULT_OUTPUT_DIR = Path.home() / ".recon" / "recon-lab" / "exports" / "ce_minilm_l6"
OPSET_VERSION = 17
MAX_LENGTH = 512


def export(output_dir: Path | None = None) -> Path:
    """Export MiniLM-L-6-v2 to ONNX. Returns path to output directory."""
    out = output_dir or DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    onnx_path = out / "model.onnx"
    tokenizer_path = out / "tokenizer.json"

    # ── Load PyTorch model + tokenizer ────────────────────────
    log.info("Loading %s …", MODEL_ID)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

    # ── Save fast-tokenizer JSON (for tokenizers lib) ─────────
    tokenizer.backend_tokenizer.save(str(tokenizer_path))
    log.info("Wrote %s (%d KB)", tokenizer_path, tokenizer_path.stat().st_size // 1024)

    # ── Export ONNX ───────────────────────────────────────────
    dummy = tokenizer(
        "query text",
        "document text",
        return_tensors="pt",
        max_length=MAX_LENGTH,
        truncation=True,
        padding="max_length",
    )

    torch.onnx.export(
        model,
        (dummy["input_ids"], dummy["attention_mask"], dummy["token_type_ids"]),
        str(onnx_path),
        opset_version=OPSET_VERSION,
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["logits"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "seq"},
            "attention_mask": {0: "batch", 1: "seq"},
            "token_type_ids": {0: "batch", 1: "seq"},
            "logits": {0: "batch"},
        },
    )
    log.info("Wrote %s (%.1f MB)", onnx_path, onnx_path.stat().st_size / 1e6)

    # ── Verify round-trip ─────────────────────────────────────
    _verify(onnx_path, tokenizer_path)

    log.info("Export complete → %s", out)
    return out


def _verify(onnx_path: Path, tokenizer_path: Path) -> None:
    """Quick sanity check: ONNX output matches PyTorch output."""
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.enable_truncation(max_length=MAX_LENGTH)

    query = "rate limiting middleware"
    doc = "function check rate limit request limit in rate limiter calls get client ip"

    encoded = tok.encode(query, doc)
    ids = np.array([encoded.ids], dtype=np.int64)
    mask = np.array([encoded.attention_mask], dtype=np.int64)
    tids = np.array([encoded.type_ids], dtype=np.int64)

    (logits,) = session.run(None, {
        "input_ids": ids,
        "attention_mask": mask,
        "token_type_ids": tids,
    })

    score = float(logits[0, 0]) if logits.ndim == 2 else float(logits[0])
    log.info("Verification — sample score: %.4f (expect non-zero)", score)
    assert not np.isnan(score), "ONNX export produced NaN"
    log.info("Verification passed ✓")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Export MiniLM-L-6-v2 to ONNX")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()
    export(args.output_dir)


if __name__ == "__main__":
    main()
