"""MCP server module — FastMCP tool registration and wiring.

Tools
=====
- **recon** / **recon_map** / **recon_impact** — code intelligence and context retrieval
- **describe** — symbol/file introspection
- **semantic_diff** — structural diff
- **refactor_rename** / **refactor_move** — semantic cross-file refactors
- **refactor_commit** / **refactor_cancel** — apply or discard refactor previews
- **checkpoint** — lint → test → commit pipeline
"""

from coderecon.mcp.context import AppContext
from coderecon.mcp.server import create_mcp_server

__all__ = ["AppContext", "create_mcp_server"]
