---
title: Getting Started
description: Walk from zero to a working CodeRecon setup in about 5 minutes
---

This guide walks from zero to a working CodeRecon + AI tool setup in about 5 minutes.

## Prerequisites

- Python 3.10+
- A Git repository
- One of: VS Code, Claude Code, Cursor, or OpenCode

## Installation

=== "pip"

    ```bash
    pip install coderecon
    ```

=== "pipx (recommended)"

    ```bash
    pipx install coderecon
    ```

=== "uv"

    ```bash
    uv tool install coderecon
    ```

Verify the install:

```bash
recon --version
```

---

## Step 1 — Start the Daemon

CodeRecon runs as a single global background daemon. Start it once; it manages all your registered repos.

```bash
recon up
```

You'll see the CodeRecon banner and the daemon URL. Leave this terminal open, or run it as a background process.

!!! tip "Keep it running"
    Run `recon up` once per session. The daemon persists across `recon register` / `recon unregister` calls — no restart needed.

---

## Step 2 — Register Your Repo

From inside your repository:

```bash
cd /path/to/your/repo
recon register
```

This does three things:

1. Creates `.recon/` with config and index storage
2. Builds the initial index (tree-sitter parse + Tantivy full-text)
3. **Writes MCP config** for your detected AI tool(s)

### What gets written

`recon register` auto-detects installed tools and writes the right config file for each:

| Detected tool | File written |
|---|---|
| VS Code / Copilot | `.vscode/mcp.json` |
| Claude Code | `.mcp.json` |
| Cursor | `.cursor/mcp.json` |
| OpenCode | `~/.config/opencode/config.json` |

It also injects a CodeRecon instruction snippet into the tool's agent instruction file (`.github/copilot-instructions.md`, `CLAUDE.md`, etc.) so your agent knows the MCP tool prefix and required workflow.

!!! info "Targeting specific tools"
    Use `--mcp-target` to be explicit:
    ```bash
    recon register --mcp-target vscode --mcp-target claude
    recon register --mcp-target all
    ```
    See [MCP Setup](mcp-setup.md) for the full flag reference.

---

## Step 3 — Verify the MCP Connection

### VS Code / GitHub Copilot

1. Open VS Code in the registered repo
2. Open the Copilot Chat panel
3. Switch to **Agent mode** (the `@` dropdown)
4. Look for `coderecon-<reponame>` in the MCP servers list

### Claude Code

```bash
# From inside the repo
claude
> /mcp   # should list coderecon-<reponame>
```

### Cursor

1. Open Cursor settings → MCP
2. Verify `coderecon-<reponame>` appears and is enabled

### OpenCode

```bash
opencode
# MCP servers are loaded automatically from ~/.config/opencode/config.json
```

---

## Step 4 — Run Your First Recon

Once connected, ask your agent to run a `recon` call:

```
recon(task="understand the authentication module", seeds=["AuthService", "login"], read_only=True)
```

The response contains:

- **SCAFFOLD** — imports and function/class signatures for the most relevant files
- **LITE** — path + one-line description for secondary context
- **repo_map** — every tracked file in the repo (your file inventory)
- **agentic_hint** — what to do next

---

## Typical Agent Workflow

### Read-only research

```
recon(task="how does caching work?", read_only=True)
→ cat src/cache.py                          # read via terminal
→ checkpoint(changed_files=[])              # reset session state
```

### Edit a file

```
recon(task="add retry logic to fetch()", read_only=False)
→ cat src/client.py                         # read, then edit
→ checkpoint(changed_files=["src/client.py"], commit_message="feat: add retry")
```

### Rename a symbol

```
recon(task="rename UserProfile → Profile", read_only=False)
→ refactor_rename(symbol="UserProfile", new_name="Profile", justification="...")
→ refactor_commit(refactor_id="...", inspect_path="src/models.py")  # review ambiguous
→ refactor_commit(refactor_id="...")                                  # apply
→ checkpoint(changed_files=["src/models.py", ...], commit_message="refactor: rename")
```

---

## Registering More Repos

Each repo gets its own daemon slot. Register as many as you like:

```bash
cd ~/projects/frontend && recon register
cd ~/projects/backend  && recon register
cd ~/projects/infra    && recon register
```

Check what's registered:

```bash
recon catalog
```

---

## Updating the Index

The index stays current automatically as you edit files. To force a full rebuild:

```bash
recon register --reindex
```

---

## Next Steps

- [MCP Setup](mcp-setup.md) — multi-tool targeting, manual config edits
- [CLI Reference](cli.md) — every `recon` command
- [MCP Tools](tools.md) — full tool catalog with parameter docs
- [Configuration](configuration.md) — `.recon/config.yaml` reference
