"""Tests for coderecon.daemon.global_lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from coderecon.daemon.global_lifecycle import (
    is_global_server_running,
    read_global_server_info,
    remove_global_pid,
    stop_global_daemon,
    write_global_pid,
)


class TestWriteGlobalPid:
    """Tests for write_global_pid."""

    def test_writes_pid_and_port(self, tmp_path: Path) -> None:
        with patch("os.getpid", return_value=42):
            write_global_pid(tmp_path, 7654)
        assert (tmp_path / "daemon.pid").read_text() == "42"
        assert (tmp_path / "daemon.port").read_text() == "7654"


class TestRemoveGlobalPid:
    """Tests for remove_global_pid."""

    def test_removes_existing_files(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text("42")
        (tmp_path / "daemon.port").write_text("7654")
        remove_global_pid(tmp_path)
        assert not (tmp_path / "daemon.pid").exists()
        assert not (tmp_path / "daemon.port").exists()

    def test_no_error_if_files_missing(self, tmp_path: Path) -> None:
        remove_global_pid(tmp_path)  # Should not raise


class TestReadGlobalServerInfo:
    """Tests for read_global_server_info."""

    def test_returns_pid_and_port(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text("1234")
        (tmp_path / "daemon.port").write_text("8080")
        result = read_global_server_info(tmp_path)
        assert result == (1234, 8080)

    def test_returns_none_when_pid_missing(self, tmp_path: Path) -> None:
        result = read_global_server_info(tmp_path)
        assert result is None

    def test_returns_none_when_pid_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text("notanumber")
        (tmp_path / "daemon.port").write_text("8080")
        result = read_global_server_info(tmp_path)
        assert result is None

    def test_returns_none_when_port_missing(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text("1234")
        result = read_global_server_info(tmp_path)
        assert result is None


class TestIsGlobalServerRunning:
    """Tests for is_global_server_running."""

    def test_returns_false_when_no_pid_file(self, tmp_path: Path) -> None:
        assert is_global_server_running(tmp_path) is False

    def test_returns_true_when_process_exists(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text(str(os.getpid()))
        (tmp_path / "daemon.port").write_text("8080")
        assert is_global_server_running(tmp_path) is True

    def test_returns_false_and_cleans_up_stale_pid(self, tmp_path: Path) -> None:
        # Use a PID that definitely doesn't exist
        (tmp_path / "daemon.pid").write_text("999999999")
        (tmp_path / "daemon.port").write_text("8080")
        assert is_global_server_running(tmp_path) is False
        # Stale PID files should be cleaned up
        assert not (tmp_path / "daemon.pid").exists()


class TestStopGlobalDaemon:
    """Tests for stop_global_daemon."""

    def test_returns_false_when_no_pid_file(self, tmp_path: Path) -> None:
        assert stop_global_daemon(tmp_path) is False

    def test_sends_sigterm_to_running_process(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text("12345")
        (tmp_path / "daemon.port").write_text("8080")
        with patch("os.kill") as mock_kill:
            result = stop_global_daemon(tmp_path)
        assert result is True
        mock_kill.assert_called_once()
        args = mock_kill.call_args[0]
        assert args[0] == 12345

    def test_returns_false_and_cleans_up_on_dead_process(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text("999999999")
        (tmp_path / "daemon.port").write_text("8080")
        with patch("os.kill", side_effect=ProcessLookupError):
            result = stop_global_daemon(tmp_path)
        assert result is False
        assert not (tmp_path / "daemon.pid").exists()
