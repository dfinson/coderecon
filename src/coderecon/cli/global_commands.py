"""Global daemon CLI commands.

Commands for managing the global multi-repo daemon:
  recon catalog      — list registered repos
  recon register     — register a repo in the catalog (and notify the daemon)
  recon unregister   — remove a repo from the catalog
  recon worktrees    — list worktrees for a repo
  recon global-status — show daemon status
"""

from __future__ import annotations

from pathlib import Path

import click


@click.command("catalog")
def catalog_command() -> None:
    """List all registered repositories."""
    from coderecon.catalog import CatalogDB, CatalogRegistry

    catalog = CatalogDB()
    registry = CatalogRegistry(catalog)
    repos = registry.list_repos()

    if not repos:
        click.echo("No repositories registered. Use 'recon register' to add one.")
        return

    for repo in repos:
        worktrees = registry.list_worktrees(repo.id)  # type: ignore[arg-type]
        wt_names = [f"{wt.name}{'*' if wt.is_main else ''}" for wt in worktrees]
        click.echo(f"  {repo.name}")
        click.echo(f"    git:        {repo.git_dir}")
        click.echo(f"    storage:    {repo.storage_dir}")
        click.echo(f"    worktrees:  {', '.join(wt_names)}")
        click.echo()


@click.command("register")
@click.argument("path", required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "-r", "--reindex", is_flag=True,
    help="Wipe and rebuild the index from scratch even if one already exists.",
)
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
def register_command(path: Path | None, reindex: bool, mcp_targets: tuple[str, ...]) -> None:
    """Register a repository in the global catalog.

    Runs the full index build if the repo has not been set up yet (or if
    --reindex is passed). If the daemon is running the repo is activated
    immediately without a restart.
    """
    from coderecon.catalog import CatalogDB, CatalogRegistry
    from coderecon.cli.init import initialize_repo
    from coderecon.cli.utils import find_repo_root
    from coderecon.daemon.global_lifecycle import is_global_server_running, read_global_server_info

    repo_root = find_repo_root(path)
    coderecon_dir = repo_root / ".recon"

    # Always run init pipeline: it's a no-op when already initialized unless
    # --reindex is set, in which case it wipes and rebuilds.
    if (reindex or not coderecon_dir.exists()) and not initialize_repo(  # noqa: SIM102
        repo_root, reindex=reindex, show_recon_up_hint=False, mcp_targets=list(mcp_targets)
    ):
        if reindex:
            raise click.ClickException("Failed to (re)initialize repository")
        # already initialized — that's fine, continue to registration

    catalog = CatalogDB()
    registry = CatalogRegistry(catalog)
    repo, wt = registry.register(repo_root)
    click.echo(f"Registered: {repo.name}  (worktree: {wt.name})")
    click.echo(f"  Storage: {repo.storage_dir}")

    # Notify the running daemon so the repo is activated immediately
    if is_global_server_running():
        info = read_global_server_info()
        if info:
            _, server_port = info
            try:
                import httpx

                resp = httpx.post(
                    f"http://127.0.0.1:{server_port}/catalog/register",
                    json={"path": str(repo_root)},
                    timeout=10.0,
                )
                data = resp.json()
                mcp = data.get("mcp_endpoint")
                if mcp:
                    click.echo(f"  MCP endpoint: http://127.0.0.1:{server_port}{mcp}")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"  Warning: daemon notification failed: {exc}", err=True)
    else:
        click.echo("  (daemon not running — start with 'recon up')")



@click.command("unregister")
@click.argument("path", required=False, type=click.Path(exists=True, path_type=Path))
def unregister_command(path: Path | None) -> None:
    """Remove a repository from the global catalog."""
    from coderecon.catalog import CatalogDB, CatalogRegistry
    from coderecon.cli.utils import find_repo_root
    from coderecon.daemon.global_lifecycle import is_global_server_running, read_global_server_info

    repo_root = path or find_repo_root()

    if is_global_server_running():
        # Let the daemon remove from catalog AND stop the live slot atomically.
        info = read_global_server_info()
        if info:
            _, server_port = info
            try:
                import httpx

                resp = httpx.post(
                    f"http://127.0.0.1:{server_port}/catalog/unregister",
                    json={"path": str(repo_root)},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    click.echo(f"Unregistered: {repo_root}")
                elif resp.status_code == 404:
                    click.echo(f"Not registered: {repo_root}")
                else:
                    click.echo(f"Daemon error: {resp.text}", err=True)
                return
            except Exception as exc:  # noqa: BLE001
                click.echo(f"Warning: daemon notification failed: {exc}", err=True)
                # Fall through to direct catalog write below

    # Daemon not running (or unreachable) — write catalog directly.
    # No live slot exists, so no cleanup needed.
    catalog = CatalogDB()
    registry = CatalogRegistry(catalog)
    if registry.unregister(repo_root):
        click.echo(f"Unregistered: {repo_root}")
    else:
        click.echo(f"Not registered: {repo_root}")


@click.command("register-worktree")
@click.argument("path", required=False, type=click.Path(exists=True, path_type=Path))
def register_worktree_command(path: Path | None) -> None:
    """Register a git worktree for a repo already in the catalog.

    PATH must be a linked worktree directory (created by ``git worktree add``).
    The parent repo must already be registered with ``recon register``.
    Worktrees share the parent repo's index — no separate indexing is needed.
    If the daemon is running, the worktree is activated immediately.
    """
    from coderecon.catalog import CatalogDB, CatalogRegistry
    from coderecon.cli.utils import find_repo_root
    from coderecon.daemon.global_lifecycle import is_global_server_running, read_global_server_info

    wt_root = find_repo_root(path)
    catalog = CatalogDB()
    registry = CatalogRegistry(catalog)

    # register() handles worktree detection via .git file indirection
    repo, wt = registry.register(wt_root)

    if wt.is_main:
        raise click.ClickException(
            f"{wt_root} is the main worktree. Use 'recon register' instead."
        )

    click.echo(f"Registered worktree: {wt.name}  (repo: {repo.name})")
    click.echo(f"  Path: {wt.root_path}")

    # Notify the running daemon to activate this worktree
    if is_global_server_running():
        info = read_global_server_info()
        if info:
            _, server_port = info
            try:
                import httpx

                # First ensure the repo slot exists
                resp = httpx.post(
                    f"http://127.0.0.1:{server_port}/repos/{repo.name}/refresh-worktrees",
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    added = data.get("added_worktrees", [])
                    if wt.name in added:
                        mcp = f"/repos/{repo.name}/worktrees/{wt.name}/mcp"
                        click.echo(f"  MCP endpoint: http://127.0.0.1:{server_port}{mcp}")
                    else:
                        click.echo("  Worktree already active in daemon.")
                elif resp.status_code == 404:
                    click.echo(
                        f"  Warning: repo '{repo.name}' not active in daemon. "
                        "Restart with 'recon down && recon up'.",
                        err=True,
                    )
            except Exception as exc:  # noqa: BLE001
                click.echo(f"  Warning: daemon notification failed: {exc}", err=True)
    else:
        click.echo("  (daemon not running — start with 'recon up')")


@click.command("worktrees")
@click.argument("name", required=False)
def worktrees_command(name: str | None) -> None:
    """List worktrees for a repository."""
    from coderecon.catalog import CatalogDB, CatalogRegistry
    from coderecon.cli.utils import find_repo_root

    catalog = CatalogDB()
    registry = CatalogRegistry(catalog)

    if name:
        result = registry.lookup_by_name(name)
    else:
        repo_root = find_repo_root()
        result = registry.lookup_by_path(repo_root)

    if result is None:
        click.echo("Repository not found in catalog.")
        return

    repo, _ = result
    worktrees = registry.list_worktrees(repo.id)  # type: ignore[arg-type]

    click.echo(f"Worktrees for {repo.name}:")
    for wt in worktrees:
        main_marker = " (main)" if wt.is_main else ""
        branch = f" [{wt.branch}]" if wt.branch else ""
        click.echo(f"  {wt.name}{main_marker}{branch}")
        click.echo(f"    {wt.root_path}")


@click.command("global-status")
def global_status_command() -> None:
    """Show global daemon status."""
    from coderecon.daemon.global_lifecycle import is_global_server_running, read_global_server_info

    if not is_global_server_running():
        click.echo("Global daemon: not running")
        return

    info = read_global_server_info()
    if info:
        pid, port = info
        click.echo(f"Global daemon: running (PID {pid}, port {port})")
        click.echo(f"  Catalog: http://127.0.0.1:{port}/catalog")
        click.echo(f"  Health:  http://127.0.0.1:{port}/health")

        # Try to list active repos
        try:
            import httpx

            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3)
            data = resp.json()
            active = data.get("active_repos", [])
            if active:
                click.echo(f"  Active repos: {', '.join(active)}")
                for name in active:
                    click.echo(f"    {name}: http://127.0.0.1:{port}/repos/{name}/mcp")
        except Exception:  # noqa: BLE001
            pass
