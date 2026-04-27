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
    _compute_gpu_batch_size,
    _ensure_cuda_lib_path,
    _query_gpu_vram_bytes,
    _select_onnx_providers,
    is_gpu_active,
)


# ── _select_onnx_providers tests ─────────────────────────────────


def _provider_name(entry: str | tuple) -> str:
    """Extract provider name from a string or (name, opts) tuple."""
    return entry[0] if isinstance(entry, tuple) else entry

def _provider_names(providers: list) -> list[str]:
    return [_provider_name(p) for p in providers]

class TestSelectOnnxProviders:
    def test_cpu_only(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
            providers = _select_onnx_providers()
        assert _provider_names(providers) == ["CPUExecutionProvider"]
    def test_cuda_preferred_over_cpu(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        names = _provider_names(providers)
        assert names[0] == "CUDAExecutionProvider"
        assert "CPUExecutionProvider" in names
    def test_cuda_gets_arena_config(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        # CUDA entry should be a (name, opts) tuple
        assert isinstance(providers[0], tuple)
        name, opts = providers[0]
        assert name == "CUDAExecutionProvider"
        assert opts["arena_extend_strategy"] == "kSameAsRequested"
    def test_cuda_with_vram_sets_gpu_mem_limit(self) -> None:
        vram = 4 * 1024**3
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers(vram_bytes=vram)
        _, opts = providers[0]
        assert opts["gpu_mem_limit"] == int(vram * 0.85)
    def test_rocm_included(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "ROCMExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        names = _provider_names(providers)
        assert "ROCMExecutionProvider" in names
        assert "CPUExecutionProvider" in names
    def test_coreml_included(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CoreMLExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        names = _provider_names(providers)
        assert "CoreMLExecutionProvider" in names
    def test_cuda_before_rocm_before_coreml(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = [
                "CoreMLExecutionProvider",
                "ROCMExecutionProvider",
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = _select_onnx_providers()
        names = _provider_names(providers)
        assert names.index("CUDAExecutionProvider") < names.index("ROCMExecutionProvider")
        assert names.index("ROCMExecutionProvider") < names.index("CoreMLExecutionProvider")
        assert names[-1] == "CPUExecutionProvider"
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
        names = _provider_names(providers)
        assert "CUDAExecutionProvider" in names
    def test_cpu_always_included_as_fallback(self) -> None:
        with patch("coderecon.index._internal.indexing.splade.ort") as mock_ort:
            mock_ort.get_available_providers.return_value = ["CUDAExecutionProvider"]
            providers = _select_onnx_providers()
        names = _provider_names(providers)
        assert "CPUExecutionProvider" in names

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
                patch.object(SpladeEncoder, "_make_gpu_session", return_value=mock_session),
                patch("coderecon.index._internal.indexing.splade._query_gpu_vram_bytes", return_value=4 * 1024**3),
                patch("coderecon.index._internal.indexing.splade.Tokenizer") as mock_tok_cls,
            ):
                mock_tok_cls.from_file.return_value = mock_tokenizer
                enc = SpladeEncoder.__new__(SpladeEncoder)
                enc._session = None
                enc._tokenizer = None
                enc._cpu_session = None
                enc._vram_bytes = None
                enc.onnx_path = Path("/fake/model.onnx")
                enc.tokenizer_path = Path("/fake/tokenizer.json")
                enc.load()
            assert splade_mod.BATCH_SIZE == BATCH_SIZE_GPU
            assert splade_mod._gpu_active is True
            assert enc._vram_bytes == 4 * 1024**3
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
                patch.object(SpladeEncoder, "_make_gpu_session", return_value=mock_session),
                patch("coderecon.index._internal.indexing.splade._query_gpu_vram_bytes", return_value=None),
                patch("coderecon.index._internal.indexing.splade.Tokenizer") as mock_tok_cls,
            ):
                mock_tok_cls.from_file.return_value = mock_tokenizer
                enc = SpladeEncoder.__new__(SpladeEncoder)
                enc._session = None
                enc._tokenizer = None
                enc._cpu_session = None
                enc._vram_bytes = None
                enc.onnx_path = Path("/fake/model.onnx")
                enc.tokenizer_path = Path("/fake/tokenizer.json")
                splade_mod.BATCH_SIZE = BATCH_SIZE_CPU
                enc.load()
            assert splade_mod.BATCH_SIZE == BATCH_SIZE_CPU
            assert splade_mod._gpu_active is False
            assert enc._vram_bytes is None
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
                patch.object(SpladeEncoder, "_make_gpu_session", return_value=mock_session) as mock_make,
                patch("coderecon.index._internal.indexing.splade._query_gpu_vram_bytes", return_value=None),
                patch("coderecon.index._internal.indexing.splade.Tokenizer") as mock_tok_cls,
            ):
                mock_tok_cls.from_file.return_value = MagicMock()
                enc = SpladeEncoder.__new__(SpladeEncoder)
                enc._session = None
                enc._tokenizer = None
                enc._cpu_session = None
                enc._vram_bytes = None
                enc.onnx_path = Path("/fake/model.onnx")
                enc.tokenizer_path = Path("/fake/tokenizer.json")
                enc.load()
                enc.load()  # second call
            # _make_gpu_session should only be called once
            assert mock_make.call_count == 1
        finally:
            splade_mod._gpu_active = original_gpu

# ── _query_gpu_vram_bytes tests ─────────────────────────────────


class TestQueryGpuVram:
    def test_returns_bytes_from_nvidia_smi(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="4096\n")
            result = _query_gpu_vram_bytes()
        assert result == 4096 * 1024 * 1024
    def test_returns_none_when_nvidia_smi_missing(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _query_gpu_vram_bytes() is None
    def test_returns_none_on_nonzero_exit(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert _query_gpu_vram_bytes() is None
    def test_multi_gpu_takes_first(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="8192\n4096\n")
            result = _query_gpu_vram_bytes()
        assert result == 8192 * 1024 * 1024

# ── _compute_gpu_batch_size tests ───────────────────────────────


class TestComputeGpuBatchSize:
    VRAM_4GB = 4 * 1024 * 1024 * 1024
    def test_short_sequences_get_large_batch(self) -> None:
        bs = _compute_gpu_batch_size(32, self.VRAM_4GB)
        assert bs == BATCH_SIZE_GPU  # capped at max
    def test_long_sequences_get_small_batch(self) -> None:
        bs = _compute_gpu_batch_size(512, self.VRAM_4GB)
        assert 1 <= bs < BATCH_SIZE_GPU
    def test_340_tokens_4gb_avoids_oom(self) -> None:
        bs = _compute_gpu_batch_size(340, self.VRAM_4GB)
        assert bs < 55  # 55 was the OOM batch size
    def test_always_at_least_one(self) -> None:
        bs = _compute_gpu_batch_size(512, 100 * 1024 * 1024)  # 100 MB VRAM
        assert bs >= 1
    def test_capped_at_batch_size_gpu(self) -> None:
        bs = _compute_gpu_batch_size(1, 32 * 1024**3)  # 32 GB
        assert bs == BATCH_SIZE_GPU
    def test_scales_inversely_with_seq_len(self) -> None:
        bs_short = _compute_gpu_batch_size(128, self.VRAM_4GB)
        bs_long = _compute_gpu_batch_size(512, self.VRAM_4GB)
        assert bs_short > bs_long

# ── OOM fallback tests ─────────────────────────────────────────


class TestOomFallback:
    def test_encode_batch_safe_halves_on_oom(self) -> None:
        """When GPU OOMs on a batch, it should create a fresh session, halve, and retry."""
        from coderecon.index._internal.indexing.splade import SpladeEncoder
        original_gpu = splade_mod._gpu_active
        try:
            splade_mod._gpu_active = True
            enc = SpladeEncoder.__new__(SpladeEncoder)
            enc._session = MagicMock()
            enc._tokenizer = MagicMock()
            enc._cpu_session = None
            enc._vram_bytes = 4 * 1024**3
            enc.onnx_path = Path("/fake/model.onnx")
            enc.tokenizer_path = Path("/fake/tokenizer.json")
            fresh_session = MagicMock()
            enc._make_gpu_session = MagicMock(return_value=fresh_session)
            call_count = 0
            def mock_encode(texts, session=None):
                nonlocal call_count
                call_count += 1
                if len(texts) > 2:
                    raise RuntimeError("CUDA error: out of memory")
                return [{1: 0.5}] * len(texts)
            enc._encode_batch = mock_encode
            result = enc._encode_batch_safe(["a", "b", "c", "d"])
            assert len(result) == 4
            assert all(r == {1: 0.5} for r in result)
            # Fresh session should have been created on OOM
            enc._make_gpu_session.assert_called()
        finally:
            splade_mod._gpu_active = original_gpu
    def test_encode_batch_safe_cpu_fallback_for_single(self) -> None:
        """When a single item OOMs on GPU, fall back to CPU session."""
        from coderecon.index._internal.indexing.splade import SpladeEncoder
        original_gpu = splade_mod._gpu_active
        try:
            splade_mod._gpu_active = True
            enc = SpladeEncoder.__new__(SpladeEncoder)
            enc._session = MagicMock()
            enc._tokenizer = MagicMock()
            enc._cpu_session = None
            enc._vram_bytes = 4 * 1024**3
            enc.onnx_path = Path("/fake/model.onnx")
            enc.tokenizer_path = Path("/fake/tokenizer.json")
            cpu_session = MagicMock()
            fresh_gpu = MagicMock()
            enc._make_gpu_session = MagicMock(return_value=fresh_gpu)
            def mock_encode(texts, session=None):
                if session is cpu_session:
                    return [{2: 0.9}]
                raise RuntimeError("CUDA error: out of memory")
            enc._encode_batch = mock_encode
            enc._get_cpu_session = lambda: cpu_session
            result = enc._encode_batch_safe(["long text"])
            assert result == [{2: 0.9}]
        finally:
            splade_mod._gpu_active = original_gpu
    def test_non_oom_errors_propagate(self) -> None:
        """Non-OOM errors should not be caught."""
        from coderecon.index._internal.indexing.splade import SpladeEncoder
        original_gpu = splade_mod._gpu_active
        try:
            splade_mod._gpu_active = True
            enc = SpladeEncoder.__new__(SpladeEncoder)
            enc._session = MagicMock()
            enc._tokenizer = MagicMock()
            enc._cpu_session = None
            enc._vram_bytes = 4 * 1024**3
            enc.onnx_path = Path("/fake/model.onnx")
            enc.tokenizer_path = Path("/fake/tokenizer.json")
            def mock_encode(texts, session=None):
                raise ValueError("bad input")
            enc._encode_batch = mock_encode
            with pytest.raises(ValueError, match="bad input"):
                enc._encode_batch_safe(["text"])
        finally:
            splade_mod._gpu_active = original_gpu
