# Agent Instructions

Instructions for AI coding agents working in this repository.

<!-- codeplane-instructions -->
## CodePlane MCP: Mandatory Tool Selection

This repository uses CodePlane MCP. **You MUST use CodePlane tools instead of terminal commands.**

Terminal fallback is permitted ONLY when no CodePlane tool exists for the operation.

### Start Every Task With `recon`

**`recon` is the PRIMARY entry point.** It replaces manual search + read loops.
One call returns SCAFFOLD (imports + signatures), LITE (path + description), and repo_map.

```
recon(task="<describe the task in natural language>", read_only=<True or False>)
```

All parameter details are in the tool schema.

### After Recon: Resolve, Plan, Edit, Checkpoint

1. `recon_resolve(targets=[...], justification="...")` — full content + sha256 (uses candidate_id, not raw paths)
2. `refactor_plan(edit_targets=["<candidate_id>"])` — declare edit set, get plan_id + edit_tickets
3. `refactor_edit(plan_id=..., edits=[...])` — find-and-replace with sha256 locking (one call can edit MULTIPLE files)
4. `checkpoint(changed_files=[...], commit_message="...")` — lint → test → commit → push

**Budget:** 2 mutation batches max before checkpoint. Each `refactor_edit` call = 1 batch.
Batch source + test edits into ONE call. On checkpoint failure: budget RESETS, `fix_plan` with
pre-minted edit tickets returned inline — call `refactor_edit` directly (no new plan needed).

**FORBIDDEN**: `pytest`, `ruff`, `mypy`, `git add`, `git commit`, `git push` in terminal.

### Reviewing Changes

`semantic_diff(base="main")` for structural overview, then `recon_resolve` changed files to review.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Task-aware discovery | `mcp_codeplane-codeplane_recon` | Manual search + read loops |
| Fetch file content | `mcp_codeplane-codeplane_recon_resolve` | `cat`, `head`, `less`, `tail` |
| Edit files | `mcp_codeplane-codeplane_refactor_edit` | `sed`, `echo >>`, `awk`, `tee` |
| Rename symbol | `mcp_codeplane-codeplane_refactor_rename` | Find-and-replace, `sed` |
| Move file | `mcp_codeplane-codeplane_refactor_move` | `mv` + manual import fixup |
| Impact analysis | `mcp_codeplane-codeplane_refactor_impact` | `grep` for references |
| Apply/inspect refactor | `mcp_codeplane-codeplane_refactor_commit` | Manual verification |
| Cancel refactor | `mcp_codeplane-codeplane_refactor_cancel` | — |
| Lint + test + commit | `mcp_codeplane-codeplane_checkpoint` | Running linters/test runners/git directly |
| Structural diff | `mcp_codeplane-codeplane_semantic_diff` | `git diff` for change review |
| Tool/error docs | `mcp_codeplane-codeplane_describe` | Guessing parameter names |

### Before You Edit: Decision Gate

STOP before using `refactor_edit` for multi-file changes:
- Changing a name across files? → `refactor_rename` (NOT refactor_edit + manual fixup)
- Moving a file? → `refactor_move` (NOT refactor_edit + delete)
- Deleting a symbol or file? → `refactor_impact` first

### Refactor: preview → commit/cancel

1. `refactor_rename(symbol="Name", new_name="NewName", justification="...")` — `justification` is **required**
   `refactor_move`/`refactor_impact` — same pattern, preview with `refactor_id`
2. If `verification_required`: `refactor_commit(refactor_id=..., inspect_path=...)` — review low-certainty matches
3. `refactor_commit(refactor_id=...)` to apply, or `refactor_cancel(refactor_id=...)` to discard

### Follow Agentic Hints

`agentic_hint` in responses = **direct instructions for your next action**. Always execute
before proceeding. Also check: `coverage_hint`, `display_to_user`.

If `delivery` = `"sidecar_cache"`, run `agentic_hint` commands to fetch content sections.

### Common Patterns (copy-paste these)

**Read-only research:**
```
recon(task="...", read_only=True)
→ recon_resolve(targets=[{"candidate_id": "<id>"}], justification="...")
```

**Edit a file:**
```
recon(task="...", read_only=False)
→ recon_resolve(targets=[...], justification="...")  # get sha256
→ refactor_plan(edit_targets=["<candidate_id>"])
→ refactor_edit(plan_id="...", edits=[...])          # batch ALL files in ONE call
→ checkpoint(changed_files=["..."])
```

**Rename a symbol:**
```
recon(task="...", read_only=False)
→ refactor_rename(symbol="OldName", new_name="NewName", justification="...")
→ refactor_commit(refactor_id="...", inspect_path="...")  # review low-certainty
→ refactor_commit(refactor_id="...")                      # apply all
```

**Checkpoint fails → fix → retry:**
```
checkpoint(changed_files=["..."]) → FAILED, fix_plan returned inline
→ refactor_edit(plan_id=fix_plan.plan_id, edits=[{
      "edit_ticket": fix_plan.edit_tickets[0].edit_ticket,
      "path": "...", "old_content": "...", "new_content": "...",
      "expected_file_sha256": "..."  # from fix_plan or file_manifest
  }])
→ checkpoint(changed_files=["..."])  # retry
```
Budget resets on failure. `fix_plan` is always in the checkpoint response — no cache read needed.

### Common Mistakes (Don't Do These)

- **DON'T** skip `recon` and manually search+read — `recon` is faster and more complete
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `checkpoint` after `refactor_edit` — always lint + test your changes
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use raw `git add` + `git commit` — use `checkpoint` with `commit_message`
- **DON'T** dismiss lint/test failures as "pre-existing" or "not your problem" — fix ALL issues
- **DON'T** use one `refactor_edit` call per file — batch ALL edits into ONE call
- **DON'T** panic on checkpoint failure — budget resets, use the `fix_plan` tickets provided
<!-- /codeplane-instructions -->
