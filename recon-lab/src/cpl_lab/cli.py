"""cpl-lab — unified CLI for the Recon Lab pipeline.

Usage:
    cpl-lab clone [--set SET]
    cpl-lab index [--set SET] [--timeout SECS]
    cpl-lab swebench [--set SET] [--repo ID] [--max-instances N]
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
MODELS = ("ranker", "cutoff", "gate", "all")
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
    """Run `cpl init` on each clone."""
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
    from cpl_lab.merge import run_merge

    cfg = ctx.obj["config"]
    run_merge(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        what=what,
        verbose=ctx.obj["verbose"],
    )


# ── train ────────────────────────────────────────────────────────


@main.command()
@click.option("--model", type=click.Choice(MODELS), default="all",
              help="Which model to train.")
@click.option("--output-dir", type=click.Path(), default=None,
              help="Override model output directory.")
@click.option("--skip-merge", is_flag=True, help="Skip signal merge step.")
@click.pass_context
def train(ctx: click.Context, model: str, output_dir: str | None, skip_merge: bool) -> None:
    """Train models (gate → ranker → cutoff)."""
    from cpl_lab.train import run_train

    cfg = ctx.obj["config"]
    from pathlib import Path as P
    out = P(output_dir) if output_dir else cfg["models_dir"]
    run_train(
        data_dir=cfg["data_dir"],
        output_dir=out,
        model=model,
        skip_merge=skip_merge,
        verbose=ctx.obj["verbose"],
    )


# ── eval ─────────────────────────────────────────────────────────


@main.command("eval")
@click.option("--experiment", default=None, help="Experiment YAML file or name.")
@click.pass_context
def eval_cmd(ctx: click.Context, experiment: str | None) -> None:
    """Run EVEE evaluation of the ranking pipeline."""
    from cpl_lab.evaluate import run_eval

    cfg = ctx.obj["config"]
    run_eval(
        config=cfg,
        experiment=experiment,
        verbose=ctx.obj["verbose"],
    )


# ── validate ─────────────────────────────────────────────────────


@main.command()
@click.option("--repo", default=None, help="Single repo ID to validate.")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to validate.")
@click.pass_context
def validate(ctx: click.Context, repo: str | None, repo_set: str) -> None:
    """Validate ground truth JSON against schema."""
    from cpl_lab.validate import run_validate

    cfg = ctx.obj["config"]
    run_validate(
        data_dir=cfg["data_dir"],
        repo=repo,
        repo_set=repo_set,
        verbose=ctx.obj["verbose"],
    )


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
