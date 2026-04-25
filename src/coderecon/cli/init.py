"""recon init command - initialize a repository for CodeRecon."""

import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import click
import json5
import structlog
from rich.table import Table

from coderecon.config.user_config import (
    DEFAULT_PORT,
    RuntimeState,
    UserConfig,
    load_user_config,
    write_runtime_state,
    write_user_config,
)
from coderecon.core.progress import (
    PhaseBox,
    get_console,
    phase_box,
    status,
)
from coderecon.templates import get_reconignore_template

log = structlog.get_logger(__name__)
# =============================================================================
# Agent Instruction Snippet
# =============================================================================

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
    import re

    if targets is None:
        targets = ["vscode"]

    modified: list[str] = []
    snippet = _make_coderecon_snippet(tool_prefix)

    # Map each tool to (path, header) — path is absolute
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
                    target.write_text(new_content)
                    try:
                        modified.append(str(target.relative_to(repo_root)))
                    except ValueError:
                        modified.append(str(target))
            else:
                new_content = content.rstrip() + "\n" + snippet
                target.write_text(new_content)
                try:
                    modified.append(str(target.relative_to(repo_root)))
                except ValueError:
                    modified.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(header + snippet)
            try:
                modified.append(str(target.relative_to(repo_root)))
            except ValueError:
                modified.append(str(target))

    return modified


# =============================================================================
# VS Code MCP Configuration
# =============================================================================


def _get_mcp_server_name(repo_root: Path) -> str:
    """Get the normalized MCP server name for a repo."""
    repo_name = repo_root.name
    normalized = repo_name.lower().replace(".", "_").replace("-", "_")
    return f"coderecon-{normalized}"


def _ensure_vscode_mcp_config(repo_root: Path, port: int) -> tuple[bool, str]:
    """Ensure .vscode/mcp.json has the CodeRecon server entry with static port.

    Creates or updates the MCP server entry with the actual port number.
    Call sync_vscode_mcp_port() from 'recon up' to update port if changed.

    Returns tuple of (was_modified, server_name).
    """
    vscode_dir = repo_root / ".vscode"
    mcp_json_path = vscode_dir / "mcp.json"
    server_name = _get_mcp_server_name(repo_root)

    expected_url = f"http://127.0.0.1:{port}/mcp"
    expected_config: dict[str, Any] = {
        "type": "http",
        "url": expected_url,
    }

    if mcp_json_path.exists():
        content = mcp_json_path.read_text()
        try:
            existing: dict[str, Any] = json5.loads(content)
        except ValueError:
            # Unparseable JSONC — don't risk overwriting existing servers
            status(
                "Warning: .vscode/mcp.json is not valid JSON(C), skipping update",
                style="warning",
            )
            return False, server_name

        servers = existing.get("servers", {})

        # Check if our server entry already exists with correct config
        if server_name in servers:
            current_url = servers[server_name].get("url", "")

            # If URL matches exactly, no change needed
            if current_url == expected_url:
                return False, server_name

            # Update with new native HTTP config
            servers[server_name] = expected_config
        else:
            # Add new server entry
            servers[server_name] = expected_config

        existing["servers"] = servers
        output = json.dumps(existing, indent=2) + "\n"
        mcp_json_path.write_text(output)
        return True, server_name
    else:
        # Create new mcp.json
        vscode_dir.mkdir(parents=True, exist_ok=True)
        config = {"servers": {server_name: expected_config}}
        output = json.dumps(config, indent=2) + "\n"
        mcp_json_path.write_text(output)
        return True, server_name


def sync_vscode_mcp_port(repo_root: Path, port: int) -> bool:
    """Update port in .vscode/mcp.json if it differs from configured port.

    Called by 'recon up' to ensure mcp.json matches the running server port.
    Returns True if file was modified.
    """
    mcp_json_path = repo_root / ".vscode" / "mcp.json"
    if not mcp_json_path.exists():
        # Create mcp.json if it doesn't exist
        return _ensure_vscode_mcp_config(repo_root, port)[0]

    server_name = _get_mcp_server_name(repo_root)
    expected_url = f"http://127.0.0.1:{port}/mcp"

    content = mcp_json_path.read_text()
    try:
        existing: dict[str, Any] = json5.loads(content)
    except ValueError:
        # Unparseable JSONC — don't risk overwriting existing servers
        return False

    servers = existing.get("servers", {})
    if server_name not in servers:
        # Our server entry doesn't exist, add it
        servers[server_name] = {
            "type": "http",
            "url": expected_url,
        }
        existing["servers"] = servers
        output = json.dumps(existing, indent=2) + "\n"
        mcp_json_path.write_text(output)
        return True

    current_url = servers[server_name].get("url", "")

    if current_url == expected_url:
        return False

    # Update config to native HTTP format
    # Preserve existing settings (headers, env, etc.) while updating type/url
    existing_entry = servers.get(server_name, {})
    if isinstance(existing_entry, dict):
        existing_entry["type"] = "http"
        existing_entry["url"] = expected_url
        servers[server_name] = existing_entry
    else:
        servers[server_name] = {"type": "http", "url": expected_url}
    existing["servers"] = servers
    output = json.dumps(existing, indent=2) + "\n"
    mcp_json_path.write_text(output)
    return True


# =============================================================================
# Filesystem Helpers
# =============================================================================


def _is_cross_filesystem(path: Path) -> bool:
    """Detect if path is on a cross-filesystem mount (WSL /mnt/*, network drives, etc.)."""
    resolved = path.resolve()
    path_str = str(resolved)
    # WSL accessing Windows filesystem
    if path_str.startswith("/mnt/") and len(path_str) > 5 and path_str[5].isalpha():
        return True
    # Common network/remote mounts
    return path_str.startswith(("/run/user/", "/media/", "/net/"))


def _get_xdg_index_dir(repo_root: Path) -> Path:
    """Get XDG-compliant index directory for a repo."""
    xdg_data = Path.home() / ".local" / "share" / "coderecon" / "indices"
    repo_hash = hashlib.sha256(str(repo_root.resolve()).encode()).hexdigest()[:12]
    return xdg_data / repo_hash


def initialize_repo(
    repo_root: Path,
    *,
    reindex: bool = False,
    show_recon_up_hint: bool = True,
    port: int | None = None,
    mcp_targets: list[str] | None = None,
) -> bool:
    """Initialize a repository for CodeRecon, returning True on success.

    Args:
        repo_root: Path to the repository root
        reindex: Wipe and rebuild the entire index from scratch
        show_recon_up_hint: Show "Run 'recon up'" hint at end (False when auto-init from recon up)
        port: Override port (persisted to config.yaml). If None, preserves existing or uses default.
        mcp_targets: List of tool IDs to write MCP configs for (e.g. ``["vscode", "claude"]``).
            Accepts the pseudo-targets ``"auto"`` and ``"all"``.  Defaults to ``["auto"]`` which
            auto-detects installed tools.
    """
    coderecon_dir = repo_root / ".recon"
    console = get_console()

    if coderecon_dir.exists() and not reindex:
        status(f"Already initialized: {coderecon_dir}", style="info")
        status("Use --reindex to rebuild the index", style="info")
        return False

    console.print()
    status(f"Initializing CodeRecon in {repo_root}", style="none")
    console.print()

    # Determine port: CLI override > existing config > default
    config_path = coderecon_dir / "config.yaml"
    if port is not None:
        # Explicit port override from CLI
        final_port = port
    elif config_path.exists():
        # Preserve existing config port (for reindex without --port)
        existing_config = load_user_config(config_path)
        final_port = existing_config.port
    else:
        # Fresh init with no port specified
        final_port = DEFAULT_PORT

    # If reindex is set, remove existing data completely to start fresh
    if reindex:
        import shutil

        if coderecon_dir.exists():
            shutil.rmtree(coderecon_dir)
        # Also clear XDG index directory (for cross-filesystem setups like WSL)
        xdg_index_dir = _get_xdg_index_dir(repo_root)
        if xdg_index_dir.exists():
            shutil.rmtree(xdg_index_dir)

    coderecon_dir.mkdir(exist_ok=True)

    # Determine index storage location before writing config
    # Cross-filesystem paths (WSL /mnt/*) need index on native filesystem
    index_dir: Path
    if _is_cross_filesystem(repo_root):
        index_dir = _get_xdg_index_dir(repo_root)
        index_dir.mkdir(parents=True, exist_ok=True)
        status(
            f"Cross-filesystem detected, storing index at: {index_dir}",
            style="info",
        )
    else:
        index_dir = coderecon_dir

    # Write user config
    write_user_config(config_path, UserConfig(port=final_port))

    # Write runtime state (index_path) - auto-generated, not user-editable
    state_path = coderecon_dir / "state.yaml"
    write_runtime_state(state_path, RuntimeState(index_path=str(index_dir)))

    reconignore_path = coderecon_dir / ".reconignore"
    if not reconignore_path.exists() or reindex:
        reconignore_path.write_text(get_reconignore_template())

    # Merge repo-root .reconignore if it exists — user patterns survive reindex
    root_reconignore = repo_root / ".reconignore"
    if root_reconignore.exists():
        generated = reconignore_path.read_text()
        root_lines = root_reconignore.read_text().strip()
        if root_lines:
            # Collect patterns not already in the generated template
            existing = set(generated.splitlines())
            new_patterns = [
                ln for ln in root_lines.splitlines()
                if ln.strip() and ln not in existing
            ]
            if new_patterns:
                merged = generated.rstrip() + "\n\n# From repo-root .reconignore\n"
                merged += "\n".join(new_patterns) + "\n"
                reconignore_path.write_text(merged)

    # Create .gitignore to exclude artifacts from version control per SPEC.md §7.7
    gitignore_path = coderecon_dir / ".gitignore"
    if not gitignore_path.exists() or reindex:
        gitignore_path.write_text(
            "# Ignore everything except user config files\n"
            "*\n"
            "!.gitignore\n"
            "!config.yaml\n"
            "# state.yaml is auto-generated, do not commit\n"
        )

    # === IDE & Agent Integration ===
    from coderecon.cli.mcp_writers import resolve_targets, write_mcp_configs

    server_name = _get_mcp_server_name(repo_root)
    resolved = resolve_targets(mcp_targets or ["auto"], repo_root)
    written_configs = write_mcp_configs(repo_root, final_port, server_name, resolved)
    for cfg in written_configs:
        status(f"Updated {cfg} with CodeRecon server", style="info")

    # Derive tool prefix from server_name: VS Code creates tools as mcp_{server_name}_{tool}
    # server_name is already normalized (lowercase, underscores)
    tool_prefix = f"mcp_{server_name}"

    # Inject CodeRecon instructions into agent instruction files
    modified_agent_files = _inject_agent_instructions(repo_root, tool_prefix, resolved)
    if modified_agent_files:
        for f in modified_agent_files:
            status(f"Updated {f} with CodeRecon instructions", style="info")

    # === Discovery Phase ===
    from coderecon.index._internal.grammars import (
        get_needed_grammars,
        install_grammars,
        scan_repo_languages,
    )

    with phase_box("Discovery", width=60) as phase:
        # Step 1: Scan languages
        task_id = phase.add_progress("Scanning", total=100)
        languages = scan_repo_languages(repo_root)
        phase.advance(task_id, 100)
        # Use .value to get string name, not enum repr
        lang_names = ", ".join(sorted(lang.value for lang in languages)) if languages else "none"
        phase.complete(f"{len(languages)} languages: {lang_names}")

        # Step 2: Install grammars if needed
        needed = get_needed_grammars(languages)
        if needed:
            task_id = phase.add_progress("Installing grammars", total=len(needed))
            grammar_result = install_grammars(needed, quiet=True, status_fn=None)
            phase.advance(task_id, len(needed))
            if grammar_result.installed_packages:
                # Log which grammars were installed
                installed_langs = [
                    pkg.replace("tree-sitter-", "").replace("tree_sitter_", "")
                    for pkg in grammar_result.installed_packages
                ]
                phase.complete(f"Installed: {', '.join(installed_langs)}")
            else:
                phase.complete("Grammars ready")

    # === GPU Detection ===
    from coderecon.core.gpu import probe_gpu

    gpu_result = probe_gpu()
    if gpu_result.has_onnx_gpu:
        provider = gpu_result.onnx_gpu_providers[0].replace("ExecutionProvider", "")
        status(f"GPU acceleration: {provider}", style="success")
    elif gpu_result.gpu_available_but_not_configured:
        hint = gpu_result.install_hint
        status(f"GPU detected ({gpu_result.provider_name}) but ONNX GPU provider not installed", style="info")
        if hint:
            status(f"  Enable GPU: {hint}", style="info")
    # else: no GPU, say nothing — CPU is the default

    # === Lexical Indexing Phase ===
    from coderecon.index.ops import IndexCoordinatorEngine

    db_path = index_dir / "index.db"
    tantivy_path = index_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    coord = IndexCoordinatorEngine(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    # Shared state for phase transitions
    indexing_state: dict[str, object] = {
        "indexing_done": False,
        "files_indexed": 0,
        "files_by_ext": {},
    }
    # Track resolution phase box and task IDs
    resolution_phase: PhaseBox | None = None
    refs_task_id: Any = None
    types_task_id: Any = None
    splade_phase: PhaseBox | None = None
    splade_task_id: Any = None
    indexing_elapsed = 0.0

    try:
        import time

        start_time = time.time()

        # Phase box 1: Indexing (unified file processing)
        indexing_phase = phase_box("Indexing", width=60)
        indexing_phase.__enter__()
        indexing_task_id = indexing_phase.add_progress("Indexing files", total=100)

        def on_index_progress(
            indexed: int, total: int, files_by_ext: dict[str, int], progress_phase: str
        ) -> None:
            nonlocal resolution_phase
            nonlocal refs_task_id, types_task_id, indexing_elapsed
            nonlocal splade_phase, splade_task_id

            if progress_phase == "indexing":
                # Update indexing phase box
                pct = int(indexed / total * 100) if total > 0 else 0
                indexing_phase._progress.update(indexing_task_id, completed=pct)  # type: ignore[union-attr]
                indexing_phase._update()

                if files_by_ext:
                    table = _make_init_extension_table(files_by_ext)
                    indexing_phase.set_live_table(table)

                # Store latest state
                indexing_state["files_indexed"] = indexed
                indexing_state["files_by_ext"] = files_by_ext

            elif progress_phase in ("resolving_cross_file", "resolving_refs", "resolving_types"):
                # First resolution callback — close indexing box, open resolution box
                if not indexing_state["indexing_done"]:
                    indexing_state["indexing_done"] = True
                    indexing_elapsed = time.time() - start_time

                    # Finalize indexing box
                    indexing_phase.set_live_table(None)
                    files = indexing_state["files_indexed"]
                    indexing_phase.complete(f"{files} files ({indexing_elapsed:.1f}s)")
                    if indexing_state["files_by_ext"]:
                        indexing_phase.add_text("")
                        ext_table = _make_init_extension_table(indexing_state["files_by_ext"])  # type: ignore[arg-type]
                        indexing_phase.add_table(ext_table)
                    indexing_phase.__exit__(None, None, None)

                if resolution_phase is None:
                    # Open resolution phase box
                    resolution_phase = phase_box("Resolution", width=60)
                    resolution_phase.__enter__()

                if progress_phase == "resolving_refs":
                    if resolution_phase is not None:
                        if refs_task_id is None:
                            refs_task_id = resolution_phase.add_progress(
                                "Resolving imports", total=100
                            )
                        pct = int(indexed / total * 100) if total > 0 else 0
                        resolution_phase._progress.update(refs_task_id, completed=pct)  # type: ignore[union-attr]
                        resolution_phase._update()

                elif progress_phase == "resolving_types" and resolution_phase is not None:
                    if types_task_id is None:
                        types_task_id = resolution_phase.add_progress("Resolving types", total=100)
                    pct = int(indexed / total * 100) if total > 0 else 0
                    resolution_phase._progress.update(types_task_id, completed=pct)  # type: ignore[union-attr]
                    resolution_phase._update()

            elif progress_phase == "encoding_splade":
                # Close resolution phase if still open
                if resolution_phase is not None:
                    total_elapsed = time.time() - start_time
                    resolution_elapsed = total_elapsed - indexing_elapsed
                    resolution_phase.complete(f"Done ({resolution_elapsed:.1f}s)")
                    resolution_phase.__exit__(None, None, None)
                    resolution_phase = None

                if splade_phase is None:
                    splade_phase = phase_box("SPLADE encoding", width=60)
                    splade_phase.__enter__()
                    splade_task_id = splade_phase.add_progress("Encoding vectors", total=100)

                pct = int(indexed / total * 100) if total > 0 else 0
                splade_phase._progress.update(splade_task_id, completed=pct)  # type: ignore[union-attr]
                splade_phase._update()

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(coord.initialize(on_index_progress=on_index_progress))
        finally:
            loop.close()

        # Handle case where there were no resolution phases (shouldn't happen normally)
        if not indexing_state["indexing_done"]:
            indexing_elapsed = time.time() - start_time
            indexing_phase.set_live_table(None)
            indexing_phase.complete(f"{result.files_indexed} files ({indexing_elapsed:.1f}s)")
            if result.files_by_ext:
                indexing_phase.add_text("")
                ext_table = _make_init_extension_table(result.files_by_ext)
                indexing_phase.add_table(ext_table)
            indexing_phase.__exit__(None, None, None)

        # Close resolution phase box if it was opened
        if resolution_phase is not None:
            total_elapsed = time.time() - start_time
            resolution_elapsed = total_elapsed - indexing_elapsed
            resolution_phase.complete(f"Done ({resolution_elapsed:.1f}s)")
            resolution_phase.__exit__(None, None, None)

        # Close SPLADE phase box if it was opened
        if splade_phase is not None:
            splade_phase.complete("Done")
            splade_phase.__exit__(None, None, None)

        if result.errors:
            for err in result.errors:
                status(f"Error: {err}", style="error")
            return False

        # Step 11: Collect initial test coverage (best-effort)
        # Load testing config for memory-aware execution
        from coderecon.config.loader import load_config as _load_config

        _cfg = _load_config(repo_root)

        cov_loop = asyncio.new_event_loop()
        try:
            cov_facts = cov_loop.run_until_complete(
                coord.collect_initial_coverage(
                    parallelism=_cfg.testing.default_parallelism,
                    memory_reserve_mb=_cfg.testing.memory_reserve_mb,
                    subprocess_memory_limit_mb=_cfg.testing.subprocess_memory_limit_mb,
                )
            )
        finally:
            cov_loop.close()
        if cov_facts:
            status(f"Coverage: {cov_facts} test→def links ingested", style="success")

    finally:
        coord.close()

    # Final config confirmation
    console.print()
    rel_config_path = config_path.relative_to(repo_root)
    status(f"Config created at {rel_config_path}", style="success")

    if show_recon_up_hint:
        console.print()
        status("Ready. Run 'recon up' to start the server.", style="none")

    return True


def _make_init_extension_table(files_by_ext: dict[str, int]) -> Table:
    """Create extension breakdown table for init output."""
    sorted_exts = sorted(files_by_ext.items(), key=lambda x: -x[1])
    if not sorted_exts:
        return Table(show_header=False, box=None)

    max_count = sorted_exts[0][1]
    max_sqrt = math.sqrt(max_count) if max_count > 0 else 1

    table = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False)
    table.add_column("ext", style="cyan", width=8)
    table.add_column("count", style="white", justify="right", width=4)
    table.add_column("bar", width=20)

    for ext, count in sorted_exts[:8]:
        bar_width = max(1, int(math.sqrt(count) / max_sqrt * 20)) if max_sqrt > 0 else 1
        bar = f"[green]{'━' * bar_width}[/green][dim]{'━' * (20 - bar_width)}[/dim]"
        table.add_row(ext, str(count), bar)

    rest = sorted_exts[8:]
    if rest:
        rest_count = sum(c for _, c in rest)
        bar_width = max(1, int(math.sqrt(rest_count) / max_sqrt * 20)) if max_sqrt > 0 else 1
        bar = f"[dim green]{'━' * bar_width}[/dim green][dim]{'━' * (20 - bar_width)}[/dim]"
        table.add_row("other", str(rest_count), bar, style="dim")

    return table


@click.command(hidden=True)
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "-r", "--reindex", is_flag=True, help="Wipe and rebuild the entire index from scratch"
)
@click.option("--port", "-p", type=int, help="Server port (persisted to config.yaml)")
@click.option(
    "--mcp-target", "-t",
    "mcp_targets",
    multiple=True,
    type=click.Choice(["vscode", "claude", "cursor", "opencode", "auto", "all"]),
    default=("auto",),
    show_default=True,
    help=(
        "AI tool(s) to write MCP config for. "
        "Use 'auto' to detect installed tools, 'all' for every supported tool. "
        "Repeat to target multiple tools, e.g. -t vscode -t claude."
    ),
)
def init_command(path: Path | None, reindex: bool, port: int | None, mcp_targets: tuple[str, ...]) -> None:
    """(Use 'recon register' instead.) Initialize a repository for CodeRecon management."""
    from coderecon.cli.utils import find_repo_root

    repo_root = find_repo_root(path)

    if not initialize_repo(repo_root, reindex=reindex, port=port, mcp_targets=list(mcp_targets)):
        if not reindex:
            return  # Already initialized, message printed
        raise SystemExit(1)  # Errors occurred
