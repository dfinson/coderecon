---
title: CodeRecon
description: Local repository control plane for AI coding agents
---

**Local repository control plane for AI coding agents.**

CodeRecon is a background daemon that indexes your repository and exposes a set of deterministic, structured tools via the Model Context Protocol (MCP). It replaces slow, fragile agent workflows — grep loops, terminal mediation, re-reads — with a single structured call.

---

## The Problem

Small code changes take AI agents 5–10 minutes, not because of model capability, but because of how agents interact with repositories:

- **Exploratory thrash** — repeated `grep`, file opens, retries to build a mental model
- **Terminal mediation** — unstructured text output from `git`, `pytest`, `cat`  
- **No convergence control** — agents repeat identical failure modes with no enforced strategy changes
- **Missing deterministic refactors** — renames that IDEs do in seconds take agents minutes

## The Fix

CodeRecon sits beneath agents and makes a repository a **deterministic, queryable system**.

```
Agent: recon(task="rename UserProfile to Profile across the codebase")

CodeRecon:
  → Looks up all 43 references via structural index
  → Generates atomic edit plan
  → Returns scaffold + repo_map + agentic_hint
  → Agent edits files, calls checkpoint()
  → CodeRecon: lint → test → commit
```

One call. Structured output. No loops.

---

## Quick Start

```bash
# 1. Install
pip install coderecon

# 2. Start the global daemon
recon up

# 3. Register your repo (from inside the repo)
recon register

# 4. Your AI tool now has CodeRecon MCP tools available
```

`recon register` auto-detects your AI tool (VS Code, Claude Code, Cursor, OpenCode) and writes the MCP config file for you.

---

## Key Features

<div class="grid cards" markdown>

-   :material-magnify: **Task-Aware Discovery**

    ---

    `recon(task="...", seeds=[...])` returns SCAFFOLD (imports + signatures), LITE (path + description), and `repo_map` — the complete file inventory.

-   :material-source-branch: **Structural Index**

    ---

    Tree-sitter-backed facts: definitions, references, imports, scopes. Enables precise find-all-usages without guessing.

-   :material-rename-box: **Deterministic Refactors**

    ---

    `refactor_rename`, `refactor_move` — preview → commit/cancel. Atomic. Cross-file. Never guesses bindings.

-   :material-check-all: **lint → test → commit**

    ---

    `checkpoint(changed_files=[...])` runs configured linters, the test suite, and commits — all in one call.

-   :material-git: **Git Integration**

    ---

    `checkpoint` and `semantic_diff` handle git operations (commit, diff, push) with structured JSON output. No terminal parsing.

-   :material-shield-check: **Convergence Controls**

    ---

    Mutation budgets, failure fingerprinting, and per-task limits prevent infinite loops before they waste context.

</div>

---

## Supported AI Tools

| Tool | Config written by `recon register` |
|---|---|
| VS Code / GitHub Copilot | `.vscode/mcp.json` |
| Claude Code | `.mcp.json` |
| Cursor | `.cursor/mcp.json` |
| OpenCode | `~/.config/opencode/config.json` |

---

## Next Steps

- [Getting Started](getting-started.md) — install, register your first repo, verify MCP connection
- [MCP Setup](mcp-setup.md) — per-tool config details and multi-tool targeting
- [MCP Tools](tools.md) — complete tool catalog with examples
- [CLI Reference](cli.md) — all `recon` commands
