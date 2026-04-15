"""Tests for recon up command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from coderecon.cli.main import cli

runner = CliRunner()


class TestUpCommand:
    """recon up command tests."""

    @patch("coderecon.daemon.global_lifecycle.read_global_server_info")
    @patch("coderecon.daemon.global_lifecycle.is_global_server_running")
    def test_given_already_running_when_up_then_reports_running(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
    ) -> None:
        """Up reports daemon already running and exits without starting a new one."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 8765)

        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        assert "already running" in result.output.lower()
        assert "12345" in result.output
        assert "recon register" in result.output

    @patch("coderecon.daemon.global_lifecycle.run_global_server")
    @patch("coderecon.daemon.global_lifecycle.is_global_server_running")
    def test_given_not_running_when_up_then_starts_server(
        self,
        mock_is_running: MagicMock,
        mock_run_server: MagicMock,
    ) -> None:
        """Up starts the global daemon when not running."""
        mock_is_running.return_value = False
        mock_run_server.side_effect = KeyboardInterrupt()

        result = runner.invoke(cli, ["up"])
        assert "stopped" in result.output.lower() or result.exit_code == 0
        mock_run_server.assert_called_once()

    @patch("coderecon.daemon.global_lifecycle.run_global_server")
    @patch("coderecon.daemon.global_lifecycle.is_global_server_running")
    def test_given_port_option_when_up_then_passes_port(
        self,
        mock_is_running: MagicMock,
        mock_run_server: MagicMock,
    ) -> None:
        """Up --port passes the port to run_global_server."""
        mock_is_running.return_value = False
        mock_run_server.side_effect = KeyboardInterrupt()

        runner.invoke(cli, ["up", "--port", "9999"])
        mock_run_server.assert_called_once()
        _, kwargs = mock_run_server.call_args
        assert kwargs.get("port") == 9999
