"""Tests for coderecon.cli.mcp_writers — multi-tool MCP config writers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from coderecon.cli.mcp_writers import (
    ALL_TOOLS,
    detect_tools,
    resolve_targets,
    write_mcp_configs,
    sync_mcp_port,
    _write_vscode,
    _write_claude,
    _write_cursor,
    _write_opencode,
)


SERVER_NAME = "coderecon-testrepo"


# =============================================================================
# detect_tools
# =============================================================================


class TestDetectTools:
    def test_detects_vscode_via_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vscode").mkdir()
        tools = detect_tools(tmp_path)
        assert "vscode" in tools

    def test_detects_vscode_via_env(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"VSCODE_IPC_HOOK_CLI": "/run/vscode.sock"}):
            tools = detect_tools(tmp_path)
        assert "vscode" in tools

    def test_detects_claude_via_which(self, tmp_path: Path) -> None:
        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/claude" if cmd == "claude" else None):
            tools = detect_tools(tmp_path)
        assert "claude" in tools

    def test_detects_claude_via_homedir(self, tmp_path: Path) -> None:
        with patch("pathlib.Path.home", return_value=tmp_path):
            (tmp_path / ".claude").mkdir()
            tools = detect_tools(tmp_path)
        assert "claude" in tools

    def test_detects_cursor_via_repo_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".cursor").mkdir()
        tools = detect_tools(tmp_path)
        assert "cursor" in tools

    def test_detects_cursor_via_which(self, tmp_path: Path) -> None:
        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/cursor" if cmd == "cursor" else None):
            tools = detect_tools(tmp_path)
        assert "cursor" in tools

    def test_detects_opencode_via_which(self, tmp_path: Path) -> None:
        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/opencode" if cmd == "opencode" else None):
            tools = detect_tools(tmp_path)
        assert "opencode" in tools

    def test_falls_back_to_vscode_when_nothing_detected(self, tmp_path: Path) -> None:
        with (
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            tools = detect_tools(tmp_path)
        assert tools == ["vscode"]

    def test_no_duplicates(self, tmp_path: Path) -> None:
        # .vscode dir AND env both trigger vscode
        (tmp_path / ".vscode").mkdir()
        with patch.dict("os.environ", {"VSCODE_IPC_HOOK_CLI": "/run/vscode.sock"}):
            tools = detect_tools(tmp_path)
        assert tools.count("vscode") == 1


# =============================================================================
# resolve_targets
# =============================================================================


class TestResolveTargets:
    def test_passthrough_concrete_targets(self, tmp_path: Path) -> None:
        result = resolve_targets(["vscode", "claude"], tmp_path)
        assert result == ["vscode", "claude"]

    def test_all_expands_to_all_tools(self, tmp_path: Path) -> None:
        result = resolve_targets(["all"], tmp_path)
        assert set(result) == set(ALL_TOOLS)

    def test_auto_calls_detect_tools(self, tmp_path: Path) -> None:
        with patch("coderecon.cli.mcp_writers.detect_tools", return_value=["vscode", "claude"]) as mock:
            result = resolve_targets(["auto"], tmp_path)
        mock.assert_called_once_with(tmp_path)
        assert "vscode" in result
        assert "claude" in result

    def test_deduplicates(self, tmp_path: Path) -> None:
        result = resolve_targets(["vscode", "vscode", "claude"], tmp_path)
        assert result.count("vscode") == 1

    def test_order_preserved_after_dedup(self, tmp_path: Path) -> None:
        result = resolve_targets(["claude", "vscode", "claude"], tmp_path)
        assert result == ["claude", "vscode"]

    def test_mixed_auto_and_concrete(self, tmp_path: Path) -> None:
        with patch("coderecon.cli.mcp_writers.detect_tools", return_value=["vscode"]):
            result = resolve_targets(["claude", "auto"], tmp_path)
        assert "claude" in result
        assert "vscode" in result


# =============================================================================
# _write_vscode
# =============================================================================


class TestWriteVscode:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        assert _write_vscode(tmp_path, 3100, SERVER_NAME) is True
        data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert data["servers"][SERVER_NAME]["url"] == "http://127.0.0.1:3100/mcp"
        assert data["servers"][SERVER_NAME]["type"] == "http"

    def test_no_op_when_already_current(self, tmp_path: Path) -> None:
        _write_vscode(tmp_path, 3100, SERVER_NAME)
        assert _write_vscode(tmp_path, 3100, SERVER_NAME) is False

    def test_updates_port(self, tmp_path: Path) -> None:
        _write_vscode(tmp_path, 3100, SERVER_NAME)
        assert _write_vscode(tmp_path, 4200, SERVER_NAME) is True
        data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert data["servers"][SERVER_NAME]["url"] == "http://127.0.0.1:4200/mcp"

    def test_preserves_other_servers(self, tmp_path: Path) -> None:
        vscode = tmp_path / ".vscode"
        vscode.mkdir()
        (vscode / "mcp.json").write_text(json.dumps({"servers": {"other": {"command": "x"}}}))
        _write_vscode(tmp_path, 3100, SERVER_NAME)
        data = json.loads((vscode / "mcp.json").read_text())
        assert "other" in data["servers"]
        assert SERVER_NAME in data["servers"]

    def test_unparseable_json_skipped(self, tmp_path: Path) -> None:
        vscode = tmp_path / ".vscode"
        vscode.mkdir()
        (vscode / "mcp.json").write_text("{broken")
        assert _write_vscode(tmp_path, 3100, SERVER_NAME) is False
        assert (vscode / "mcp.json").read_text() == "{broken"


# =============================================================================
# _write_claude
# =============================================================================


class TestWriteClaude:
    def test_creates_dot_mcp_json(self, tmp_path: Path) -> None:
        assert _write_claude(tmp_path, 3100, SERVER_NAME) is True
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert data["mcpServers"][SERVER_NAME]["url"] == "http://127.0.0.1:3100/mcp"

    def test_no_op_when_already_current(self, tmp_path: Path) -> None:
        _write_claude(tmp_path, 3100, SERVER_NAME)
        assert _write_claude(tmp_path, 3100, SERVER_NAME) is False

    def test_updates_port(self, tmp_path: Path) -> None:
        _write_claude(tmp_path, 3100, SERVER_NAME)
        assert _write_claude(tmp_path, 5000, SERVER_NAME) is True
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert data["mcpServers"][SERVER_NAME]["url"] == "http://127.0.0.1:5000/mcp"

    def test_preserves_other_servers(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"existing": {"type": "http", "url": "http://x/mcp"}}})
        )
        _write_claude(tmp_path, 3100, SERVER_NAME)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "existing" in data["mcpServers"]
        assert SERVER_NAME in data["mcpServers"]

    def test_unparseable_json_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{bad json")
        assert _write_claude(tmp_path, 3100, SERVER_NAME) is False


# =============================================================================
# _write_cursor
# =============================================================================


class TestWriteCursor:
    def test_creates_cursor_mcp_json(self, tmp_path: Path) -> None:
        assert _write_cursor(tmp_path, 3100, SERVER_NAME) is True
        data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        assert data["mcpServers"][SERVER_NAME]["url"] == "http://127.0.0.1:3100/mcp"

    def test_no_op_when_already_current(self, tmp_path: Path) -> None:
        _write_cursor(tmp_path, 3100, SERVER_NAME)
        assert _write_cursor(tmp_path, 3100, SERVER_NAME) is False

    def test_updates_port(self, tmp_path: Path) -> None:
        _write_cursor(tmp_path, 3100, SERVER_NAME)
        _write_cursor(tmp_path, 5500, SERVER_NAME)
        data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        assert data["mcpServers"][SERVER_NAME]["url"] == "http://127.0.0.1:5500/mcp"

    def test_preserves_other_servers(self, tmp_path: Path) -> None:
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"other": {"type": "http", "url": "http://other/mcp"}}})
        )
        _write_cursor(tmp_path, 3100, SERVER_NAME)
        data = json.loads((cursor_dir / "mcp.json").read_text())
        assert "other" in data["mcpServers"]
        assert SERVER_NAME in data["mcpServers"]


# =============================================================================
# _write_opencode
# =============================================================================


class TestWriteOpencode:
    def test_creates_global_config(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        with patch("pathlib.Path.home", return_value=home):
            assert _write_opencode(tmp_path, 3100, SERVER_NAME) is True
            config_path = home / ".config" / "opencode" / "config.json"
            assert config_path.exists()
            data = json.loads(config_path.read_text())
        assert data["mcp"][SERVER_NAME]["url"] == "http://127.0.0.1:3100/mcp"
        assert data["mcp"][SERVER_NAME]["type"] == "streamable-http"

    def test_no_op_when_already_current(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        with patch("pathlib.Path.home", return_value=home):
            _write_opencode(tmp_path, 3100, SERVER_NAME)
            assert _write_opencode(tmp_path, 3100, SERVER_NAME) is False

    def test_updates_port(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        with patch("pathlib.Path.home", return_value=home):
            _write_opencode(tmp_path, 3100, SERVER_NAME)
            assert _write_opencode(tmp_path, 4000, SERVER_NAME) is True
            data = json.loads((home / ".config" / "opencode" / "config.json").read_text())
        assert data["mcp"][SERVER_NAME]["url"] == "http://127.0.0.1:4000/mcp"

    def test_preserves_other_entries(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(
            json.dumps({"mcp": {"other-server": {"type": "streamable-http", "url": "http://other/mcp"}}})
        )
        with patch("pathlib.Path.home", return_value=home):
            _write_opencode(tmp_path, 3100, SERVER_NAME)
            data = json.loads((config_dir / "config.json").read_text())
        assert "other-server" in data["mcp"]
        assert SERVER_NAME in data["mcp"]


# =============================================================================
# write_mcp_configs / sync_mcp_port
# =============================================================================


class TestWriteMcpConfigs:
    def test_writes_only_requested_tools(self, tmp_path: Path) -> None:
        written = write_mcp_configs(tmp_path, 3100, SERVER_NAME, ["vscode"])
        assert len(written) == 1
        assert (tmp_path / ".vscode" / "mcp.json").exists()
        assert not (tmp_path / ".mcp.json").exists()

    def test_writes_multiple_tools(self, tmp_path: Path) -> None:
        written = write_mcp_configs(tmp_path, 3100, SERVER_NAME, ["vscode", "claude"])
        assert len(written) == 2
        assert (tmp_path / ".vscode" / "mcp.json").exists()
        assert (tmp_path / ".mcp.json").exists()

    def test_unknown_tool_is_skipped(self, tmp_path: Path) -> None:
        # Should not raise; returns empty list
        written = write_mcp_configs(tmp_path, 3100, SERVER_NAME, ["unknown_tool"])
        assert written == []

    def test_no_op_when_nothing_changed(self, tmp_path: Path) -> None:
        write_mcp_configs(tmp_path, 3100, SERVER_NAME, ["vscode"])
        written = write_mcp_configs(tmp_path, 3100, SERVER_NAME, ["vscode"])
        assert written == []

    def test_returns_labels_for_written_files(self, tmp_path: Path) -> None:
        written = write_mcp_configs(tmp_path, 3100, SERVER_NAME, ["vscode", "claude"])
        assert any("mcp.json" in w for w in written)

    def test_sync_mcp_port_delegates_to_writers(self, tmp_path: Path) -> None:
        write_mcp_configs(tmp_path, 3100, SERVER_NAME, ["vscode", "claude"])
        synced = sync_mcp_port(tmp_path, 5000, SERVER_NAME, ["vscode", "claude"])
        assert len(synced) == 2
        vscode_data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert vscode_data["servers"][SERVER_NAME]["url"] == "http://127.0.0.1:5000/mcp"
        claude_data = json.loads((tmp_path / ".mcp.json").read_text())
        assert claude_data["mcpServers"][SERVER_NAME]["url"] == "http://127.0.0.1:5000/mcp"
