# MCP Setup

`recon register` writes MCP config files automatically. This page covers manual setup, multi-tool targeting, and troubleshooting.

---

## How It Works

When you run `recon register`, CodeRecon:

1. Determines which tools are present via auto-detection
2. Writes a per-tool MCP config file pointing at the running daemon
3. Injects a CodeRecon instruction snippet into each tool's agent instruction file

The server name is derived from the repository directory name:

```
coderecon-<normalized-repo-name>
```

For example, a repo at `/home/dave/projects/my-app` gets server name `coderecon-my_app`.

---

## Tool Targeting

### Auto-detection (default)

```bash
recon register          # same as --mcp-target auto
```

Auto-detection checks (in order):

| Tool | Probe |
|---|---|
| VS Code | `.vscode/` directory in repo, OR `VSCODE_IPC_HOOK_CLI` env var |
| Claude Code | `which claude` succeeds, OR `~/.claude/` directory exists |
| Cursor | `~/.cursor/` or `.cursor/` in repo, OR `which cursor` succeeds |
| OpenCode | `~/.config/opencode/` directory exists, OR `which opencode` succeeds |

If nothing is detected, VS Code is used as the fallback.

### Explicit targeting

```bash
# Single tool
recon register --mcp-target vscode
recon register --mcp-target claude

# Multiple tools (repeat the flag)
recon register --mcp-target vscode --mcp-target claude

# All supported tools
recon register --mcp-target all
```

---

## Config File Locations

### VS Code / GitHub Copilot

**File:** `.vscode/mcp.json`

```json
{
  "servers": {
    "coderecon-myapp": {
      "type": "http",
      "url": "http://127.0.0.1:3100/mcp"
    }
  }
}
```

!!! info "Existing servers preserved"
    CodeRecon adds its entry without touching other servers already in the file. JSONC comments and trailing commas are handled correctly.

---

### Claude Code

**File:** `.mcp.json` (project root)

```json
{
  "mcpServers": {
    "coderecon-myapp": {
      "type": "http",
      "url": "http://127.0.0.1:3100/mcp"
    }
  }
}
```

---

### Cursor

**File:** `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "coderecon-myapp": {
      "type": "http",
      "url": "http://127.0.0.1:3100/mcp"
    }
  }
}
```

---

### OpenCode

**File:** `~/.config/opencode/config.json` (global, not per-project)

```json
{
  "mcp": {
    "coderecon-myapp": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:3100/mcp"
    }
  }
}
```

!!! note "Global config"
    OpenCode uses a global config file. `recon register` updates it with the new server entry without removing existing entries.

---

## Agent Instruction Files

Alongside the MCP config, `recon register` injects a CodeRecon snippet into each tool's agent instruction file. This tells your agent the MCP tool prefix (`mcp_coderecon_myapp_`) and the required workflow (always call `recon` first, use `checkpoint` to commit).

| Tool | Instruction file |
|---|---|
| VS Code / Copilot | `.github/copilot-instructions.md` |
| Claude Code | `CLAUDE.md` |
| Cursor | `.cursor/rules/coderecon.mdc` |
| OpenCode | `AGENTS.md` |

If the file already contains a CodeRecon snippet, it is replaced in-place. If the file doesn't exist, it is created with a minimal header.

!!! tip "Re-running registration"
    `recon register` is safe to run again — it's idempotent. If the port changes (e.g. after restarting on a different port), re-registering updates all config files.

---

## Port Configuration

The default port is **3100**. To use a different port:

```bash
recon up --port 4200
recon register --port 4200
```

The port is persisted in `.recon/config.yaml` so subsequent `recon register` calls on the same repo use it automatically.

---

## Troubleshooting

### MCP server doesn't appear in the tool

1. Confirm the daemon is running: `recon status`
2. Check the config file was written: `cat .vscode/mcp.json`
3. Reload the tool (VS Code: restart Copilot Chat; Claude Code: restart session)
4. Verify the URL is reachable: `curl http://127.0.0.1:3100/health`

### Wrong port in config file

Re-register with the correct port:

```bash
recon register --port 3100
```

### Multiple repos — wrong server activated

Each repo gets a unique server name (`coderecon-<repo>`). Your agent should target the specific server. Check the server name with:

```bash
recon catalog
```

### Config file has comments and `recon register` is skipping it

VS Code `.vscode/mcp.json` with JSONC comments is fully supported. If the file is truly malformed (not parseable), CodeRecon will skip the update and log a warning — fix the JSON syntax manually.
