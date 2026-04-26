"""recon clear command - remove CodeRecon data from a repository."""

from __future__ import annotations

import shutil
from pathlib import Path

import click
import questionary
from rich.console import Console

from coderecon.cli.init import _get_xdg_index_dir
from coderecon.cli.utils import find_repo_root


def clear_repo(repo_root: Path, *, yes: bool = False) -> bool:
    """Remove all CodeRecon data from a repository.

    This removes:
    - .recon/ directory (config, local index if stored there)
    - XDG index directory (for cross-filesystem setups like WSL)

    Returns True if cleared successfully, False if cancelled or nothing to clear.
    """
    console = Console(stderr=True)
    coderecon_dir = repo_root / ".recon"
    xdg_index_dir = _get_xdg_index_dir(repo_root)

    # Check what exists
    has_coderecon_dir = coderecon_dir.exists()
    has_xdg_index = xdg_index_dir.exists()

    if not has_coderecon_dir and not has_xdg_index:
        console.print("[yellow]Nothing to clear[/yellow] - no CodeRecon data found")
        return False

    # Show what will be deleted
    console.print("\n[bold]The following will be permanently deleted:[/bold]\n")

    if has_coderecon_dir:
        console.print(f"  [cyan]•[/cyan] {coderecon_dir}")

    if has_xdg_index:
        console.print(f"  [cyan]•[/cyan] {xdg_index_dir}")

    console.print()

    # Confirm unless --yes
    if not yes:
        answer = questionary.select(
            "This action cannot be undone. Are you sure?",
            choices=[
                questionary.Choice("No, keep my data", value=False),
                questionary.Choice("Yes, delete everything", value=True),
            ],
            style=questionary.Style(
                [
                    ("question", "bold"),
                    ("highlighted", "fg:red bold"),
                    ("selected", "fg:red"),
                ]
            ),
        ).ask()

        if not answer:
            console.print("[dim]Cancelled[/dim]")
            return False

    # Delete
    errors: list[str] = []

    if has_coderecon_dir:
        try:
            shutil.rmtree(coderecon_dir)
            console.print(f"  [green]✓[/green] Removed {coderecon_dir}")
        except OSError as e:
            errors.append(f"Failed to remove {coderecon_dir}: {e}")
            console.print(f"  [red]✗[/red] Failed to remove {coderecon_dir}: {e}")

    if has_xdg_index:
        try:
            shutil.rmtree(xdg_index_dir)
            console.print(f"  [green]✓[/green] Removed {xdg_index_dir}")
        except OSError as e:
            errors.append(f"Failed to remove {xdg_index_dir}: {e}")
            console.print(f"  [red]✗[/red] Failed to remove {xdg_index_dir}: {e}")

    if errors:
        return False

    console.print("\n[green]CodeRecon data cleared successfully[/green]")
    return True


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def clear_command(path: Path | None, yes: bool) -> None:
    """Remove all CodeRecon data from a repository.

    This removes the .recon/ directory and any associated index files
    (including cross-filesystem index storage for WSL setups).

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    repo_root = find_repo_root(path)

    if not clear_repo(repo_root, yes=yes):
        if not yes:
            return  # Cancelled or nothing to clear
        raise click.ClickException("Failed to clear CodeRecon data")
