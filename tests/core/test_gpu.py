"""Tests for coderecon.core.gpu — GPU hardware detection and guidance."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from coderecon.core.gpu import (
    GpuProbeResult,
    GpuVendor,
    _check_amd_gpu,
    _check_nvidia_gpu,
    probe_gpu,
)


# ── GpuProbeResult property tests ────────────────────────────────


class TestGpuProbeResult:
    def test_empty_result_has_no_gpu(self) -> None:
        r = GpuProbeResult()
        assert r.has_gpu is False
        assert r.has_onnx_gpu is False
        assert r.gpu_available_but_not_configured is False
        assert r.install_hint is None
        assert r.provider_name is None

    def test_nvidia_detected_without_onnx(self) -> None:
        r = GpuProbeResult(detected_gpus=[GpuVendor.NVIDIA])
        assert r.has_gpu is True
        assert r.has_onnx_gpu is False
        assert r.gpu_available_but_not_configured is True
        assert r.provider_name == "CUDA"

    def test_nvidia_with_onnx_cuda(self) -> None:
        r = GpuProbeResult(
            detected_gpus=[GpuVendor.NVIDIA],
            onnx_gpu_providers=["CUDAExecutionProvider"],
        )
        assert r.has_gpu is True
        assert r.has_onnx_gpu is True
        assert r.gpu_available_but_not_configured is False
        assert r.install_hint is None

    def test_amd_detected_without_onnx(self) -> None:
        r = GpuProbeResult(detected_gpus=[GpuVendor.AMD])
        assert r.has_gpu is True
        assert r.gpu_available_but_not_configured is True
        assert r.provider_name == "ROCm"

    def test_amd_install_hint(self) -> None:
        r = GpuProbeResult(detected_gpus=[GpuVendor.AMD])
        assert r.install_hint == "pip install onnxruntime-rocm"

    def test_nvidia_install_hint_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        r = GpuProbeResult(detected_gpus=[GpuVendor.NVIDIA])
        assert r.install_hint == "pip install coderecon[gpu]"

    def test_nvidia_install_hint_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        r = GpuProbeResult(detected_gpus=[GpuVendor.NVIDIA])
        hint = r.install_hint
        assert hint is not None
        assert "CUDA Toolkit" in hint
        assert "nvidia.com" in hint
        assert "onnxruntime-gpu" in hint

    def test_nvidia_install_hint_macos_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        r = GpuProbeResult(detected_gpus=[GpuVendor.NVIDIA])
        assert r.install_hint is None

    def test_both_gpus_nvidia_takes_priority(self) -> None:
        r = GpuProbeResult(detected_gpus=[GpuVendor.NVIDIA, GpuVendor.AMD])
        assert r.provider_name == "CUDA"


# ── Hardware detection tests ─────────────────────────────────────


class TestCheckNvidiaGpu:
    def test_nvidia_smi_not_found(self) -> None:
        with patch("coderecon.core.gpu.shutil.which", return_value=None):
            assert _check_nvidia_gpu() is False

    def test_nvidia_smi_found_returns_true(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"NVIDIA RTX A500 Laptop GPU\n"
        with (
            patch("coderecon.core.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("coderecon.core.gpu.subprocess.run", return_value=mock_proc),
        ):
            assert _check_nvidia_gpu() is True

    def test_nvidia_smi_fails(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = b""
        with (
            patch("coderecon.core.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("coderecon.core.gpu.subprocess.run", return_value=mock_proc),
        ):
            assert _check_nvidia_gpu() is False

    def test_nvidia_smi_empty_output(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b""
        with (
            patch("coderecon.core.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("coderecon.core.gpu.subprocess.run", return_value=mock_proc),
        ):
            assert _check_nvidia_gpu() is False

    def test_nvidia_smi_timeout(self) -> None:
        with (
            patch("coderecon.core.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch(
                "coderecon.core.gpu.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5),
            ),
        ):
            assert _check_nvidia_gpu() is False


class TestCheckAmdGpu:
    def test_rocm_smi_not_found(self) -> None:
        with patch("coderecon.core.gpu.shutil.which", return_value=None):
            assert _check_amd_gpu() is False

    def test_rocm_smi_found_returns_true(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"GPU[0] : 0x7310\n"
        with (
            patch("coderecon.core.gpu.shutil.which", return_value="/usr/bin/rocm-smi"),
            patch("coderecon.core.gpu.subprocess.run", return_value=mock_proc),
        ):
            assert _check_amd_gpu() is True

    def test_rocm_smi_fails(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = b""
        with (
            patch("coderecon.core.gpu.shutil.which", return_value="/usr/bin/rocm-smi"),
            patch("coderecon.core.gpu.subprocess.run", return_value=mock_proc),
        ):
            assert _check_amd_gpu() is False


# ── probe_gpu integration tests ──────────────────────────────────


class TestProbeGpu:
    def test_no_gpu_no_ort(self) -> None:
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
        with (
            patch("coderecon.core.gpu._check_nvidia_gpu", return_value=False),
            patch("coderecon.core.gpu._check_amd_gpu", return_value=False),
            patch(
                "coderecon.index._internal.indexing.splade._ensure_cuda_lib_path",
            ),
            patch.dict("sys.modules", {"onnxruntime": mock_ort}),
        ):
            result = probe_gpu()
            assert result.has_gpu is False
            assert result.has_onnx_gpu is False

    def test_nvidia_gpu_with_cuda_provider(self) -> None:
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ]
        with (
            patch("coderecon.core.gpu._check_nvidia_gpu", return_value=True),
            patch("coderecon.core.gpu._check_amd_gpu", return_value=False),
            patch(
                "coderecon.index._internal.indexing.splade._ensure_cuda_lib_path",
            ),
            patch.dict("sys.modules", {"onnxruntime": mock_ort}),
        ):
            result = probe_gpu()
            assert result.has_gpu is True
            assert "CUDAExecutionProvider" in result.onnx_gpu_providers

    def test_nvidia_gpu_without_cuda_provider(self) -> None:
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
        with (
            patch("coderecon.core.gpu._check_nvidia_gpu", return_value=True),
            patch("coderecon.core.gpu._check_amd_gpu", return_value=False),
            patch(
                "coderecon.index._internal.indexing.splade._ensure_cuda_lib_path",
            ),
            patch.dict("sys.modules", {"onnxruntime": mock_ort}),
        ):
            result = probe_gpu()
            assert result.has_gpu is True
            assert result.has_onnx_gpu is False
            assert result.gpu_available_but_not_configured is True

    def test_ort_import_failure_handled_gracefully(self) -> None:
        with (
            patch("coderecon.core.gpu._check_nvidia_gpu", return_value=True),
            patch("coderecon.core.gpu._check_amd_gpu", return_value=False),
            patch(
                "coderecon.index._internal.indexing.splade._ensure_cuda_lib_path",
                side_effect=ImportError("no ort"),
            ),
        ):
            result = probe_gpu()
            assert result.has_gpu is True
            # Should not crash, just report no ONNX GPU
            assert result.has_onnx_gpu is False

    def test_probe_calls_ensure_cuda_lib_path(self) -> None:
        mock_ensure = MagicMock()
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
        with (
            patch("coderecon.core.gpu._check_nvidia_gpu", return_value=False),
            patch("coderecon.core.gpu._check_amd_gpu", return_value=False),
            patch(
                "coderecon.index._internal.indexing.splade._ensure_cuda_lib_path",
                mock_ensure,
            ),
        ):
            probe_gpu()
            mock_ensure.assert_called_once()
