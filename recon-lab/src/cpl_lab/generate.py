"""Generate ground truth — wraps infra/gt_orchestrator.py.

The orchestrator is a complex 1100-line Copilot SDK integration.
Rather than rewriting it, this module provides the CLI adapter that
sets up the environment and delegates to the orchestrator's existing
command interface.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click


def run_generate(
    config: dict[str, Any],
    repo_set: str | None = None,
    repo: str | None = None,
    stage: str | None = None,
    concurrency: int = 5,
    resume: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Run the GT orchestrator pipeline.

    Sets CPL_LAB_WORKSPACE so the orchestrator resolves paths correctly,
    then delegates to its main() function.
    """
    # Ensure the orchestrator can find the workspace
    os.environ["CPL_LAB_WORKSPACE"] = str(config["workspace"])

    # Add infra/ to sys.path so gt_orchestrator can be imported
    infra_dir = config["infra_dir"]
    if str(infra_dir) not in sys.path:
        sys.path.insert(0, str(infra_dir))

    # Build synthetic argv for the orchestrator's argparse
    argv: list[str] = ["gt_orchestrator.py"]

    if dry_run:
        click.echo("[dry-run] would run GT pipeline with:")
        click.echo(f"  workspace: {config['workspace']}")
        click.echo(f"  stage: {stage or 'all'}")
        click.echo(f"  repo: {repo or 'all'}")
        click.echo(f"  concurrency: {concurrency}")
        return

    if resume or repo or stage:
        argv.append("run")
        if stage:
            argv.extend(["--stage", stage])
        if repo:
            argv.extend(["--repo", repo])
        argv.extend(["--concurrency", str(concurrency)])
    else:
        argv.append("run")
        argv.extend(["--concurrency", str(concurrency)])

    # Import and invoke the orchestrator
    old_argv = sys.argv
    try:
        sys.argv = argv
        from gt_orchestrator import main  # type: ignore[import-not-found]
        main()
    finally:
        sys.argv = old_argv
