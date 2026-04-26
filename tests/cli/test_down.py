"""Tests for coderecon.cli.down."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from coderecon.cli.down import down_command


class TestDownCommand:
    """Tests for the down CLI command."""

    def test_daemon_not_running(self) -> None:
        runner = CliRunner()
        with patch("coderecon.cli.down.is_global_server_running", return_value=False):
            result = runner.invoke(down_command)
        assert result.exit_code == 0
        assert "not running" in result.output

    def test_daemon_running_but_no_server_info(self) -> None:
        runner = CliRunner()
        with (
            patch("coderecon.cli.down.is_global_server_running", return_value=True),
            patch("coderecon.cli.down.read_global_server_info", return_value=None),
        ):
            result = runner.invoke(down_command)
        assert result.exit_code == 0
        assert "not running" in result.output

    def test_daemon_stop_succeeds(self) -> None:
        runner = CliRunner()
        # First call returns True (running), subsequent calls return False (stopped)
        running_calls = iter([True, False])
        with (
            patch("coderecon.cli.down.is_global_server_running", side_effect=lambda: next(running_calls, False)),
            patch("coderecon.cli.down.read_global_server_info", return_value=(1234, 8080)),
            patch("coderecon.cli.down.stop_global_daemon", return_value=True),
        ):
            result = runner.invoke(down_command)
        assert result.exit_code == 0
        assert "Stopping daemon" in result.output
        assert "Daemon stopped" in result.output

    def test_daemon_stop_fails_signal(self) -> None:
        runner = CliRunner()
        with (
            patch("coderecon.cli.down.is_global_server_running", return_value=True),
            patch("coderecon.cli.down.read_global_server_info", return_value=(1234, 8080)),
            patch("coderecon.cli.down.stop_global_daemon", return_value=False),
        ):
            result = runner.invoke(down_command)
        assert result.exit_code == 1
        assert "Failed to send stop signal" in result.output

    def test_daemon_stop_timeout(self) -> None:
        runner = CliRunner()
        with (
            patch("coderecon.cli.down.is_global_server_running", return_value=True),
            patch("coderecon.cli.down.read_global_server_info", return_value=(1234, 8080)),
            patch("coderecon.cli.down.stop_global_daemon", return_value=True),
            patch("time.sleep"),  # Don't actually wait
        ):
            result = runner.invoke(down_command)
        assert result.exit_code == 1
        assert "did not stop" in result.output
