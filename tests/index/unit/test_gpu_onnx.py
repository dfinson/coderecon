"""Tests for GPU-related features in splade.py — provider selection,
LD_LIBRARY_PATH auto-patching, adaptive batch size, and is_gpu_active().
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import coderecon.index._internal.indexing.splade as splade_mod
from coderecon.index._internal.indexing.splade import (
    BATCH_SIZE_CPU,
    BATCH_SIZE_GPU,
    _ensure_cuda_lib_path,
    _select_onnx_providers,
    is_gpu_active,
)


# ── _select_onnx_providers tests ─────────────────────────────────


class TestSelectOnnxProviders:
    def test_cpu_only(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
            providers = _select_onnx_providers()
        assert providers == ["CPUExecutionProvider"]

    def test_cuda_preferred_over_cpu(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        assert providers[0] == "CUDAExecutionProvider"
        assert "CPUExecutionProvider" in providers

    def test_rocm_included(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "ROCMExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        assert "ROCMExecutionProvider" in providers
        assert "CPUExecutionProvider" in providers

    def test_coreml_included(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CoreMLExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        assert "CoreMLExecutionProvider" in providers

    def test_cuda_before_rocm_before_coreml(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CoreMLExecutionProvider",
                "ROCMExecutionProvider",
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        # Order should be CUDA, ROCM, CoreML, CPU regardless of ORT order
        assert providers.index("CUDAExecutionProvider") < providers.index("ROCMExecutionProvider")
        assert providers.index("ROCMExecutionProvider") < providers.index("CoreMLExecutionProvider")
        assert providers[-1] == "CPUExecutionProvider"

    def test_forced_cpu_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODERECON_ONNX_DEVICE", "cpu")
        providers = _select_onnx_providers()
        assert providers == ["CPUExecutionProvider"]

    def test_forced_cpu_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODERECON_ONNX_DEVICE", "CPU")
        providers = _select_onnx_providers()
        assert providers == ["CPUExecutionProvider"]

    def test_unknown_env_value_does_not_force_cpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODERECON_ONNX_DEVICE", "auto")
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        assert "CUDAExecutionProvider" in providers

    def test_cpu_always_included_as_fallback(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = ["CUDAExecutionProvider"]
            providers = _select_onnx_providers()
        assert "CPUExecutionProvider" in providers


# ── _ensure_cuda_lib_path tests ──────────────────────────────────


class TestEnsureCudaLibPath:
    def test_noop_on_non_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        original = os.environ.get("LD_LIBRARY_PATH", "")
        _ensure_cuda_lib_path()
        assert os.environ.get("LD_LIBRARY_PATH", "") == original

    def test_noop_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        original = os.environ.get("LD_LIBRARY_PATH", "")
        _ensure_cuda_lib_path()
        assert os.environ.get("LD_LIBRARY_PATH", "") == original

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-specific test")
    def test_adds_pip_package_lib_dirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Create fake nvidia package dirs
        cudnn_lib = tmp_path / "nvidia" / "cudnn" / "lib"
        cudnn_lib.mkdir(parents=True)
        cublas_lib = tmp_path / "nvidia" / "cublas" / "lib"
        cublas_lib.mkdir(parents=True)

        # Mock the nvidia packages in sys.modules
        fake_cudnn = MagicMock()
        fake_cudnn.__path__ = [str(tmp_path / "nvidia" / "cudnn")]
        fake_cublas = MagicMock()
        fake_cublas.__path__ = [str(tmp_path / "nvidia" / "cublas")]
        fake_nvrtc = MagicMock()
        fake_nvrtc.__path__ = [str(tmp_path / "nvidia" / "cuda_nvrtc")]  # no lib dir

        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
        with patch.dict(
            "sys.modules",
            {
                "nvidia.cudnn": fake_cudnn,
                "nvidia.cublas": fake_cublas,
                "nvidia.cuda_nvrtc": fake_nvrtc,
            },
        ):
            _ensure_cuda_lib_path()

        ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        assert str(cudnn_lib) in ld_path
        assert str(cublas_lib) in ld_path

        # Clean up
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-specific test")
    def test_does_not_duplicate_existing_paths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cudnn_lib = tmp_path / "nvidia" / "cudnn" / "lib"
        cudnn_lib.mkdir(parents=True)

        fake_cudnn = MagicMock()
        fake_cudnn.__path__ = [str(tmp_path / "nvidia" / "cudnn")]

        monkeypatch.setenv("LD_LIBRARY_PATH", str(cudnn_lib))
        with patch.dict("sys.modules", {"nvidia.cudnn": fake_cudnn}):
            _ensure_cuda_lib_path()

        ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        # Should appear exactly once
        assert ld_path.count(str(cudnn_lib)) == 1

        # Clean up
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-specific test")
    def test_preserves_existing_ld_library_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cudnn_lib = tmp_path / "nvidia" / "cudnn" / "lib"
        cudnn_lib.mkdir(parents=True)

        fake_cudnn = MagicMock()
        fake_cudnn.__path__ = [str(tmp_path / "nvidia" / "cudnn")]

        monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/lib")
        with patch.dict("sys.modules", {"nvidia.cudnn": fake_cudnn}):
            _ensure_cuda_lib_path()

        ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        assert "/usr/local/lib" in ld_path
        assert str(cudnn_lib) in ld_path

        # Clean up
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-specific test")
    def test_no_nvidia_packages_no_system_paths_no_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

        # Remove any real nvidia modules from sys.modules temporarily
        # and ensure __import__ raises for nvidia.* packages
        removed = {}
        for key in list(sys.modules):
            if key.startswith("nvidia."):
                removed[key] = sys.modules.pop(key)

        try:
            # Also patch system path checks so they don't find real CUDA
            with patch(
                "coderecon.index._internal.indexing.splade.Path"
            ) as mock_path_cls:
                mock_instance = MagicMock()
                mock_instance.exists.return_value = False
                mock_instance.is_dir.return_value = False
                mock_instance.__truediv__ = lambda self, other: mock_instance
                mock_path_cls.return_value = mock_instance
                # The function uses Path(mod.__path__[0]) / "lib" internally
                # but since nvidia packages won't be importable, it won't reach that
                _ensure_cuda_lib_path()
        finally:
            sys.modules.update(removed)

        assert os.environ.get("LD_LIBRARY_PATH", "") == ""


# ── Adaptive batch size tests ────────────────────────────────────


class TestAdaptiveBatchSize:
    def test_default_batch_size_is_cpu(self) -> None:
        assert BATCH_SIZE_CPU == 16
        assert BATCH_SIZE_GPU == 64

    def test_gpu_active_before_load_returns_false(self) -> None:
        # Reset global state
        original = splade_mod._gpu_active
        try:
            splade_mod._gpu_active = None
            assert is_gpu_active() is False
        finally:
            splade_mod._gpu_active = original

    def test_is_gpu_active_true_when_set(self) -> None:
        original = splade_mod._gpu_active
        try:
            splade_mod._gpu_active = True
            assert is_gpu_active() is True
        finally:
            splade_mod._gpu_active = original

    def test_is_gpu_active_false_when_cpu(self) -> None:
        original = splade_mod._gpu_active
        try:
            splade_mod._gpu_active = False
            assert is_gpu_active() is False
        finally:
            splade_mod._gpu_active = original

    def test_batch_size_set_to_gpu_when_gpu_active(self) -> None:
        """Verify SpladeEncoder.load() sets BATCH_SIZE to GPU value when GPU provider is active."""
        from coderecon.index._internal.indexing.splade import SpladeEncoder

        original_batch = splade_mod.BATCH_SIZE
        original_gpu = splade_mod._gpu_active

        mock_session = MagicMock()
        mock_session.get_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        mock_tokenizer = MagicMock()

        try:
            with (
                patch("coderecon.index._internal.indexing.splade._select_onnx_providers") as mock_providers,
                patch("coderecon.index._internal.indexing.splade.ort") as mock_ort,
                patch("coderecon.index._internal.indexing.splade.Tokenizer") as mock_tok_cls,
            ):
                mock_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                mock_ort.InferenceSession.return_value = mock_session
                mock_ort.SessionOptions.return_value = MagicMock()
                mock_tok_cls.from_file.return_value = mock_tokenizer

                enc = SpladeEncoder.__new__(SpladeEncoder)
                enc._session = None
                enc._tokenizer = None
                enc.onnx_path = Path("/fake/model.onnx")
                enc.tokenizer_path = Path("/fake/tokenizer.json")

                enc.load()

            assert splade_mod.BATCH_SIZE == BATCH_SIZE_GPU
            assert splade_mod._gpu_active is True
        finally:
            splade_mod.BATCH_SIZE = original_batch
            splade_mod._gpu_active = original_gpu

    def test_batch_size_stays_cpu_when_no_gpu(self) -> None:
        """Verify SpladeEncoder.load() keeps BATCH_SIZE at CPU value when CPU-only."""
        from coderecon.index._internal.indexing.splade import SpladeEncoder

        original_batch = splade_mod.BATCH_SIZE
        original_gpu = splade_mod._gpu_active

        mock_session = MagicMock()
        mock_session.get_providers.return_value = ["CPUExecutionProvider"]

        mock_tokenizer = MagicMock()

        try:
            with (
                patch("coderecon.index._internal.indexing.splade._select_onnx_providers") as mock_providers,
                patch("coderecon.index._internal.indexing.splade.ort") as mock_ort,
                patch("coderecon.index._internal.indexing.splade.Tokenizer") as mock_tok_cls,
            ):
                mock_providers.return_value = ["CPUExecutionProvider"]
                mock_ort.InferenceSession.return_value = mock_session
                mock_ort.SessionOptions.return_value = MagicMock()
                mock_tok_cls.from_file.return_value = mock_tokenizer

                enc = SpladeEncoder.__new__(SpladeEncoder)
                enc._session = None
                enc._tokenizer = None
                enc.onnx_path = Path("/fake/model.onnx")
                enc.tokenizer_path = Path("/fake/tokenizer.json")

                splade_mod.BATCH_SIZE = BATCH_SIZE_CPU
                enc.load()

            assert splade_mod.BATCH_SIZE == BATCH_SIZE_CPU
            assert splade_mod._gpu_active is False
        finally:
            splade_mod.BATCH_SIZE = original_batch
            splade_mod._gpu_active = original_gpu

    def test_load_is_idempotent(self) -> None:
        """Calling load() twice should not create a second session."""
        from coderecon.index._internal.indexing.splade import SpladeEncoder

        original_gpu = splade_mod._gpu_active

        mock_session = MagicMock()
        mock_session.get_providers.return_value = ["CPUExecutionProvider"]

        try:
            with (
                patch("coderecon.index._internal.indexing.splade._select_onnx_providers") as mock_providers,
                patch("coderecon.index._internal.indexing.splade.ort") as mock_ort,
                patch("coderecon.index._internal.indexing.splade.Tokenizer") as mock_tok_cls,
            ):
                mock_providers.return_value = ["CPUExecutionProvider"]
                mock_ort.InferenceSession.return_value = mock_session
                mock_ort.SessionOptions.return_value = MagicMock()
                mock_tok_cls.from_file.return_value = MagicMock()

                enc = SpladeEncoder.__new__(SpladeEncoder)
                enc._session = None
                enc._tokenizer = None
                enc.onnx_path = Path("/fake/model.onnx")
                enc.tokenizer_path = Path("/fake/tokenizer.json")

                enc.load()
                enc.load()  # second call

            # InferenceSession should only be created once
            assert mock_ort.InferenceSession.call_count == 1
        finally:
            splade_mod._gpu_active = original_gpu
