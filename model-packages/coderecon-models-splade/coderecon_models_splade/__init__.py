"""SPLADE-mini ONNX model for coderecon sparse retrieval."""

from __future__ import annotations

from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"
ONNX_PATH = MODELS_DIR / "model.onnx"
TOKENIZER_PATH = MODELS_DIR / "tokenizer.json"
