---
title: Architecture
description: How CodeRecon works under the hood
---

How CodeRecon works under the hood.

---

## Overview

CodeRecon is a **per-repository analysis daemon**. Each registered repository runs its own daemon process on a dedicated port. AI agents connect via MCP over HTTP. The global catalog tracks all registered repos.

```
┌─────────────────────────────────────┐
│           AI Agent (LLM)            │
│   VS Code / Claude / Cursor / ...   │
└──────────────┬──────────────────────┘
               │ MCP (HTTP/SSE, port 7654)
               ▼
┌─────────────────────────────────────┐
│         CodeRecon Daemon            │
│         (FastMCP server)            │
│                                     │
│  ┌─────────────┐  ┌──────────────┐  │
│  │  MCP Tools  │  │ Session Mgr  │  │
│  └──────┬──────┘  └──────┬───────┘  │
│         │                │          │
│  ┌──────▼──────────────▼───────┐    │
│  │    IndexCoordinatorEngine   │    │
│  │   ┌───────┐  ┌───────────┐  │    │
│  │   │Tier 0 │  │  Tier 1   │  │    │
│  │   │Tantivy│  │SQLite+TSx │  │    │
│  │   └───────┘  └───────────┘  │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
               │
               │ catalog lookup
               ▼
┌─────────────────────────────────────┐
│       Global Catalog (~/.local)     │
│  SQLite: repo registry + worktrees  │
└─────────────────────────────────────┘
```

---

## Global Daemon Architecture

CodeRecon uses a **global multi-repo daemon**: a single background process manages all registered repos and routes MCP connections to the correct per-repo engine. Each repo still gets its own isolated port.

```
recon up        → start daemon for current repo
recon global-status  → see all running repo servers
```

Daemon processes are managed via PID files in `~/.local/share/coderecon/`.

---

## Two-Tier Index

Every registered repo builds and maintains a two-tier index inside `.recon/`.

### Tier 0 — Lexical (Tantivy)

**Always on.** Tantivy full-text index over all source files. Used for:

- Candidate discovery (fast text search across all tokens)
- Fallback when Tier 1 is unavailable (e.g., during indexing)
- Supplemental lexical matching alongside semantic results

Stored in `.recon/tantivy/`. Rebuilt automatically on first `recon up` and kept fresh via file-watch events.

### Tier 1 — Structural Facts (Tree-sitter + SQLite)

**Parsed, structured knowledge.** Each source file is parsed with Tree-sitter to extract:

| Fact type | Description |
|-----------|-------------|
| `DefFact` | Symbol definitions (functions, classes, variables) |
| `RefFact` | References to symbols, with tier/role |
| `ImportFact` | Import statements and resolved targets |
| `ExportFact` | Exported symbols / public API surface |
| `ScopeFact` | Lexical scope hierarchy |
| `LocalBindFact` | Local variable bindings |

The import graph (who imports whom) drives the `graph_*` tools and the tiered test selection in `checkpoint`.

---

## Epoch Model and Freshness

The index uses an **epoch counter** to track index generations. Each full re-index bumps the epoch. Incremental updates (triggered by file changes) are applied as delta patches without bumping the epoch.

A **freshness gate** blocks queries when the index is stale beyond a threshold — ensuring agents never see significantly out-of-date results.

```
epoch 1 → initial index
epoch 2 → full re-index after large change
epoch 3 → ...

intra-epoch deltas: tracked as "changed files" against the current epoch
```

---

## Session Model

Each MCP connection gets an isolated **session**. Sessions hold:

- `candidate_maps` — the `recon` call's result, required before refactoring
- `mutation_ctx` — pending refactor IDs awaiting `refactor_commit` or `refactor_cancel`

Sessions are scoped to a single agent conversation. No session state bleeds between connections.

---

## Refactor Engine

Structural refactors (rename/move) are **preview-first**: the engine computes all edits and assigns each hunk a certainty level before any file is touched.

```
Certainty levels:
  high    — unambiguous symbol resolution
  medium  — probable match, context confirmed
  low     — possible match, human review suggested
```

When low-certainty hunks are present, `refactor_commit` returns a `verification_required` flag. The agent should use `refactor_commit(inspect_path=...)` to review those matches before applying.

The apply step runs inside a mutation lock to prevent concurrent conflicting changes.

---

## Worktree Support

CodeRecon supports Git worktrees. Each worktree is registered separately and gets its own entry in the global catalog. The index is shared (read-only) across worktrees; only the worktree-specific delta is tracked independently.

```bash
recon register-worktree    # register current worktree
recon worktrees            # list all worktrees for this repo
```

---

## Cross-Filesystem Detection (WSL)

When a repo is on a Windows filesystem path (e.g. `/mnt/c/...`), cross-filesystem SQLite I/O would be prohibitively slow. CodeRecon detects this automatically at register time and moves the `.recon/` index to a native Linux path: `~/.local/share/coderecon/indices/<repo-hash>/`. This is transparent to the agent.

---

## File Watching and Delta Indexing

Once `recon up` starts the daemon, a file watcher monitors the repository for changes. When files are saved:

1. Changed files are queued for re-parsing
2. Tier 1 deltas are applied to SQLite
3. Tantivy index is updated incrementally

This keeps the index fresh without requiring a full re-index between edits.

---

## Checkpoint: Tiered Test Selection

`checkpoint` selects which tests to run using the import graph, not a naive "run everything" approach:

| Hop | Description |
|-----|-------------|
| 0 | Test files that **directly import** a changed file |
| 1 | Test files that import files that import a changed file |
| N | Further transitive dependencies |

By default, only hop-0 tests run. If hop-0 passes, hop-1 runs. If hop-0 fails, transitive hops are skipped (failures would just cascade). This gives fast iteration without sacrificing coverage on the critical path.

Low-cost test targets (e.g., individual pytest files) are batched into single subprocess invocations to reduce spawn overhead.
