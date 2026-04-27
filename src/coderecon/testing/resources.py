"""Cross-platform memory budget and history for test execution.

Uses ``psutil`` (Linux, macOS, Windows) for all memory queries.
No platform-specific code paths.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import structlog

from coderecon.config.constants import BYTES_PER_MB
from coderecon.files.ops import atomic_write_text

log = structlog.get_logger(__name__)

_DEFAULT_RESERVE_MB = 1024  # 1 GB — leaves headroom for IDE + OS
_MIN_SUBPROCESS_CEILING_MB = 128  # floor for ceiling_mb to avoid starving subprocesses

# Patterns emitted by runtimes on OOM. Matched against stderr.
_OOM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"java\.lang\.OutOfMemoryError", re.IGNORECASE),
    re.compile(r"FATAL ERROR:.*Allocation failed", re.IGNORECASE),
    re.compile(r"OutOfMemoryException", re.IGNORECASE),
    re.compile(r"Cannot allocate memory", re.IGNORECASE),
    re.compile(r"memory allocation.*failed", re.IGNORECASE),
    re.compile(r"Killed\b", re.IGNORECASE),  # OOM-killer message on Linux
    re.compile(r"GOMEMLIMIT", re.IGNORECASE),
    re.compile(r"runtime: out of memory", re.IGNORECASE),
]

class MemoryBudget:
    """Cross-platform memory budget using ``psutil``.

    All queries delegate to ``psutil.virtual_memory()`` which works
    identically on Linux (``/proc/meminfo``), macOS (``vm_stat``/
    ``sysctl``), and Windows (``GlobalMemoryStatusEx``).
    """

    def __init__(self, reserve_mb: int = _DEFAULT_RESERVE_MB) -> None:
        self._reserve_bytes = reserve_mb * BYTES_PER_MB

    def available_mb(self) -> int:
        """Available memory in MB (kernel estimate)."""
        return int(psutil.virtual_memory().available // BYTES_PER_MB)

    def can_launch(self) -> bool:
        """True if available memory exceeds the reserve threshold."""
        return psutil.virtual_memory().available > self._reserve_bytes

    def ceiling_mb(self) -> int:
        """Max MB any single subprocess should use (available − reserve)."""
        raw = psutil.virtual_memory().available - self._reserve_bytes
        return max(int(raw // BYTES_PER_MB), _MIN_SUBPROCESS_CEILING_MB)  # floor avoids starving subprocesses

def child_rss_mb(pid: int) -> int:
    """Sum of RSS (MB) for *pid* and all its descendants.

    Returns 0 if the process no longer exists.
    """
    try:
        proc = psutil.Process(pid)
        total = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                structlog.get_logger().debug("child_process_memory_unavailable", exc_info=True)
                pass
        return int(total // BYTES_PER_MB)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0

def classify_oom(
    exit_code: int | None,
    stderr: str,
    peak_rss_mb: int,
    ceiling_mb: int,
) -> bool:
    """Heuristic: did this subprocess die from memory exhaustion?

    Combines exit code, stderr patterns, and RSS-near-ceiling check.
    """
    if exit_code is None or exit_code == 0:
        return False

    # Linux OOM-kill: SIGKILL = 137 (128+9); macOS similar
    if exit_code in (137, -9, 9):
        return True

    # RSS was >= 80 % of the ceiling we set
    if ceiling_mb > 0 and peak_rss_mb >= int(ceiling_mb * 0.8):
        return True

    # Runtime printed an OOM message
    return any(pat.search(stderr) for pat in _OOM_PATTERNS)

# Persistent per-repo history

_HISTORY_FILENAME = "test_memory_profile.json"

@dataclass
class _TargetProfile:
    peak_rss_mb: int = 0
    last_run_ts: float = 0.0
    oom_count: int = 0

@dataclass
class MemoryHistory:
    """Simple JSON-backed per-target peak-RSS history.

    Stored at ``<repo_root>/.recon/test_memory_profile.json``.
    Thread-safe enough for a single async event loop (no file locking).
    """

    _path: Path
    _data: dict[str, _TargetProfile] = field(default_factory=dict)

    @classmethod
    def for_repo(cls, repo_root: Path) -> MemoryHistory:
        path = repo_root / ".recon" / _HISTORY_FILENAME
        inst = cls(_path=path)
        inst._load()
        return inst

    def estimate_mb(self, target_id: str) -> int | None:
        """Last observed peak RSS for *target_id*, or ``None``."""
        p = self._data.get(target_id)
        return p.peak_rss_mb if p else None

    def oom_count(self, target_id: str) -> int:
        return self._data.get(target_id, _TargetProfile()).oom_count

    def record(self, target_id: str, peak_rss_mb: int) -> None:
        p = self._data.setdefault(target_id, _TargetProfile())
        p.peak_rss_mb = peak_rss_mb
        p.last_run_ts = time.time()
        self._save()

    def record_oom(self, target_id: str, peak_rss_mb: int) -> None:
        p = self._data.setdefault(target_id, _TargetProfile())
        p.peak_rss_mb = peak_rss_mb
        p.last_run_ts = time.time()
        p.oom_count += 1
        self._save()

    # -- persistence --

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            for tid, d in raw.items():
                self._data[tid] = _TargetProfile(
                    peak_rss_mb=d.get("peak_rss_mb", 0),
                    last_run_ts=d.get("last_run_ts", 0.0),
                    oom_count=d.get("oom_count", 0),
                )
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            log.debug("memory_history.load_failed", exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                tid: {
                    "peak_rss_mb": p.peak_rss_mb,
                    "last_run_ts": p.last_run_ts,
                    "oom_count": p.oom_count,
                }
                for tid, p in self._data.items()
            }
            atomic_write_text(self._path, json.dumps(payload, indent=2) + "\n")
        except OSError:
            log.debug("memory_history.save_failed", exc_info=True)
