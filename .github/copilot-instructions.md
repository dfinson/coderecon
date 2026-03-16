# Copilot Instructions

Authority: SPEC.md wins. If unsure or there is a spec conflict, stop and ask.

---

## 1) No Hacks (Root Cause Only)

If something fails, diagnose and fix it properly. Do not "make it pass".

Forbidden:
- # type: ignore, Any, dishonest cast()
- try/except or inline imports to dodge module issues
- regex or string parsing for structured data
- raw SQL to bypass ORM or typing
- empty except blocks or silent fallbacks
- "for now" workarounds

If you cannot solve it correctly with available tools or information, say so and ask.

## 2) All Checks Must Pass

Lint, typecheck, tests, and CI must be green.

## 3) GitHub Remote Actions Must Be Exact

When asked to perform a specific remote action (merge, resolve threads, release, etc.):
- do exactly that action, or
- state it is not possible with available tools

No substitutions.

## 4) Change Discipline (Minimal)

- Before coding: read the issue, relevant SPEC.md sections, and match repo patterns
- Prefer minimal code; do not invent abstractions or reimplement libraries
- Tests should be small, behavioral, and parameterized when appropriate

## 5) NEVER Reset Hard Without Approval

**ABSOLUTE PROHIBITION**: Never execute `git reset --hard` under any circumstances without explicit user approval.

This applies to:
- `git reset --hard` (any ref)
- Any equivalent destructive operation that discards uncommitted changes

If you believe a hard reset is needed:
1. STOP and explain why you think it's necessary
2. List what uncommitted work will be lost
3. Wait for explicit user confirmation before proceeding

Violating this rule destroys work irreversibly and may affect parallel agent workflows.

<!-- codeplane-instructions -->
## CodePlane MCP: Mandatory Tool Selection

This repository uses CodePlane MCP.

### ⛔ NEVER Use Terminal to Bypass CodePlane ⛔

**Every file read, search, edit, delete, git operation, lint, and test run MUST go through
CodePlane tools — NEVER through terminal commands.** Violations break the mutation ledger
and corrupt the index.

**Explicitly banned** (non-exhaustive — if a CodePlane tool can do it, the terminal MUST NOT):
- `cat`, `head`, `tail`, `less`, `sed -n`, `bat` → allowed for reading files after `recon`
- `grep`, `rg`, `find`, `ag`, `wc`, `ls` → use `recon`
- `sed -i`, `awk`, `echo >>`, `tee`, `perl -i` → use `refactor_edit`
- `rm`, `git rm` → use `refactor_edit(delete=True)`
- `mv` → use `refactor_move` or `refactor_rename`
- `git add`, `git commit`, `git push`, `git diff`, `git status`, `git log` → use `checkpoint` or `semantic_diff`
- `pytest`, `python -m pytest`, `ruff`, `mypy`, `flake8`, `black` → use `checkpoint`

**Allowed terminal use (exhaustive):** `jq` for sidecar cache reads per `agentic_hint`,
package installation, running the user's application, and operations with genuinely no
CodePlane equivalent (`docker`, `curl` to external services, etc.).

### Start Every Task With `recon`

**`recon` is the PRIMARY entry point.** It replaces manual search + read loops.
One call returns SCAFFOLD (imports + signatures), LITE (path + description), and repo_map.
repo_map lists **every tracked file** — if a path is not in repo_map, the file does not exist.

```
recon(task="<describe the task>", seeds=["SymA", "SymB", ...], read_only=<True or False>)
```

**ONE recon call handles multiple symbols** — put ALL names in `seeds`, never loop.

**Recon is hard-gated to 1 call per task.** The 2nd call is blocked unconditionally.
Read files via terminal (`cat`, `head`) using paths from scaffolds. A gate escape (gate_token)
is issued on the 2nd block for emergencies only.

### After Recon: Read, Plan, Edit, Checkpoint

1. Read files via terminal (`cat`, `head`, `sed -n`) using paths from recon scaffolds
2. `refactor_plan(edit_targets=["<candidate_id>"])` — declare edit set, get plan_id + edit_tickets (sha256 computed from disk)
3. `refactor_edit(plan_id=..., edits=[...])` — find-and-replace with sha256 locking (one call can edit MULTIPLE files)
4. `checkpoint(changed_files=[...], commit_message="...")` — lint → test → commit → push

**Budget:** 4 mutation batches max before checkpoint. Each `refactor_edit` call = 1 batch.
Batch source + test edits into ONE call. On checkpoint failure: budget RESETS, `fix_plan` with
pre-minted edit tickets returned inline — call `refactor_edit` directly (no new plan needed).

### Reviewing Changes

`semantic_diff(base="main")` for structural overview, then read changed files via terminal.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Task-aware discovery | `mcp_codeplane-codeplane_recon` | Manual search + read loops |
| Read file content | `cat`, `head`, `sed -n` (terminal) | N/A — terminal reads are allowed |
| Edit files | `mcp_codeplane-codeplane_refactor_edit` | `sed`, `echo >>`, `awk`, `tee` |
| Delete file | `mcp_codeplane-codeplane_refactor_edit(delete=True)` | `git rm`, `rm` |
| Rename symbol | `mcp_codeplane-codeplane_refactor_rename` | Find-and-replace, `sed` |
| Move file | `mcp_codeplane-codeplane_refactor_move` | `mv` + manual import fixup |
| Find all references | `mcp_codeplane-codeplane_recon_impact` | `grep`, `rg`, scaffold iteration |
| Apply/inspect refactor | `mcp_codeplane-codeplane_refactor_commit` | Manual verification |
| Cancel refactor | `mcp_codeplane-codeplane_refactor_cancel` | — |
| Lint + test + commit | `mcp_codeplane-codeplane_checkpoint` | Running linters/test runners/git directly |
| Structural diff | `mcp_codeplane-codeplane_semantic_diff` | `git diff` for change review |
| Tool/error docs | `mcp_codeplane-codeplane_describe` | Guessing parameter names |

### Before You Edit: Decision Gate

STOP before using `refactor_edit` for multi-file changes:
- Changing a name across files? → `refactor_rename` (NOT refactor_edit + manual fixup)
- Moving a file? → `refactor_move` (NOT refactor_edit + delete)
- Deleting a file? → `recon_impact` first, then `refactor_edit(delete=True)`
- Finding all usages of a symbol? → `recon_impact` (NOT grep/scaffold iteration)

### Refactor: preview → commit/cancel

1. `refactor_rename(symbol="Name", new_name="NewName", justification="...")` — `justification` is **required**
   `refactor_move` — same pattern, preview with `refactor_id`
2. If `verification_required`: `refactor_commit(refactor_id=..., inspect_path=...)` — review low-certainty matches
3. `refactor_commit(refactor_id=...)` to apply, or `refactor_cancel(refactor_id=...)` to discard

### Follow Agentic Hints

`agentic_hint` in responses = **direct instructions for your next action**. Always execute
before proceeding. Also check: `coverage_hint`, `display_to_user`.

If `delivery` = `"sidecar_cache"`, run `agentic_hint` commands **verbatim** to fetch content.
Cache keys: `candidates` (file list with .id), `scaffold:<path>` (imports + signatures),
`lite:<path>` (path + description), `repo_map` (every tracked file — file inventory only).
**repo_map** = file existence check. **scaffold** = code structure. **recon_impact** = symbol usages.

### Common Patterns (copy-paste these)

**Read-only research:**
```
recon(task="...", read_only=True)
→ cat src/path/file.py                               # read via terminal
→ checkpoint(changed_files=[])                      # reset session state
```

**Edit a file:**
```
recon(task="...", read_only=False)
→ cat src/path/file.py                               # read via terminal
→ refactor_plan(edit_targets=["<candidate_id>"])     # sha256 computed from disk
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

**Find all usages of a symbol (audit/trace):**
```
recon(task="...", seeds=["SymbolName"], read_only=True)
→ recon_impact(target="SymbolName")         # returns ALL reference sites
→ cat src/path/file.py                         # read files you need via terminal
```

**Delete a file:**
```
recon(task="...", read_only=False)
→ recon_impact(target="file/to/delete.py")          # check dependents first
→ refactor_plan(edit_targets=["<candidate_id>"])
→ refactor_edit(plan_id="...", edits=[{
      "edit_ticket": "...", "path": "file/to/delete.py",
      "delete": true, "expected_file_sha256": "..."
  }])
→ checkpoint(changed_files=["file/to/delete.py"])
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
- **DON'T** call `recon` in a loop (once per symbol) — put ALL symbols in `seeds` in ONE call
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `checkpoint` after `refactor_edit` — always lint + test your changes
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** dismiss lint/test failures as "pre-existing" or "not your problem" — fix ALL issues
- **DON'T** use one `refactor_edit` call per file — batch ALL edits into ONE call
- **DON'T** panic on checkpoint failure — budget resets, use the `fix_plan` tickets provided
- **DON'T** grep/filter scaffold metadata to find files — scaffolds are a TABLE OF CONTENTS,
  not a search index. Use `recon_impact` to find all usages of a symbol
- **DON'T** skip `checkpoint(changed_files=[])` after read-only flows — session state
  (recon gate, mutation budget) carries over and blocks the next task
<!-- /codeplane-instructions -->
