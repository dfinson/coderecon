"""VS Code MCP configuration management."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import json5
import structlog

from coderecon.core.progress import status
from coderecon.files.ops import atomic_write_text

log = structlog.get_logger(__name__)

_MCP_URL_TEMPLATE = "http://127.0.0.1:{port}/mcp"

def _get_mcp_server_name(repo_root: Path) -> str:
    """Get the normalized MCP server name for a repo."""
    repo_name = repo_root.name
    normalized = repo_name.lower().replace(".", "_").replace("-", "_")
    return f"coderecon-{normalized}"

def _ensure_vscode_mcp_config(repo_root: Path, port: int) -> tuple[bool, str]:
    """Ensure .vscode/mcp.json has the CodeRecon server entry with static port.
    Creates or updates the MCP server entry with the actual port number.
    Call sync_vscode_mcp_port() from 'recon up' to update port if changed.
    Returns tuple of (was_modified, server_name).
    """
    vscode_dir = repo_root / ".vscode"
    mcp_json_path = vscode_dir / "mcp.json"
    server_name = _get_mcp_server_name(repo_root)
    expected_url = _MCP_URL_TEMPLATE.format(port=port)
    expected_config: dict[str, Any] = {
        "type": "http",
        "url": expected_url,
    }
    if mcp_json_path.exists():
        content = mcp_json_path.read_text()
        try:
            existing: dict[str, Any] = json5.loads(content)
        except ValueError:
            status(
                "Warning: .vscode/mcp.json is not valid JSON(C), skipping update",
                style="warning",
            )
            return False, server_name
        servers = existing.get("servers", {})
        if server_name in servers:
            current_url = servers[server_name].get("url", "")
            if current_url == expected_url:
                return False, server_name
            servers[server_name] = expected_config
        else:
            servers[server_name] = expected_config
        existing["servers"] = servers
        output = json.dumps(existing, indent=2) + "\n"
        atomic_write_text(mcp_json_path, output)
        return True, server_name
    else:
        vscode_dir.mkdir(parents=True, exist_ok=True)
        config = {"servers": {server_name: expected_config}}
        output = json.dumps(config, indent=2) + "\n"
        atomic_write_text(mcp_json_path, output)
        return True, server_name

def sync_vscode_mcp_port(repo_root: Path, port: int) -> bool:
    """Update port in .vscode/mcp.json if it differs from configured port.
    Called by 'recon up' to ensure mcp.json matches the running server port.
    Returns True if file was modified.
    """
    mcp_json_path = repo_root / ".vscode" / "mcp.json"
    if not mcp_json_path.exists():
        return _ensure_vscode_mcp_config(repo_root, port)[0]
    server_name = _get_mcp_server_name(repo_root)
    expected_url = _MCP_URL_TEMPLATE.format(port=port)
    content = mcp_json_path.read_text()
    try:
        existing: dict[str, Any] = json5.loads(content)
    except ValueError:
        log.debug("mcp_config_parse_failed", exc_info=True)
        return False
    servers = existing.get("servers", {})
    if server_name not in servers:
        servers[server_name] = {
            "type": "http",
            "url": expected_url,
        }
        existing["servers"] = servers
        output = json.dumps(existing, indent=2) + "\n"
        atomic_write_text(mcp_json_path, output)
        return True
    current_url = servers[server_name].get("url", "")
    if current_url == expected_url:
        return False
    existing_entry = servers.get(server_name, {})
    if isinstance(existing_entry, dict):
        existing_entry["type"] = "http"
        existing_entry["url"] = expected_url
        servers[server_name] = existing_entry
    else:
        servers[server_name] = {"type": "http", "url": expected_url}
    existing["servers"] = servers
    output = json.dumps(existing, indent=2) + "\n"
    atomic_write_text(mcp_json_path, output)
    return True
