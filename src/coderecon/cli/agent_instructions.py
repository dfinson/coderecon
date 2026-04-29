"""Agent instruction snippet generation and injection."""
from __future__ import annotations

import re
from pathlib import Path

from coderecon.adapters.files.ops import atomic_write_text

_CODERECON_SNIPPET_MARKER = "<!-- coderecon-instructions -->"

def _make_coderecon_snippet(tool_prefix: str) -> str:
    """Generate the CodeRecon instruction snippet with the actual tool prefix.
    Args:
        tool_prefix: The MCP tool prefix (e.g., 'mcp_coderecon_myrepo')
    """
    # Note: Using {{}} to escape braces that should appear literally in output
    # The f-string only interpolates {tool_prefix}
    return f"""
<!-- coderecon-instructions -->
## CodeRecon MCP

This repository uses CodeRecon MCP for code intelligence and semantic refactoring.

### Start Every Task With `recon`

**`recon` is the PRIMARY entry point.** It replaces manual search + read loops.
One call returns SCAFFOLD (imports + signatures), LITE (path + description), and repo_map.
repo_map lists **every tracked file** — if a path is not in repo_map, the file does not exist.

```
recon(task="<describe the task>", seeds=["SymA", "SymB", ...], read_only=<True or False>)
```

**ONE recon call handles multiple symbols** — put ALL names in `seeds`, never loop.

### After Recon: Read, Edit, Checkpoint

1. Read files via terminal (`cat`, `head`, `sed -n`) using paths from recon scaffolds
2. Edit files using your host's native edit tools
3. `checkpoint(changed_files=[...], commit_message="...")` — lint → test → commit → push

### Reviewing Changes

`semantic_diff(base="main")` for structural overview, then read changed files via terminal.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Task-aware discovery | `{tool_prefix}_recon` | Manual search + read loops |
| Read file content | `cat`, `head`, `sed -n` (terminal) | N/A — terminal reads are allowed |
| Rename symbol | `{tool_prefix}_refactor_rename` | Find-and-replace, `sed` |
| Move file | `{tool_prefix}_refactor_move` | `mv` + manual import fixup |
| Find all references | `{tool_prefix}_recon_impact` | `grep`, `rg`, scaffold iteration |
| Apply/inspect refactor | `{tool_prefix}_refactor_commit` | Manual verification |
| Cancel refactor | `{tool_prefix}_refactor_cancel` | — |
| Lint + test + commit | `{tool_prefix}_checkpoint` | Running linters/test runners/git directly |
| Structural diff | `{tool_prefix}_semantic_diff` | `git diff` for change review |
| Tool/error docs | `{tool_prefix}_describe` | Guessing parameter names |

### Before You Edit: Decision Gate

STOP before editing files manually:
- Changing a name across files? → `refactor_rename` (NOT manual find-and-replace)
- Moving a file? → `refactor_move` (NOT `mv` + manual import fixup)
- Finding all usages of a symbol? → `recon_impact` (NOT grep/scaffold iteration)

### Refactor: preview → commit/cancel

1. `refactor_rename(symbol="Name", new_name="NewName", justification="...")`
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
→ (edit files using host tools)
→ checkpoint(changed_files=["..."], commit_message="...")
```

**Rename a symbol:**
```
recon(task="...", read_only=False)
→ refactor_rename(symbol="OldName", new_name="NewName", justification="...")
→ refactor_commit(refactor_id="...", inspect_path="...")  # review low-certainty
→ refactor_commit(refactor_id="...")                      # apply all
→ checkpoint(changed_files=["..."], commit_message="...")
```

**Find all usages of a symbol (audit/trace):**
```
recon(task="...", seeds=["SymbolName"], read_only=True)
→ recon_impact(target="SymbolName")         # returns ALL reference sites
→ cat src/path/file.py                         # read files you need via terminal
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
<!-- /coderecon-instructions -->
"""

def _inject_agent_instructions(
    repo_root: Path, tool_prefix: str, targets: list[str] | None = None
) -> list[str]:
    """Inject CodeRecon snippet into agent instruction files for each target tool.
    Target → instruction file mapping:
      vscode   → .github/copilot-instructions.md
      claude   → CLAUDE.md
      cursor   → .cursor/rules/coderecon.mdc
      opencode → AGENTS.md
    If the file already exists, the snippet is appended (or an existing
    snippet block is replaced in-place).  If it does not exist the file
    is created with a minimal header.
    Args:
        repo_root: Path to the repository root
        tool_prefix: The MCP tool prefix (e.g., 'mcp_coderecon_myrepo')
        targets: Concrete tool IDs to write for. Defaults to ``["vscode"]`` for
            backward compatibility.
    Returns list of files that were created or updated (relative to repo_root,
    except for global paths which are returned as absolute strings).
    """
    if targets is None:
        targets = ["vscode"]
    modified: list[str] = []
    snippet = _make_coderecon_snippet(tool_prefix)
    tool_targets: dict[str, tuple[Path, str]] = {
        "vscode": (
            repo_root / ".github" / "copilot-instructions.md",
            "# Copilot Instructions\n\nInstructions for GitHub Copilot working in this repository.\n",
        ),
        "claude": (
            repo_root / "CLAUDE.md",
            "# Claude Instructions\n\nInstructions for Claude Code working in this repository.\n",
        ),
        "cursor": (
            repo_root / ".cursor" / "rules" / "coderecon.mdc",
            "---\ndescription: CodeRecon MCP instructions\n---\n\n",
        ),
        "opencode": (
            repo_root / "AGENTS.md",
            "# Agent Instructions\n\nInstructions for AI agents working in this repository.\n",
        ),
    }
    for tool in targets:
        if tool not in tool_targets:
            continue
        target, header = tool_targets[tool]
        if target.exists():
            content = target.read_text()
            if _CODERECON_SNIPPET_MARKER in content:
                new_content = re.sub(
                    r"<!-- coderecon-instructions -->.*?<!-- /coderecon-instructions -->",
                    snippet.strip(),
                    content,
                    flags=re.DOTALL,
                )
                if new_content != content:
                    atomic_write_text(target, new_content)
                    try:
                        modified.append(str(target.relative_to(repo_root)))
                    except ValueError:
                        modified.append(str(target))
            else:
                new_content = content.rstrip() + "\n" + snippet
                atomic_write_text(target, new_content)
                try:
                    modified.append(str(target.relative_to(repo_root)))
                except ValueError:
                    modified.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, header + snippet)
            try:
                modified.append(str(target.relative_to(repo_root)))
            except ValueError:
                modified.append(str(target))
    return modified
