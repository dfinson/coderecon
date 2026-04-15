"""cpl-lab — unified CLI for the Recon Lab pipeline.

Usage:
    cpl-lab clone [--set SET]
    cpl-lab index [--set SET] [--timeout SECS]
    cpl-lab swebench [--set SET] [--repo ID] [--max-instances N]
    cpl-lab swebench-import [--set SET] [--repo ID] [--max-instances N]
    cpl-lab swebench-resolve [--set SET] [--repo ID]
    cpl-lab collect [--set SET] [--repo ID]
    cpl-lab merge [--what {gt,signals,all}]
    cpl-lab train [--model {ranker,cutoff,gate,all}]
    cpl-lab eval [--experiment YAML]
    cpl-lab validate [--repo ID] [--set SET]
    cpl-lab status
"""

from __future__ import annotations

import click

from cpl_lab.config import get_config

SETS = ("ranker-gate", "cutoff", "eval", "all")
MERGE_TARGETS = ("gt", "signals", "all")


@click.group()
@click.option("--workspace", default=None,
              help="Override workspace directory (default: lab.toml).")
@click.option("-v", "--verbose", is_flag=True, help="Increase log verbosity.")
@click.option("--dry-run", is_flag=True, help="Show what would be done.")
@click.pass_context
def main(ctx: click.Context, workspace: str | None, verbose: bool, dry_run: bool) -> None:
    """Recon Lab — training pipeline for CodeRecon's recon models."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = get_config(workspace)
    ctx.obj["verbose"] = verbose
    ctx.obj["dry_run"] = dry_run


# ── clone ────────────────────────────────────────────────────────


@main.command()
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to clone.")
@click.option("--jobs", type=int, default=None, help="Parallel clone jobs.")
@click.pass_context
def clone(ctx: click.Context, repo_set: str, jobs: int | None) -> None:
    """Clone repos to the workspace."""
    from cpl_lab.clone import run_clone

    cfg = ctx.obj["config"]
    jobs = jobs or cfg["clone"]["jobs"]
    run_clone(
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        jobs=jobs,
        depth=cfg["clone"]["depth"],
        dry_run=ctx.obj["dry_run"],
        verbose=ctx.obj["verbose"],
    )


# ── index ────────────────────────────────────────────────────────


@main.command()
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to index.")
@click.option("--timeout", type=int, default=None, help="Timeout per repo (seconds).")
@click.option("--reindex", is_flag=True, help="Force re-index even if .recon/ exists.")
@click.pass_context
def index(ctx: click.Context, repo_set: str, timeout: int | None, reindex: bool) -> None:
    """Run `recon init` on each clone."""
    from cpl_lab.index import run_index

    cfg = ctx.obj["config"]
    timeout = timeout or cfg["index"]["timeout"]
    run_index(
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        timeout=timeout,
        reindex=reindex,
        dry_run=ctx.obj["dry_run"],
        verbose=ctx.obj["verbose"],
    )


# ── swebench ─────────────────────────────────────────────────────


@main.command()
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to import from SWE-bench.")
@click.option("--repo", default=None,
              help="Filter to a single repo slug or logical repo ID.")
@click.option("--max-instances", type=int, default=None,
              help="Limit imported instances after set/repo filtering.")
@click.option("--llm-model", default=None,
              help="LLM model for query/tier adaptation.")
@click.option("--filter-model", default=None,
              help="LLM model for context filtering.")
@click.pass_context
def swebench(ctx: click.Context, repo_set: str, repo: str | None,
             max_instances: int | None, llm_model: str | None,
             filter_model: str | None) -> None:
    """Import SWE-bench instances and adapt them into ground truth."""
    from cpl_lab.swebench import run_swebench

    cfg = ctx.obj["config"]
    swebench_cfg = cfg["swebench"]
    run_swebench(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        repo=repo,
        max_instances=max_instances if max_instances is not None else swebench_cfg["max_instances"],
        llm_model=llm_model or swebench_cfg["llm_model"],
        filter_model=filter_model or swebench_cfg["filter_model"],
        training_dataset=swebench_cfg["training_dataset"],
        training_split=swebench_cfg["training_split"],
        eval_dataset=swebench_cfg["eval_dataset"],
        eval_split=swebench_cfg["eval_split"],
        cutoff_mod=swebench_cfg["cutoff_mod"],
        cutoff_remainder=swebench_cfg["cutoff_remainder"],
        supplemental_datasets=swebench_cfg["supplemental_datasets"],
        verbose=ctx.obj["verbose"],
    )


# ── swebench-import (Phase 1: no coderecon) ──────────────────────


@main.command("swebench-import")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to import from SWE-bench.")
@click.option("--repo", default=None,
              help="Filter to a single repo slug or logical repo ID.")
@click.option("--max-instances", type=int, default=None,
              help="Limit imported instances after set/repo filtering.")
@click.option("--llm-model", default=None,
              help="LLM model for query generation.")
@click.option("--workers", type=int, default=10,
              help="Parallel import workers (default 10).")
@click.pass_context
def swebench_import(ctx: click.Context, repo_set: str, repo: str | None,
                   max_instances: int | None, llm_model: str | None,
                   workers: int) -> None:
    """Phase 1: Import SWE-bench instances + generate queries (no coderecon)."""
    from cpl_lab.swebench import run_swebench_import

    cfg = ctx.obj["config"]
    swebench_cfg = cfg["swebench"]
    run_swebench_import(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        repo=repo,
        max_instances=max_instances if max_instances is not None else swebench_cfg["max_instances"],
        llm_model=llm_model or swebench_cfg["llm_model"],
        training_dataset=swebench_cfg["training_dataset"],
        training_split=swebench_cfg["training_split"],
        eval_dataset=swebench_cfg["eval_dataset"],
        eval_split=swebench_cfg["eval_split"],
        cutoff_mod=swebench_cfg["cutoff_mod"],
        cutoff_remainder=swebench_cfg["cutoff_remainder"],
        supplemental_datasets=swebench_cfg["supplemental_datasets"],
        workers=workers,
        verbose=ctx.obj["verbose"],
    )


# ── swebench-resolve (Phase 2: needs coderecon) ─────────────────


@main.command("swebench-resolve")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to resolve.")
@click.option("--repo", default=None,
              help="Filter to a single repo slug or logical repo ID.")
@click.option("--filter-model", default=None,
              help="LLM model for context filtering.")
@click.pass_context
def swebench_resolve(ctx: click.Context, repo_set: str, repo: str | None,
                    filter_model: str | None) -> None:
    """Phase 2: Map defs + LLM filter (needs coderecon index)."""
    from cpl_lab.swebench import run_swebench_resolve

    cfg = ctx.obj["config"]
    swebench_cfg = cfg["swebench"]
    run_swebench_resolve(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        repo=repo,
        filter_model=filter_model or swebench_cfg["filter_model"],
        verbose=ctx.obj["verbose"],
    )


# ── collect ──────────────────────────────────────────────────────


@main.command()
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to collect signals for.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.option("--workers", default=0, help="Parallel workers (0=auto).")
@click.pass_context
def collect(ctx: click.Context, repo_set: str, repo: str | None, workers: int) -> None:
    """Collect retrieval signals (direct, no MCP server needed)."""
    from cpl_lab.collect import run_collect

    cfg = ctx.obj["config"]
    run_collect(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        repo=repo,
        workers=workers,
        verbose=ctx.obj["verbose"],
    )


# ── merge ────────────────────────────────────────────────────────


@main.command()
@click.option("--what", type=click.Choice(MERGE_TARGETS), default="all",
              help="What to merge: gt, signals, or all.")
@click.pass_context
def merge(ctx: click.Context, what: str) -> None:
    """Merge per-repo data into unified parquet tables."""
    cfg = ctx.obj["config"]

    if what in ("gt", "all"):
        click.echo("=== Merging Ground Truth ===")
        from cpl_lab.merge_ground_truth import merge_ground_truth

        summary = merge_ground_truth(cfg["data_dir"], clones_dir=cfg["clones_dir"],
                                     verbose=ctx.obj["verbose"])
        for table, count in summary["counts"].items():
            click.echo(f"  {table}: {count} rows")

    if what in ("signals", "all"):
        from cpl_lab.merge_signals import merge_signals

        summary = merge_signals(cfg["data_dir"])
        pos = summary['total_candidates'] * summary['positive_rate']
        click.echo(f"  {summary['total_candidates']:,} candidates merged, "
                   f"{int(pos):,} positive ({summary['positive_rate']*100:.4f}%)")

    click.echo("\nMerge complete.")


# ── train ────────────────────────────────────────────────────────


@main.command()
@click.option("--output-dir", type=click.Path(), default=None,
              help="Override model output directory.")
@click.option("--skip-merge", is_flag=True, help="Skip signal merge step.")
@click.pass_context
def train(ctx: click.Context, output_dir: str | None, skip_merge: bool) -> None:
    """Train all 8 models (gate, file-ranker, def-ranker, cutoff × structural/enhanced)."""
    from cpl_lab.train_all import train_all

    cfg = ctx.obj["config"]
    from pathlib import Path as P
    out = P(output_dir) if output_dir else cfg["models_dir"]
    train_all(
        data_dir=cfg["data_dir"],
        output_dir=out,
        skip_merge=skip_merge,
    )


# ── eval ─────────────────────────────────────────────────────────


@main.command("eval")
@click.option("--experiment", default=None, help="Experiment set: 'ranking' (default) or 'llm'.")
@click.pass_context
def eval_cmd(ctx: click.Context, experiment: str | None) -> None:
    """Run Inspect AI evaluation of the ranking pipeline."""
    from cpl_lab.eval.run import run

    label = experiment or "ranking"
    click.echo(f"Running Inspect AI evaluation: {label}")
    run(experiment)


# ── validate ─────────────────────────────────────────────────────


@main.command()
@click.option("--repo", default=None, help="Single repo ID to validate.")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to validate.")
@click.pass_context
def validate(ctx: click.Context, repo: str | None, repo_set: str) -> None:
    """Validate ground truth JSON against schema."""
    from cpl_lab.data_manifest import iter_repo_data_dirs, repo_set_for_dir
    from cpl_lab.validate_ground_truth import validate_repo

    cfg = ctx.obj["config"]
    data_dir = cfg["data_dir"]
    verbose = ctx.obj["verbose"]

    repo_dirs: list = []
    if repo:
        rd = data_dir / repo
        if not rd.is_dir():
            raise click.ClickException(f"Repo data not found: {rd}")
        repo_dirs = [rd]
    else:
        for repo_dir in iter_repo_data_dirs(data_dir):
            gt = repo_dir / "ground_truth"
            if gt.is_dir() and any(gt.glob("*.json")):
                if repo_set == "all" or repo_set_for_dir(repo_dir) == repo_set:
                    repo_dirs.append(repo_dir)

    if not repo_dirs:
        click.echo("No repos with ground truth found to validate.")
        return

    ok = failed = 0
    for rd in repo_dirs:
        errors = validate_repo(rd)
        if errors:
            click.echo(f"FAIL {rd.name} — {len(errors)} error(s)")
            if verbose:
                for e in errors[:10]:
                    click.echo(f"  {e}")
            failed += 1
        else:
            click.echo(f"  OK {rd.name}")
            ok += 1

    click.echo(f"\nValidated: {ok} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


# ── status ───────────────────────────────────────────────────────


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show pipeline state across all stages."""
    from cpl_lab.status import run_status

    cfg = ctx.obj["config"]
    run_status(config=cfg, verbose=ctx.obj["verbose"])


if __name__ == "__main__":
    main()
