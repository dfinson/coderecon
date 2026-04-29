"""recon init command - initialize a repository for CodeRecon."""
from __future__ import annotations

import asyncio
import hashlib
import math
from pathlib import Path
from typing import TYPE_CHECKING

import click
import structlog
from rich.table import Table

if TYPE_CHECKING:
    from rich.progress import TaskID

from coderecon.config.user_config import (
    DEFAULT_PORT,
    RuntimeState,
    UserConfig,
    load_user_config,
    write_runtime_state,
    write_user_config,
)
from coderecon._core.progress import (
    PhaseBox,
    get_console,
    phase_box,
    status,
)
from coderecon.adapters.files.ops import atomic_write_text
from coderecon._core.excludes import get_reconignore_template

log = structlog.get_logger(__name__)

from coderecon.cli.agent_instructions import (  # noqa: E402
    _inject_agent_instructions,
)
from coderecon.cli.mcp_config import (  # noqa: E402
    _get_mcp_server_name,
)

# Filesystem Helpers

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
    _write_init_files(
        repo_root, coderecon_dir, config_path, final_port, reindex,
        mcp_targets, console,
    )
    _run_discovery_phase(repo_root)
    # else: no GPU, say nothing — CPU is the default
    return _run_indexing_phase(
        repo_root, index_dir, config_path, console, show_recon_up_hint,
    )

def _write_init_files(
    repo_root: Path,
    coderecon_dir: Path,
    config_path: Path,
    final_port: int,
    reindex: bool,
    mcp_targets: list[str] | None,
    console: object,
) -> None:
    """Write config, reconignore, gitignore, IDE and agent integration files."""
    write_user_config(config_path, UserConfig(port=final_port))
    state_path = coderecon_dir / "state.yaml"
    # Determine index_dir from runtime state
    index_dir = coderecon_dir
    if _is_cross_filesystem(repo_root):
        index_dir = _get_xdg_index_dir(repo_root)
    write_runtime_state(state_path, RuntimeState(index_path=str(index_dir)))
    reconignore_path = coderecon_dir / ".reconignore"
    if not reconignore_path.exists() or reindex:
        atomic_write_text(reconignore_path, get_reconignore_template())
    root_reconignore = repo_root / ".reconignore"
    if root_reconignore.exists():
        generated = reconignore_path.read_text()
        root_lines = root_reconignore.read_text().strip()
        if root_lines:
            existing = set(generated.splitlines())
            new_patterns = [
                ln for ln in root_lines.splitlines()
                if ln.strip() and ln not in existing
            ]
            if new_patterns:
                merged = generated.rstrip() + "\n\n# From repo-root .reconignore\n"
                merged += "\n".join(new_patterns) + "\n"
                atomic_write_text(reconignore_path, merged)
    gitignore_path = coderecon_dir / ".gitignore"
    if not gitignore_path.exists() or reindex:
        atomic_write_text(
            gitignore_path,
            "# Ignore everything except user config files\n"
            "*\n"
            "!.gitignore\n"
            "!config.yaml\n"
            "# state.yaml is auto-generated, do not commit\n",
        )
    from coderecon.cli.mcp_writers import resolve_targets, write_mcp_configs
    server_name = _get_mcp_server_name(repo_root)
    resolved = resolve_targets(mcp_targets or ["auto"], repo_root)
    written_configs = write_mcp_configs(repo_root, final_port, server_name, resolved)
    for cfg in written_configs:
        status(f"Updated {cfg} with CodeRecon server", style="info")
    tool_prefix = f"mcp_{server_name}"
    modified_agent_files = _inject_agent_instructions(repo_root, tool_prefix, resolved)
    if modified_agent_files:
        for f in modified_agent_files:
            status(f"Updated {f} with CodeRecon instructions", style="info")


def _run_discovery_phase(repo_root: Path) -> None:
    """Scan languages, install grammars, and probe GPU."""
    from coderecon.index.parsing.grammars import (
        get_needed_grammars,
        install_grammars,
        scan_repo_languages,
    )
    with phase_box("Discovery", width=60) as phase:
        task_id = phase.add_progress("Scanning", total=100)
        languages = scan_repo_languages(repo_root)
        phase.advance(task_id, 100)
        lang_names = ", ".join(sorted(lang.value for lang in languages)) if languages else "none"
        phase.complete(f"{len(languages)} languages: {lang_names}")
        needed = get_needed_grammars(languages)
        if needed:
            task_id = phase.add_progress("Installing grammars", total=len(needed))
            grammar_result = install_grammars(needed, quiet=True, status_fn=None)
            phase.advance(task_id, len(needed))
            if grammar_result.installed_packages:
                installed_langs = [
                    pkg.replace("tree-sitter-", "").replace("tree_sitter_", "")
                    for pkg in grammar_result.installed_packages
                ]
                phase.complete(f"Installed: {', '.join(installed_langs)}")
            else:
                phase.complete("Grammars ready")
    from coderecon._core.gpu import probe_gpu
    gpu_result = probe_gpu()
    if gpu_result.has_onnx_gpu:
        provider = gpu_result.onnx_gpu_providers[0].replace("ExecutionProvider", "")
        status(f"GPU acceleration: {provider}", style="success")
    elif gpu_result.gpu_available_but_not_configured:
        hint = gpu_result.install_hint
        status(f"GPU detected ({gpu_result.provider_name}) but ONNX GPU provider not installed", style="info")
        if hint:
            status(f"  Enable GPU: {hint}", style="info")


def _run_indexing_phase(
    repo_root: Path,
    index_dir: Path,
    config_path: Path,
    console: object,
    show_recon_up_hint: bool,
) -> bool:
    """Run indexing, resolution, SPLADE encoding, and coverage collection."""
    from coderecon.index.ops import IndexCoordinatorEngine
    db_path = index_dir / "index.db"
    tantivy_path = index_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)
    coord = IndexCoordinatorEngine(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )
    indexing_state: dict[str, object] = {
        "indexing_done": False,
        "files_indexed": 0,
        "files_by_ext": {},
    }
    resolution_phase: PhaseBox | None = None
    refs_task_id: TaskID | None = None
    types_task_id: TaskID | None = None
    splade_phase: PhaseBox | None = None
    splade_task_id: TaskID | None = None
    indexing_elapsed = 0.0
    try:
        import time
        start_time = time.time()
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
                pct = int(indexed / total * 100) if total > 0 else 0
                indexing_phase._progress.update(indexing_task_id, completed=pct)  # type: ignore[union-attr]
                indexing_phase._update()
                if files_by_ext:
                    table = _make_init_extension_table(files_by_ext)
                    indexing_phase.set_live_table(table)
                indexing_state["files_indexed"] = indexed
                indexing_state["files_by_ext"] = files_by_ext
            elif progress_phase in ("resolving_cross_file", "resolving_refs", "resolving_types"):
                if not indexing_state["indexing_done"]:
                    indexing_state["indexing_done"] = True
                    indexing_elapsed = time.time() - start_time
                    indexing_phase.set_live_table(None)
                    files = indexing_state["files_indexed"]
                    indexing_phase.complete(f"{files} files ({indexing_elapsed:.1f}s)")
                    if indexing_state["files_by_ext"]:
                        indexing_phase.add_text("")
                        ext_table = _make_init_extension_table(indexing_state["files_by_ext"])  # type: ignore[arg-type]
                        indexing_phase.add_table(ext_table)
                    indexing_phase.__exit__(None, None, None)
                if resolution_phase is None:
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
        if not indexing_state["indexing_done"]:
            indexing_elapsed = time.time() - start_time
            indexing_phase.set_live_table(None)
            indexing_phase.complete(f"{result.files_indexed} files ({indexing_elapsed:.1f}s)")
            if result.files_by_ext:
                indexing_phase.add_text("")
                ext_table = _make_init_extension_table(result.files_by_ext)
                indexing_phase.add_table(ext_table)
            indexing_phase.__exit__(None, None, None)
        if resolution_phase is not None:
            total_elapsed = time.time() - start_time
            resolution_elapsed = total_elapsed - indexing_elapsed
            resolution_phase.complete(f"Done ({resolution_elapsed:.1f}s)")
            resolution_phase.__exit__(None, None, None)
        if splade_phase is not None:
            splade_phase.complete("Done")
            splade_phase.__exit__(None, None, None)
        if result.errors:
            for err in result.errors:
                status(f"Error: {err}", style="error")
            return False
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
    console.print()  # type: ignore[attr-defined]
    rel_config_path = config_path.relative_to(repo_root)
    status(f"Config created at {rel_config_path}", style="success")
    if show_recon_up_hint:
        console.print()  # type: ignore[attr-defined]
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
