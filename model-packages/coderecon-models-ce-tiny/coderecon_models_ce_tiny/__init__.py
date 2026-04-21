"""TinyBERT-L-2-v2 cross-encoder ONNX model for coderecon fast reranking."""

from __future__ import annotations

from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"
ONNX_PATH = MODELS_DIR / "model.onnx"
TOKENIZER_PATH = MODELS_DIR / "tokenizer.json"
