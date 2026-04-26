"""Tests for testing.resources — MemoryBudget, MemoryHistory, classify_oom."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from coderecon.testing.resources import (
    MemoryBudget,
    MemoryHistory,
    child_rss_mb,
    classify_oom,
)


# =============================================================================
# MemoryBudget
# =============================================================================


class TestMemoryBudget:
    """Tests for cross-platform MemoryBudget."""

    def _mock_vmem(self, available_mb: int) -> MagicMock:
        vm = MagicMock()
        vm.available = available_mb * 1024 * 1024
        return vm

    def test_available_mb(self) -> None:
        budget = MemoryBudget(reserve_mb=1024)
        with patch("coderecon.testing.resources.psutil") as mock_ps:
            mock_ps.virtual_memory.return_value = self._mock_vmem(4096)
            assert budget.available_mb() == 4096

    def test_can_launch_true_when_above_reserve(self) -> None:
        budget = MemoryBudget(reserve_mb=1024)
        with patch("coderecon.testing.resources.psutil") as mock_ps:
            mock_ps.virtual_memory.return_value = self._mock_vmem(2000)
            assert budget.can_launch() is True

    def test_can_launch_false_when_below_reserve(self) -> None:
        budget = MemoryBudget(reserve_mb=1024)
        with patch("coderecon.testing.resources.psutil") as mock_ps:
            mock_ps.virtual_memory.return_value = self._mock_vmem(512)
            assert budget.can_launch() is False

    def test_can_launch_false_when_exactly_at_reserve(self) -> None:
        budget = MemoryBudget(reserve_mb=1024)
        with patch("coderecon.testing.resources.psutil") as mock_ps:
            mock_ps.virtual_memory.return_value = self._mock_vmem(1024)
            assert budget.can_launch() is False

    def test_ceiling_mb_normal(self) -> None:
        budget = MemoryBudget(reserve_mb=1024)
        with patch("coderecon.testing.resources.psutil") as mock_ps:
            mock_ps.virtual_memory.return_value = self._mock_vmem(4096)
            assert budget.ceiling_mb() == 3072  # 4096 - 1024

    def test_ceiling_mb_floor_at_128(self) -> None:
        budget = MemoryBudget(reserve_mb=1024)
        with patch("coderecon.testing.resources.psutil") as mock_ps:
            mock_ps.virtual_memory.return_value = self._mock_vmem(500)
            # 500 - 1024 = negative, so floor at 128
            assert budget.ceiling_mb() == 128

    def test_custom_reserve(self) -> None:
        budget = MemoryBudget(reserve_mb=2048)
        with patch("coderecon.testing.resources.psutil") as mock_ps:
            mock_ps.virtual_memory.return_value = self._mock_vmem(6000)
            assert budget.ceiling_mb() == 3952  # 6000 - 2048


# =============================================================================
# child_rss_mb
# =============================================================================


class TestChildRssMb:
    """Tests for child RSS measurement."""

    def test_returns_zero_for_nonexistent_pid(self) -> None:
        # PID 999999999 should not exist
        assert child_rss_mb(999999999) == 0

    def test_returns_rss_for_current_process(self) -> None:
        import os

        rss = child_rss_mb(os.getpid())
        # Current Python process should use at least some memory
        assert rss > 0

    def test_handles_access_denied(self) -> None:
        import psutil

        with patch("coderecon.testing.resources.psutil.Process") as mock_proc:
            mock_proc.side_effect = psutil.AccessDenied(pid=1)
            assert child_rss_mb(1) == 0


# =============================================================================
# classify_oom
# =============================================================================


class TestClassifyOom:
    """Tests for OOM classification heuristic."""

    def test_exit_code_zero_is_not_oom(self) -> None:
        assert classify_oom(0, "", 500, 1024) is False

    def test_exit_code_none_is_not_oom(self) -> None:
        assert classify_oom(None, "", 500, 1024) is False

    def test_sigkill_137_is_oom(self) -> None:
        assert classify_oom(137, "", 100, 1024) is True

    def test_sigkill_minus9_is_oom(self) -> None:
        assert classify_oom(-9, "", 100, 1024) is True

    def test_rss_near_ceiling_is_oom(self) -> None:
        # 900 >= 0.8 * 1024 = 819
        assert classify_oom(1, "", 900, 1024) is True

    def test_rss_well_below_ceiling_not_oom(self) -> None:
        assert classify_oom(1, "", 200, 1024) is False

    def test_java_oom_in_stderr(self) -> None:
        assert classify_oom(1, "java.lang.OutOfMemoryError: heap space", 100, 1024) is True

    def test_node_oom_in_stderr(self) -> None:
        assert classify_oom(
            1, "FATAL ERROR: CALL_AND_RETRY_LAST Allocation failed", 100, 1024
        ) is True

    def test_dotnet_oom_in_stderr(self) -> None:
        assert classify_oom(1, "System.OutOfMemoryException thrown", 100, 1024) is True

    def test_go_oom_in_stderr(self) -> None:
        assert classify_oom(1, "runtime: out of memory", 100, 1024) is True

    def test_killed_in_stderr(self) -> None:
        assert classify_oom(1, "Killed", 100, 1024) is True

    def test_normal_test_failure_not_oom(self) -> None:
        assert classify_oom(1, "FAILED test_something - AssertionError", 200, 1024) is False

    def test_zero_ceiling_no_rss_check(self) -> None:
        # ceiling=0 should not trigger RSS-based check
        assert classify_oom(1, "", 500, 0) is False


# =============================================================================
# MemoryHistory
# =============================================================================


class TestMemoryHistory:
    """Tests for persistent per-target RSS history."""

    def test_empty_history_returns_none(self, tmp_path: Path) -> None:
        h = MemoryHistory.for_repo(tmp_path)
        assert h.estimate_mb("foo") is None

    def test_record_and_retrieve(self, tmp_path: Path) -> None:
        h = MemoryHistory.for_repo(tmp_path)
        h.record("target-a", 512)
        assert h.estimate_mb("target-a") == 512

    def test_oom_count_increments(self, tmp_path: Path) -> None:
        h = MemoryHistory.for_repo(tmp_path)
        assert h.oom_count("target-a") == 0
        h.record_oom("target-a", 1024)
        assert h.oom_count("target-a") == 1
        h.record_oom("target-a", 2048)
        assert h.oom_count("target-a") == 2

    def test_record_updates_peak(self, tmp_path: Path) -> None:
        h = MemoryHistory.for_repo(tmp_path)
        h.record("t1", 100)
        h.record("t1", 200)
        assert h.estimate_mb("t1") == 200

    def test_persists_to_disk(self, tmp_path: Path) -> None:
        h1 = MemoryHistory.for_repo(tmp_path)
        h1.record("x", 999)
        h1.record_oom("y", 1500)

        # Load fresh from disk
        h2 = MemoryHistory.for_repo(tmp_path)
        assert h2.estimate_mb("x") == 999
        assert h2.estimate_mb("y") == 1500
        assert h2.oom_count("y") == 1

    def test_json_format(self, tmp_path: Path) -> None:
        h = MemoryHistory.for_repo(tmp_path)
        h.record("tgt", 256)

        path = tmp_path / ".recon" / "test_memory_profile.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "tgt" in data
        assert data["tgt"]["peak_rss_mb"] == 256
        assert data["tgt"]["oom_count"] == 0
        assert data["tgt"]["last_run_ts"] > 0

    def test_corrupt_json_gracefully_ignored(self, tmp_path: Path) -> None:
        recon_dir = tmp_path / ".recon"
        recon_dir.mkdir(parents=True)
        (recon_dir / "test_memory_profile.json").write_text("{invalid json}}}}")

        h = MemoryHistory.for_repo(tmp_path)
        assert h.estimate_mb("anything") is None
        # Should still work for writes
        h.record("new", 100)
        assert h.estimate_mb("new") == 100

    def test_missing_recon_dir_created(self, tmp_path: Path) -> None:
        h = MemoryHistory.for_repo(tmp_path)
        h.record("t", 50)
        assert (tmp_path / ".recon" / "test_memory_profile.json").exists()

    def test_multiple_targets(self, tmp_path: Path) -> None:
        h = MemoryHistory.for_repo(tmp_path)
        h.record("a", 100)
        h.record("b", 200)
        h.record("c", 300)
        assert h.estimate_mb("a") == 100
        assert h.estimate_mb("b") == 200
        assert h.estimate_mb("c") == 300
