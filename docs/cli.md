---
title: CLI Reference
description: Operator interface commands for the recon CLI
---

The `recon` CLI is the operator interface. It is **not** agent-facing — agents interact via MCP tools.

---

## Server Lifecycle

### `recon up`

Start the global multi-repo daemon (foreground).

```bash
recon up [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--port, -p` | `7654` | Port to bind to |
| `--dev-mode` | off | Enable development endpoints |

The daemon activates all repos already in the catalog on startup. Press `Ctrl+C` to stop.

---

### `recon down`

Stop the running daemon.

```bash
recon down
```

---

### `recon restart`

Restart the daemon (stop + start with same config).

```bash
recon restart
```

---

### `recon status`

Show daemon status and per-repo index health.

```bash
recon status
```

---

## Repository Management

### `recon register`

Register a repository in the global catalog. Builds the index if needed and activates the repo immediately if the daemon is running.

```bash
recon register [PATH] [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `PATH` | current dir | Repository path |
| `--reindex, -r` | off | Wipe and rebuild the index from scratch |
| `--mcp-target, -t` | `auto` | AI tool(s) to write MCP config for. Repeatable. |
| `--port, -p` | — | Override port (persisted to `config.yaml`) |

**`--mcp-target` choices:**

| Value | Effect |
|---|---|
| `auto` | Detect installed tools automatically (default) |
| `all` | Write config for every supported tool |
| `vscode` | VS Code / GitHub Copilot (`.vscode/mcp.json`) |
| `claude` | Claude Code (`.mcp.json`) |
| `cursor` | Cursor (`.cursor/mcp.json`) |
| `opencode` | OpenCode (`~/.config/opencode/config.json`) |

**Examples:**

```bash
# Current directory, auto-detect tools
recon register

# Specific path, both VS Code and Claude Code
recon register ~/projects/myapp --mcp-target vscode --mcp-target claude

# Force rebuild
recon register --reindex

# Write for every supported tool
recon register --mcp-target all
```

---

### `recon unregister`

Remove a repository from the catalog and deactivate its live daemon slot.

```bash
recon unregister [PATH]
```

If the daemon is running, deactivation happens immediately without a restart.

---

### `recon catalog`

List all registered repositories and their worktrees.

```bash
recon catalog
```

Example output:

```
  myapp
    git:        /home/dave/projects/myapp/.git
    storage:    /home/dave/projects/myapp/.recon
    worktrees:  main* feature-auth

  backend
    git:        /home/dave/projects/backend/.git
    storage:    /home/dave/projects/backend/.recon
    worktrees:  main*
```

---

### `recon worktrees`

List worktrees for a repository.

```bash
recon worktrees [PATH]
```

---

### `recon register-worktree`

Register a specific git worktree that was added after the main repo was registered.

```bash
recon register-worktree [PATH]
```

---

### `recon global-status`

Show daemon health summary across all registered repos.

```bash
recon global-status
```

---

## Per-Repo Utilities

### `recon clear`

Remove CodeRecon artifacts from a repository (`.recon/` directory). Does not touch your source code.

```bash
recon clear [PATH]
```

!!! warning
    This deletes the index, config, and all stored artifacts. `recon register` can rebuild everything.

---

### `recon init` *(internal)*

Low-level repo initialization. **Use `recon register` instead.** `init` is hidden from help output.

```bash
recon init [PATH] [--reindex] [--port PORT] [--mcp-target TARGET]
```

---

## Global Flags

| Flag | Description |
|---|---|
| `--version` | Show installed version |
| `--verbose, -v` | Enable DEBUG logging |
| `--help` | Show help for any command |

---

## Environment Variables

| Variable | Description |
|---|---|
| `CODERECON__SERVER__PORT` | Override default daemon port |
| `CODERECON__LOGGING__LEVEL` | Override log level (`DEBUG`, `INFO`, `WARN`, `ERROR`) |

Double-underscores separate nesting levels: `CODERECON__<SECTION>__<KEY>`.
