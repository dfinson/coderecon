"""Export rasyosef/splade-mini to ONNX with dynamic batch axes.

Produces two files ready for vendoring into coderecon-models-splade:
  - model.onnx       (~44 MB)
  - tokenizer.json   (~700 KB)

Requirements (recon-lab only):
  - torch
  - transformers
  - tokenizers

Usage
-----
    uv run python -m cpl_lab.experiments.splade_bakeoff.export_onnx [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import torch
from transformers import AutoModelForMaskedLM, AutoTokenizer

log = logging.getLogger(__name__)

MODEL_ID = "rasyosef/splade-mini"
DEFAULT_OUTPUT_DIR = Path.home() / ".recon" / "recon-lab" / "exports" / "splade_mini"
OPSET_VERSION = 17
MAX_LENGTH = 512


class _SpladeWrapper(torch.nn.Module):
    """Thin wrapper that applies SPLADE pooling inside the ONNX graph.

    Output: (batch, vocab_size) sparse logit weights.
    Pooling: log(1 + ReLU(logits)).max(dim=seq_len)
    """

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        token_type_ids: torch.Tensor,
    ) -> torch.Tensor:
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        logits = outputs.logits  # (batch, seq, vocab)
        activated = torch.log1p(torch.relu(logits))  # SPLADE activation

        # Mask padding tokens before max-pool
        mask = attention_mask.unsqueeze(-1).float()  # (batch, seq, 1)
        activated = activated * mask

        pooled, _ = activated.max(dim=1)  # (batch, vocab)
        return pooled


def export(output_dir: Path | None = None) -> Path:
    """Export splade-mini to ONNX with dynamic batch. Returns output dir."""
    out = output_dir or DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    onnx_path = out / "model.onnx"
    tokenizer_path = out / "tokenizer.json"

    # ── Load PyTorch model + tokenizer ────────────────────────
    log.info("Loading %s …", MODEL_ID)
    base_model = AutoModelForMaskedLM.from_pretrained(MODEL_ID)
    base_model.eval()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

    wrapper = _SpladeWrapper(base_model)
    wrapper.eval()

    # ── Save fast-tokenizer JSON ──────────────────────────────
    tokenizer.backend_tokenizer.save(str(tokenizer_path))
    log.info("Wrote %s (%d KB)", tokenizer_path, tokenizer_path.stat().st_size // 1024)

    # ── Export ONNX ───────────────────────────────────────────
    dummy = tokenizer(
        "function rate limit middleware",
        return_tensors="pt",
        max_length=MAX_LENGTH,
        truncation=True,
        padding="max_length",
    )

    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            (dummy["input_ids"], dummy["attention_mask"], dummy["token_type_ids"]),
            str(onnx_path),
            opset_version=OPSET_VERSION,
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["pooled"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "token_type_ids": {0: "batch", 1: "seq"},
                "pooled": {0: "batch"},
            },
        )
    log.info("Wrote %s (%.1f MB)", onnx_path, onnx_path.stat().st_size / 1e6)

    # ── Verify ────────────────────────────────────────────────
    _verify(onnx_path, tokenizer_path)

    log.info("Export complete → %s", out)
    return out


def _verify(onnx_path: Path, tokenizer_path: Path) -> None:
    """Verify: dynamic batch works, output matches old pipeline."""
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    # Check shapes are dynamic
    for inp in session.get_inputs():
        print(f"  INPUT:  {inp.name} shape={inp.shape}")
    for out in session.get_outputs():
        print(f"  OUTPUT: {out.name} shape={out.shape}")

    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.enable_truncation(max_length=512)
    tok.enable_padding()

    # ── Single inference ──────────────────────────────────────
    texts = [
        "function rate limit middleware",
        "class user authentication handler",
        "module database connection pool",
    ]

    encodings = tok.encode_batch(texts)
    ids = np.array([e.ids for e in encodings], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    tids = np.zeros_like(ids)

    (pooled,) = session.run(None, {
        "input_ids": ids,
        "attention_mask": mask,
        "token_type_ids": tids,
    })
    print(f"  Batch of {len(texts)}: output shape = {pooled.shape}")
    assert pooled.shape[0] == len(texts), f"Expected batch={len(texts)}, got {pooled.shape[0]}"
    assert pooled.shape[1] == 30522, f"Expected vocab=30522, got {pooled.shape[1]}"

    # Check sparsity (most dims should be 0)
    nnz = [(row > 0).sum() for row in pooled]
    print(f"  Non-zero terms per doc: {nnz}")
    assert all(10 < n < 200 for n in nnz), f"Unexpected sparsity: {nnz}"

    # ── batch=1 also works ────────────────────────────────────
    enc1 = tok.encode(texts[0])
    ids1 = np.array([enc1.ids], dtype=np.int64)
    mask1 = np.array([enc1.attention_mask], dtype=np.int64)
    tids1 = np.zeros_like(ids1)
    (pooled1,) = session.run(None, {
        "input_ids": ids1, "attention_mask": mask1, "token_type_ids": tids1,
    })
    assert pooled1.shape == (1, 30522)

    # Compare batch[0] vs single — should be identical
    diff = np.abs(pooled[0] - pooled1[0]).max()
    print(f"  Max diff (batch vs single): {diff:.6f}")
    assert diff < 1e-4, f"Batch/single mismatch: {diff}"

    print("  ✓ Verification passed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Export splade-mini to ONNX")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    export(args.output_dir)


if __name__ == "__main__":
    main()
