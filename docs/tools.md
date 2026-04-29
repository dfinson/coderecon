---
title: MCP Tools Reference
description: Complete reference for all CodeRecon MCP tools
---

CodeRecon exposes **14 MCP tools** across 5 groups. Call `describe(action="tool", name="<tool>")` at any time to get full parameter documentation inline.

---

## Orientation

| Tool | Group | Read-only? |
|------|-------|-----------|
| [`describe`](#describe) | Introspection | yes |
| [`recon_map`](#recon_map) | Discovery | yes |
| [`recon`](#recon) | Discovery | yes |
| [`recon_understand`](#recon_understand) | Discovery | yes |
| [`recon_impact`](#recon_impact) | Discovery | yes |
| [`semantic_diff`](#semantic_diff) | Analysis | yes |
| [`graph_cycles`](#graph_cycles) | Analysis | yes |
| [`graph_communities`](#graph_communities) | Analysis | yes |
| [`graph_export`](#graph_export) | Analysis | yes |
| [`refactor_rename`](#refactor_rename) | Refactor | no |
| [`refactor_move`](#refactor_move) | Refactor | no |
| [`refactor_commit`](#refactor_commit) | Refactor | no |
| [`refactor_cancel`](#refactor_cancel) | Refactor | no |
| [`checkpoint`](#checkpoint) | Commit | no |

---

## Introspection

### `describe`

Get parameter documentation for any tool, or decode an error code.

```json
{
  "action": "tool",
  "name": "recon"
}
```

```json
{
  "action": "error",
  "code": "E_INDEX_NOT_READY"
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `action` | `"tool" \| "error"` | Introspection mode |
| `name` | `string?` | Tool name (required for `action="tool"`) |
| `code` | `string?` | Error code (required for `action="error"`) |

---

## Discovery

### `recon_map`

Repository structure map — file tree, language counts, entry points, and PageRank top files and symbols. **Call this first on an unfamiliar repo** to understand what you're working with.

No parameters. Returns:

- `overview` — summary stats
- `tree` — directory tree (depth-3)
- `languages` — language distribution
- `entry_points` — detected entry points
- `pagerank_files` — top 10 most-imported files
- `pagerank_defs` — top 10 most-referenced symbols

---

### `recon`

Task-aware context retrieval. Given a natural language task description, returns ranked semantic spans with code snippets from the most relevant definitions.

```json
{
  "task": "Add retry logic to the HTTP client when rate-limited",
  "seeds": ["HttpClient", "send_request"],
  "pins": ["src/http/client.py"]
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `task` | `string` | required | Natural language task description — be specific, include symbol names |
| `seeds` | `string[]` | `[]` | Symbol names to prioritize in retrieval |
| `pins` | `string[]` | `[]` | File paths to always include |

**Response fields:**

| Field | Description |
|-------|-------------|
| `gate` | Quality gate: `OK` / `UNSAT` / `BROAD` / `AMBIG` |
| `results` | Ranked list of spans — top half include full `snippet`, bottom half `sig` only |
| `metrics` | Retrieval diagnostics |
| `hint` | Actionable follow-up guidance |

!!! tip "Gate meanings"
    - `OK` — good context found, proceed
    - `UNSAT` — task assumptions don't match the codebase
    - `BROAD` — task is too vague, decompose it
    - `AMBIG` — term is ambiguous, specify which subsystem

!!! warning "Required before structural refactors"
    `refactor_rename` and `refactor_move` require a prior `recon` call in the same session.

---

### `recon_understand`

Deep dive on a specific definition — callers, callees, tests, related defs, docstring, and implementation graph.

```json
{
  "def_uid": "src/http/client.py::HttpClient.send_request"
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `def_uid` | `string` | Fully qualified definition UID from a prior `recon` result |

---

### `recon_impact`

Read-only reference analysis — find every place a symbol or file is referenced. Use this before deciding whether a rename/move is worth doing.

```json
{
  "target": "HttpClient",
  "justification": "Planning to rename before refactoring"
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target` | `string` | required | Symbol name or file path |
| `justification` | `string` | required | Why you're running this analysis |
| `include_comments` | `bool` | `true` | Include comment references |

Returns `references` list with `path`, `line`, `match_text`, and `certainty`. **No refactor ID is created — this is always read-only.**

---

## Analysis

### `semantic_diff`

Diff the index between two Git refs, showing which definitions changed and their semantic distance.

```json
{
  "base": "HEAD",
  "target": "feature-branch",
  "paths": ["src/http/"]
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base` | `string` | `"HEAD"` | Base ref (commit SHA, branch, tag, or `epoch:N`) |
| `target` | `string?` | working tree | Target ref |
| `paths` | `string[]?` | all | Limit to specific paths |

---

### `graph_cycles`

Detect import cycles in the codebase. Returns cycle chains ordered by length.

No required parameters. Returns a list of cycles, each as an ordered list of file paths.

---

### `graph_communities`

Cluster files into communities based on import graph structure using the Louvain algorithm. Useful for understanding module boundaries and coupling.

No required parameters. Returns community assignments and modularity score.

---

### `graph_export`

Export the full import graph as a serializable structure for external visualization or analysis.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `format` | `"json" \| "dot"` | `"json"` | Output format |
| `paths` | `string[]?` | all | Limit to subgraph |

---

## Refactor

Structural refactors follow a **preview → commit/cancel** flow. No changes are written until you call `refactor_commit`.

```
refactor_rename  ──→  refactor_commit   (applies changes)
refactor_move    ──→  refactor_cancel   (discards preview)
```

!!! warning "Recon required"
    All refactor tools require a prior `recon` call in the current session.

---

### `refactor_rename`

Rename a symbol across the entire codebase. Produces a preview with per-hunk certainty ratings.

```json
{
  "symbol": "HttpClient",
  "new_name": "RestClient",
  "justification": "Aligning with REST naming conventions",
  "include_comments": true
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | `string` | required | Symbol name to rename (not `path:line:col`) |
| `new_name` | `string` | required | New name |
| `justification` | `string` | required | Why you're renaming |
| `include_comments` | `bool` | `true` | Include comment/string references |
| `contexts` | `string[]?` | all | Limit to specific file paths |

**Preview response includes:**

- `refactor_id` — pass to `refactor_commit` or `refactor_cancel`
- `preview.edits` — per-file hunks with `old`, `new`, `line`, `certainty`
- `preview.verification_required` — true if any low-certainty matches need review
- `display_to_user` — human-readable summary to show the user

---

### `refactor_move`

Move a file/module and automatically update all imports that reference it.

```json
{
  "from_path": "src/http/client.py",
  "to_path": "src/rest/client.py",
  "justification": "Relocating to new module structure"
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from_path` | `string` | required | Source file path |
| `to_path` | `string` | required | Destination file path |
| `justification` | `string` | required | Why you're moving |
| `include_comments` | `bool` | `true` | Include comment references |

---

### `refactor_commit`

Apply a previewed refactoring, or inspect low-certainty matches before committing.

**Apply mode** (no `inspect_path`):

```json
{
  "refactor_id": "abc123"
}
```

**Inspect mode** (review low-certainty matches in a file before committing):

```json
{
  "refactor_id": "abc123",
  "inspect_path": "src/utils/helpers.py",
  "context_lines": 3
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `refactor_id` | `string` | required | ID from the rename/move preview |
| `inspect_path` | `string?` | — | File to inspect instead of applying |
| `context_lines` | `int` | `2` | Context lines shown in inspect mode |

---

### `refactor_cancel`

Discard a pending refactoring preview without making any changes.

```json
{
  "refactor_id": "abc123"
}
```

---

## Commit

### `checkpoint`

All-in-one lint → test → commit → (optional push) workflow. Designed to be the **last step** in a coding task.

```json
{
  "changed_files": ["src/http/client.py", "tests/test_client.py"],
  "commit_message": "refactor: rename HttpClient to RestClient",
  "push": false
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `changed_files` | `string[]` | required | Files modified in this task |
| `commit_message` | `string` | required | Conventional commit message |
| `push` | `bool` | `false` | Push after committing |
| `lint` | `bool` | `true` | Run lint before tests |
| `test` | `bool` | `true` | Run affected tests |
| `coverage` | `bool` | `false` | Collect coverage data |

**What checkpoint does:**

1. Stages `changed_files`
2. Runs pre-commit hooks (with auto-fix retry)
3. Runs lint on changed files
4. Discovers affected test targets via import graph
5. Runs tests in hop tiers (direct first, transitive only if direct pass)
6. Commits with the provided message
7. Optionally pushes

!!! tip "Import-graph tiered testing"
    Tests are run in hop order: files that directly import changed files run first. If those pass, transitive tests run. This makes checkpoint fast for small changes and thorough for large ones.

---

## Typical Agent Workflow

```
1. recon_map            → understand repo structure
2. recon(task=...)      → find relevant code
3. [read files, make edits]
4. recon_impact(...)    → verify nothing unexpected breaks
5. checkpoint(...)      → lint, test, commit
```

For structural renames / moves:

```
1. recon(task=...)
2. recon_impact(target=...)        → see scope
3. refactor_rename / refactor_move → preview
4. refactor_commit                 → apply
5. checkpoint(...)                 → verify & commit
```
