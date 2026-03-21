"""Index cloned repos with `recon init` — ported from index_all.sh."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import click


def _coderecon_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _find_recon_python(repo_root: Path | None = None) -> str:
    """Find a Python interpreter that can run CodeRecon's source-tree CLI."""
    root = repo_root or _coderecon_repo_root()
    candidates = [
        root / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise click.ClickException("Could not find a Python interpreter for recon init")


def _recon_env(repo_root: Path | None = None) -> dict[str, str]:
    root = repo_root or _coderecon_repo_root()
    env = os.environ.copy()
    src_path = str(root / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
    return env


def _recon_init_cmd(repo_dir: Path, *, reindex: bool = False) -> tuple[list[str], dict[str, str]]:
    repo_root = _coderecon_repo_root()
    cmd = [_find_recon_python(repo_root), "-m", "coderecon.cli.main", "init"]
    if reindex:
        cmd.append("-r")
    cmd.append(str(repo_dir))
    return cmd, _recon_env(repo_root)


def _ensure_recon_models() -> None:
    from coderecon.cli.models import ensure_models

    if not ensure_models(interactive=False):
        raise click.ClickException("Embedding models are required for recon init")


def _iter_clones(clones_dir: Path, repo_set: str) -> list[Path]:
    """Yield clone directories for the given repo set."""
    if repo_set == "all":
        set_dirs = sorted(d for d in clones_dir.iterdir() if d.is_dir())
    else:
        target = clones_dir / repo_set
        if not target.is_dir():
            raise click.ClickException(f"Set directory not found: {target}")
        set_dirs = [target]

    repos = []
    for set_dir in set_dirs:
        for repo_dir in sorted(set_dir.iterdir()):
            if (repo_dir / ".git").is_dir():
                repos.append(repo_dir)
    return repos


def run_index(
    clones_dir: Path,
    repo_set: str = "all",
    timeout: int = 1800,
    reindex: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Run `recon init` on each clone."""
    repos = _iter_clones(clones_dir, repo_set)

    if repos and not dry_run:
        _ensure_recon_models()

    ok = skipped = failed = 0

    for repo_dir in repos:
        rel = f"{repo_dir.parent.name}/{repo_dir.name}"

        if not reindex and (repo_dir / ".recon").is_dir():
            if verbose:
                click.echo(f"SKIP {rel} (already indexed)")
            skipped += 1
            continue

        if dry_run:
            click.echo(f"[dry-run] would index {rel}")
            continue

        click.echo(f"INDEX {rel} ... ", nl=False)
        start = time.monotonic()

        cmd, env = _recon_init_cmd(repo_dir, reindex=reindex)

        try:
            subprocess.run(
                cmd, env=env, timeout=timeout, check=True,
                capture_output=True, text=True,
            )
            elapsed = time.monotonic() - start
            click.echo(f"OK ({elapsed:.0f}s)")
            ok += 1
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            click.echo(f"TIMEOUT ({elapsed:.0f}s)")
            failed += 1
        except subprocess.CalledProcessError as e:
            elapsed = time.monotonic() - start
            click.echo(f"FAILED exit={e.returncode} ({elapsed:.0f}s)")
            if e.stderr:
                click.echo(f"  {e.stderr[:200]}")
            failed += 1

    click.echo(f"\nTotal: {len(repos)} | OK: {ok} | Skipped: {skipped} | Failed: {failed}")
