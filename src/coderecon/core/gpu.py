"""GPU hardware detection and ONNX Runtime provider guidance.

Detects whether an NVIDIA or AMD GPU is physically present, and whether
the matching ``onnxruntime-gpu`` provider is installed.  Used by the init
flow to prompt the user when a GPU exists but the runtime lacks support.
"""

from __future__ import annotations

import structlog
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum

log = structlog.get_logger(__name__)


class GpuVendor(Enum):
    NVIDIA = "nvidia"
    AMD = "amd"


@dataclass
class GpuProbeResult:
    """Result of probing for GPU hardware + ONNX Runtime support."""

    detected_gpus: list[GpuVendor] = field(default_factory=list)
    onnx_gpu_providers: list[str] = field(default_factory=list)

    @property
    def has_gpu(self) -> bool:
        return len(self.detected_gpus) > 0

    @property
    def has_onnx_gpu(self) -> bool:
        return len(self.onnx_gpu_providers) > 0

    @property
    def gpu_available_but_not_configured(self) -> bool:
        return self.has_gpu and not self.has_onnx_gpu

    @property
    def install_hint(self) -> str | None:
        """Return the install command for the missing provider, or None."""
        if not self.gpu_available_but_not_configured:
            return None
        if GpuVendor.NVIDIA in self.detected_gpus:
            if sys.platform == "darwin":
                return None
            if sys.platform == "win32":
                return (
                    "Install the CUDA Toolkit from https://developer.nvidia.com "
                    "then: pip install --force-reinstall onnxruntime-gpu"
                )
            return "pip install coderecon[gpu]"
        if GpuVendor.AMD in self.detected_gpus:
            return "pip install onnxruntime-rocm"
        return None

    @property
    def provider_name(self) -> str | None:
        """Human-readable name of the GPU provider that would be used."""
        if GpuVendor.NVIDIA in self.detected_gpus:
            return "CUDA"
        if GpuVendor.AMD in self.detected_gpus:
            return "ROCm"
        return None


def probe_gpu() -> GpuProbeResult:
    """Detect GPU hardware and check ONNX Runtime provider availability.

    Hardware detection uses ``nvidia-smi`` / ``rocm-smi`` presence and
    exit codes (no Python GPU libraries required).

    ONNX provider detection uses ``onnxruntime.get_available_providers()``.
    """
    result = GpuProbeResult()

    # --- Hardware detection via CLI tools ---
    if _check_nvidia_gpu():
        result.detected_gpus.append(GpuVendor.NVIDIA)
    if _check_amd_gpu():
        result.detected_gpus.append(GpuVendor.AMD)

    # --- ONNX Runtime provider detection ---
    try:
        from coderecon.index._internal.indexing.splade import _ensure_cuda_lib_path

        _ensure_cuda_lib_path()
        import onnxruntime as ort

        available = set(ort.get_available_providers())
        for provider in ("CUDAExecutionProvider", "ROCMExecutionProvider", "CoreMLExecutionProvider"):
            if provider in available:
                result.onnx_gpu_providers.append(provider)
    except (ImportError, OSError, RuntimeError):
        log.debug("gpu.ort_probe_failed", exc_info=True)

    return result


def _check_nvidia_gpu() -> bool:
    """Return True if an NVIDIA GPU is detected via nvidia-smi."""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0 and len(proc.stdout.strip()) > 0
    except (OSError, subprocess.SubprocessError):
        return False


def _check_amd_gpu() -> bool:
    """Return True if an AMD GPU is detected via rocm-smi."""
    if shutil.which("rocm-smi") is None:
        return False
    try:
        proc = subprocess.run(
            ["rocm-smi", "--showid"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0 and len(proc.stdout.strip()) > 0
    except (OSError, subprocess.SubprocessError):
        return False
