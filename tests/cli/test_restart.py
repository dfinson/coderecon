"""Tests for recon restart command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from coderecon.cli.main import cli

runner = CliRunner()

class TestRestartCommand:
    """recon restart command tests."""

    @patch("coderecon.daemon.global_lifecycle.run_global_server", side_effect=KeyboardInterrupt())
    @patch("coderecon.cli.restart.is_global_server_running")
    def test_given_not_running_when_restart_then_starts_fresh(
        self,
        mock_is_running: MagicMock,
        mock_run_server: MagicMock,
    ) -> None:
        """Restart starts fresh when no daemon is running."""
        mock_is_running.return_value = False

        with patch("coderecon.daemon.global_lifecycle.is_global_server_running", return_value=False):
            result = runner.invoke(cli, ["restart"])

        assert "starting fresh" in result.output.lower()

    @patch("coderecon.daemon.global_lifecycle.run_global_server", side_effect=KeyboardInterrupt())
    @patch("coderecon.cli.restart.stop_global_daemon")
    @patch("coderecon.cli.restart.read_global_server_info")
    @patch("coderecon.cli.restart.is_global_server_running")
    def test_given_running_when_restart_then_stops_then_starts(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop: MagicMock,
        mock_run_server: MagicMock,
    ) -> None:
        """Restart stops the running daemon, then starts a new one."""
        mock_is_running.side_effect = [True, False]
        mock_read_info.return_value = (12345, 7654)
        mock_stop.return_value = True

        with patch("coderecon.daemon.global_lifecycle.is_global_server_running", return_value=False):
            result = runner.invoke(cli, ["restart"])

        assert "stopping" in result.output.lower()
        assert "12345" in result.output
        mock_stop.assert_called_once()

    @patch("coderecon.cli.restart.stop_global_daemon")
    @patch("coderecon.cli.restart.read_global_server_info")
    @patch("coderecon.cli.restart.is_global_server_running")
    def test_given_running_when_stop_fails_then_exits(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop: MagicMock,
    ) -> None:
        """Restart exits with error if stop signal fails."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 7654)
        mock_stop.return_value = False

        result = runner.invoke(cli, ["restart"])
        assert result.exit_code != 0

    @patch("coderecon.cli.restart.stop_global_daemon")
    @patch("coderecon.cli.restart.read_global_server_info")
    @patch("coderecon.cli.restart.is_global_server_running")
    def test_given_running_when_stop_timeout_then_exits(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop: MagicMock,
    ) -> None:
        """Restart exits with error if daemon doesn't stop within timeout."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 7654)
        mock_stop.return_value = True

        with patch("coderecon.cli.restart.time.sleep"):
            result = runner.invoke(cli, ["restart"])

        assert result.exit_code != 0
        assert "did not stop" in result.output.lower()
