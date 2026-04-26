"""MCP configuration writers for each supported AI coding tool.

Supported tools:
  vscode   — .vscode/mcp.json          (VS Code / GitHub Copilot)
  claude   — .mcp.json                 (Claude Code project-level config)
  cursor   — .cursor/mcp.json          (Cursor)
  opencode — ~/.config/opencode/config.json  (OpenCode)

Auto-detection order when ``--mcp-target auto`` is used:
  1. VS Code  — .vscode/ dir in repo  OR  VSCODE_IPC_HOOK_CLI env var
  2. Claude   — ``which claude``       OR  ~/.claude/ dir exists
  3. Cursor   — ~/.cursor/ dir         OR  .cursor/ in repo  OR  ``which cursor``
  4. OpenCode — ~/.config/opencode/    OR  ``which opencode``

If no tool is detected the writer falls back to VS Code.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import json5
import structlog

from coderecon.files.ops import atomic_write_text

log = structlog.get_logger(__name__)

# Public constants

#: All tool IDs recognised by the writers.
ALL_TOOLS: tuple[str, ...] = ("vscode", "claude", "cursor", "opencode")


# Auto-detection


def detect_tools(repo_root: Path) -> list[str]:
    """Return the list of AI tools that appear to be present in this environment.

    Checks are cheap (filesystem probes + ``which``).  Always returns at least
    ``["vscode"]`` so callers never receive an empty list.

    Args:
        repo_root: Repository root used for per-project config probes.

    Returns:
        Ordered list of tool IDs (subset of ALL_TOOLS).
    """
    found: list[str] = []

    # VS Code / GitHub Copilot
    if (repo_root / ".vscode").exists() or os.environ.get("VSCODE_IPC_HOOK_CLI"):
        found.append("vscode")

    # Claude Code
    if shutil.which("claude") or (Path.home() / ".claude").exists():
        found.append("claude")

    # Cursor
    if (
        (Path.home() / ".cursor").exists()
        or (repo_root / ".cursor").exists()
        or shutil.which("cursor")
    ):
        found.append("cursor")

    # OpenCode
    if (Path.home() / ".config" / "opencode").exists() or shutil.which("opencode"):
        found.append("opencode")

    return found if found else ["vscode"]


def resolve_targets(targets: list[str], repo_root: Path) -> list[str]:
    """Expand ``auto`` / ``all`` pseudo-targets and de-duplicate.

    Args:
        targets: Raw list from ``--mcp-target`` (may include ``auto`` / ``all``).
        repo_root: Passed to :func:`detect_tools` when ``auto`` is requested.

    Returns:
        Flat, de-duplicated list of concrete tool IDs.
    """
    resolved: list[str] = []
    for t in targets:
        if t == "all":
            resolved.extend(ALL_TOOLS)
        elif t == "auto":
            resolved.extend(detect_tools(repo_root))
        else:
            resolved.append(t)
    # Preserve order but deduplicate
    seen: set[str] = set()
    out: list[str] = []
    for t in resolved:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# Per-tool writers


def _write_vscode(repo_root: Path, port: int, server_name: str) -> bool:
    """Write / update ``.vscode/mcp.json``.

    Format::

        {
          "servers": {
            "<server_name>": { "type": "http", "url": "http://127.0.0.1:<port>/mcp" }
          }
        }

    Returns:
        True if the file was created or modified.
    """
    vscode_dir = repo_root / ".vscode"
    mcp_json_path = vscode_dir / "mcp.json"
    expected_url = f"http://127.0.0.1:{port}/mcp"
    expected_entry: dict[str, Any] = {"type": "http", "url": expected_url}

    if mcp_json_path.exists():
        content = mcp_json_path.read_text()
        try:
            existing: dict[str, Any] = json5.loads(content)
        except ValueError:
            log.warning("vscode mcp.json is not valid JSONC — skipping update", path=str(mcp_json_path))
            return False

        servers: dict[str, Any] = existing.get("servers", {})
        if server_name in servers and servers[server_name].get("url") == expected_url:
            return False  # already up-to-date

        servers[server_name] = expected_entry
        existing["servers"] = servers
        atomic_write_text(mcp_json_path, json.dumps(existing, indent=2) + "\n")
        return True

    vscode_dir.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {"servers": {server_name: expected_entry}}
    atomic_write_text(mcp_json_path, json.dumps(config, indent=2) + "\n")
    return True


def _write_claude(repo_root: Path, port: int, server_name: str) -> bool:
    """Write / update project-level ``.mcp.json`` for Claude Code.

    Format::

        {
          "mcpServers": {
            "<server_name>": { "type": "http", "url": "http://127.0.0.1:<port>/mcp" }
          }
        }

    Returns:
        True if the file was created or modified.
    """
    mcp_path = repo_root / ".mcp.json"
    expected_url = f"http://127.0.0.1:{port}/mcp"
    expected_entry: dict[str, Any] = {"type": "http", "url": expected_url}

    if mcp_path.exists():
        content = mcp_path.read_text()
        try:
            existing: dict[str, Any] = json.loads(content)
        except ValueError:
            log.warning("claude .mcp.json is not valid JSON — skipping update", path=str(mcp_path))
            return False

        servers: dict[str, Any] = existing.get("mcpServers", {})
        if server_name in servers and servers[server_name].get("url") == expected_url:
            return False

        servers[server_name] = expected_entry
        existing["mcpServers"] = servers
        atomic_write_text(mcp_path, json.dumps(existing, indent=2) + "\n")
        return True

    config: dict[str, Any] = {"mcpServers": {server_name: expected_entry}}
    atomic_write_text(mcp_path, json.dumps(config, indent=2) + "\n")
    return True


def _write_cursor(repo_root: Path, port: int, server_name: str) -> bool:
    """Write / update ``.cursor/mcp.json`` for Cursor.

    Uses the same ``servers`` format as VS Code.

    Returns:
        True if the file was created or modified.
    """
    cursor_dir = repo_root / ".cursor"
    mcp_path = cursor_dir / "mcp.json"
    expected_url = f"http://127.0.0.1:{port}/mcp"
    expected_entry: dict[str, Any] = {"type": "http", "url": expected_url}

    if mcp_path.exists():
        content = mcp_path.read_text()
        try:
            existing: dict[str, Any] = json5.loads(content)
        except ValueError:
            log.warning("cursor mcp.json is not valid JSON — skipping update", path=str(mcp_path))
            return False

        servers: dict[str, Any] = existing.get("mcpServers", {})
        if server_name in servers and servers[server_name].get("url") == expected_url:
            return False

        servers[server_name] = expected_entry
        existing["mcpServers"] = servers
        atomic_write_text(mcp_path, json.dumps(existing, indent=2) + "\n")
        return True

    cursor_dir.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {"mcpServers": {server_name: expected_entry}}
    atomic_write_text(mcp_path, json.dumps(config, indent=2) + "\n")
    return True


def _write_opencode(_repo_root: Path, port: int, server_name: str) -> bool:
    """Write / update OpenCode global config at ``~/.config/opencode/config.json``.

    OpenCode uses a global config rather than a per-project file.  We create
    ``~/.config/opencode/config.json`` (or update it) with the server entry.

    Format::

        {
          "mcp": {
            "<server_name>": {
              "type": "streamable-http",
              "url": "http://127.0.0.1:<port>/mcp"
            }
          }
        }

    Returns:
        True if the file was created or modified.
    """
    config_dir = Path.home() / ".config" / "opencode"
    config_path = config_dir / "config.json"
    expected_url = f"http://127.0.0.1:{port}/mcp"
    expected_entry: dict[str, Any] = {"type": "streamable-http", "url": expected_url}

    if config_path.exists():
        content = config_path.read_text()
        try:
            existing: dict[str, Any] = json.loads(content)
        except ValueError:
            log.warning(
                "opencode config.json is not valid JSON — skipping update",
                path=str(config_path),
            )
            return False

        servers: dict[str, Any] = existing.get("mcp", {})
        if server_name in servers and servers[server_name].get("url") == expected_url:
            return False

        servers[server_name] = expected_entry
        existing["mcp"] = servers
        atomic_write_text(config_path, json.dumps(existing, indent=2) + "\n")
        return True

    config_dir.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {"mcp": {server_name: expected_entry}}
    atomic_write_text(config_path, json.dumps(config, indent=2) + "\n")
    return True


# Dispatch table

_WRITERS = {
    "vscode": _write_vscode,
    "claude": _write_claude,
    "cursor": _write_cursor,
    "opencode": _write_opencode,
}


def write_mcp_configs(
    repo_root: Path, port: int, server_name: str, targets: list[str]
) -> list[str]:
    """Write MCP config for each requested tool.

    Args:
        repo_root: Repository root.
        port: Port the CodeRecon daemon is (or will be) listening on.
        server_name: Normalized server name, e.g. ``coderecon-myrepo``.
        targets: Concrete tool IDs (already resolved — no ``auto`` / ``all``).

    Returns:
        List of human-readable descriptions of files created/updated, e.g.
        ``[".vscode/mcp.json", ".mcp.json"]``.
    """
    written: list[str] = []
    for tool in targets:
        writer = _WRITERS.get(tool)
        if writer is None:
            log.warning("unknown mcp target — skipping", tool=tool)
            continue
        modified = writer(repo_root, port, server_name)
        if modified:
            label = _tool_config_label(tool, repo_root)
            written.append(label)
    return written


def sync_mcp_port(repo_root: Path, port: int, server_name: str, targets: list[str]) -> list[str]:
    """Update port in all tool MCP configs.

    Same as :func:`write_mcp_configs` — the writers are idempotent and always
    update if the URL differs.

    Args:
        repo_root: Repository root.
        port: New port to synchronise.
        server_name: Normalized server name.
        targets: Concrete tool IDs to update.

    Returns:
        List of human-readable descriptions of files updated.
    """
    return write_mcp_configs(repo_root, port, server_name, targets)


# Helpers


def _tool_config_label(tool: str, _repo_root: Path) -> str:
    """Short human-readable label for the config file written by *tool*."""
    labels = {
        "vscode": ".vscode/mcp.json",
        "claude": ".mcp.json",
        "cursor": ".cursor/mcp.json",
        "opencode": str(Path("~/.config/opencode/config.json").expanduser()),
    }
    return labels.get(tool, tool)
