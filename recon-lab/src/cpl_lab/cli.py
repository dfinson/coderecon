"""cpl-lab — unified CLI for the Recon Lab pipeline.

Usage:
    cpl-lab clone [--set SET]
    cpl-lab index-main [--set SET]
    cpl-lab pr-select [--set SET] [--repo ID]
    cpl-lab pr-checkout [--set SET]
    cpl-lab index-worktrees [--set SET]
    cpl-lab pr-import [--set SET] [--repo ID] [--max-instances N]
    cpl-lab non-ok-queries [--set SET] [--repo ID] [--force]
    cpl-lab collect [--set SET] [--repo ID]
    cpl-lab merge [--what {gt,signals,all}]
    cpl-lab train [--model {ranker,cutoff,gate,all}]
    cpl-lab eval [--experiment YAML]
    cpl-lab validate [--repo ID] [--set SET]
    cpl-lab status
    cpl-lab splade-bakeoff [--repo ID] [--model KEY] [--max-queries N]
    cpl-lab ce-bakeoff [--repo ID] [--model KEY] [--top-k N] [--test-body]
    cpl-lab ce-export [--output-dir DIR]
"""

from __future__ import annotations

from pathlib import Path

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
    """Clone repos to the workspace (full history, no indexing)."""
    from cpl_lab.pipeline.clone import run_clone

    cfg = ctx.obj["config"]
    jobs = jobs or cfg["clone"]["jobs"]
    run_clone(
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        jobs=jobs,
        dry_run=ctx.obj["dry_run"],
        verbose=ctx.obj["verbose"],
    )


# ── index-main ───────────────────────────────────────────────────


@main.command("index-main")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to index.")
@click.pass_context
def index_main(ctx: click.Context, repo_set: str) -> None:
    """Start daemon, register + index all main repos."""
    from cpl_lab.pipeline.pr_index import run_index_main

    import json, time
    cfg = ctx.obj["config"]
    run_index_main(
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        verbose=ctx.obj["verbose"],
    )
    # Write stamp file
    stamp = cfg["data_dir"] / "index_main.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}))


# ── pr-select ────────────────────────────────────────────────────


@main.command("pr-select")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to select PRs for.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.option("--prs-per-repo", type=int, default=None,
              help="Max PRs to select per repo.")
@click.pass_context
def pr_select(ctx: click.Context, repo_set: str, repo: str | None,
              prs_per_repo: int | None) -> None:
    """Select merged PRs from GitHub for each repo."""
    from cpl_lab.pipeline.pr_select import run_pr_select

    cfg = ctx.obj["config"]
    pr_cfg = cfg.get("pr_select", {})
    run_pr_select(
        clones_dir=cfg["clones_dir"],
        data_dir=cfg["data_dir"],
        repo_set=repo_set,
        repo=repo,
        prs_per_repo=prs_per_repo or pr_cfg.get("prs_per_repo", 30),
        max_files=pr_cfg.get("max_files_changed", 50),
        verbose=ctx.obj["verbose"],
    )


# ── pr-checkout ──────────────────────────────────────────────────


@main.command("pr-checkout")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.pass_context
def pr_checkout(ctx: click.Context, repo_set: str, repo: str | None) -> None:
    """Create git worktrees for all selected PR instances."""
    import json, time
    from cpl_lab.pipeline.pr_checkout import run_pr_checkout

    cfg = ctx.obj["config"]
    run_pr_checkout(
        clones_dir=cfg["clones_dir"],
        data_dir=cfg["data_dir"],
        repo_set=repo_set,
        repo=repo,
        verbose=ctx.obj["verbose"],
    )
    stamp = cfg["data_dir"] / "pr_checkout.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}))


# ── index-worktrees ──────────────────────────────────────────────


@main.command("index-worktrees")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.pass_context
def index_worktrees(ctx: click.Context, repo_set: str, repo: str | None) -> None:
    """Run recon init on each PR worktree and register with daemon."""
    import json, time
    from cpl_lab.pipeline.pr_index import run_index_worktrees

    cfg = ctx.obj["config"]
    run_index_worktrees(
        clones_dir=cfg["clones_dir"],
        data_dir=cfg["data_dir"],
        repo_set=repo_set,
        repo=repo,
        verbose=ctx.obj["verbose"],
    )
    stamp = cfg["data_dir"] / "index_worktrees.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}))


# ── pr-import ────────────────────────────────────────────────────


@main.command("pr-import")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to import.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.option("--max-instances", type=int, default=0,
              help="Limit imported instances (0=all).")
@click.option("--llm-model", default=None,
              help="LLM model for query generation.")
@click.option("--workers", type=int, default=8,
              help="Parallel workers for instance processing.")
@click.pass_context
def pr_import(ctx: click.Context, repo_set: str, repo: str | None,
              max_instances: int, llm_model: str | None, workers: int) -> None:
    """Import PR instances: diff → GT defs + LLM query generation."""
    from cpl_lab.pipeline.pr_import import run_pr_import

    cfg = ctx.obj["config"]
    pr_cfg = cfg.get("pr_select", {})
    run_pr_import(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        llm_model=llm_model or pr_cfg.get("llm_model", "openai/gpt-4-1-nano"),
        repo_set=repo_set,
        repo=repo,
        max_instances=max_instances,
        workers=workers,
        verbose=ctx.obj["verbose"],
    )


# ── non-ok-queries ───────────────────────────────────────────────


@main.command("non-ok-queries")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to generate non-OK queries for.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.option("--llm-model", default=None,
              help="LLM model (default: gpt-4.1-mini — needs reasoning capability).")
@click.option("--force", is_flag=True, help="Overwrite existing non_ok_queries.json.")
@click.option("--workers", default=1, help="Concurrent Copilot sessions (default 1).")
@click.pass_context
def non_ok_queries(ctx: click.Context, repo_set: str, repo: str | None,
                   llm_model: str | None, force: bool, workers: int) -> None:
    """Generate UNSAT/BROAD/AMBIG queries per repo (agentic, post pr-import)."""
    from cpl_lab.pipeline.non_ok_queries import run_non_ok_queries

    cfg = ctx.obj["config"]
    run_non_ok_queries(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        llm_model=llm_model or "openai/gpt-4.1-mini",
        repo_set=repo_set,
        repo=repo,
        force=force,
        verbose=ctx.obj["verbose"],
        workers=workers,
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
    from cpl_lab.collect.collect import run_collect

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
        from cpl_lab.collect.merge_ground_truth import merge_ground_truth

        summary = merge_ground_truth(cfg["data_dir"], clones_dir=cfg["clones_dir"],
                                     verbose=ctx.obj["verbose"])
        for table, count in summary["counts"].items():
            click.echo(f"  {table}: {count} rows")

    if what in ("signals", "all"):
        from cpl_lab.collect.merge_signals import merge_signals

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
    from cpl_lab.training.train_all import train_all

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
@click.option("--experiment", default=None, help="Experiment set: 'ranking' (default).")
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
    from cpl_lab.training.validate_ground_truth import validate_repo

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


# ── splade-bakeoff ───────────────────────────────────────────────


@main.command("splade-bakeoff")
@click.option("--repo", multiple=True, help="Specific repo IDs (repeatable). Default: auto-discover.")
@click.option("--model", multiple=True,
              help="Model keys to test (repeatable). Default: all three.")
@click.option("--max-queries", type=int, default=0,
              help="Max queries per repo (0=all).")
@click.pass_context
def splade_bakeoff(ctx: click.Context, repo: tuple[str, ...], model: tuple[str, ...],
                   max_queries: int) -> None:
    """Run SPLADE model bakeoff — compare sparse encoders on code scaffolds."""
    from cpl_lab.experiments.splade_bakeoff.run import run_bakeoff

    cfg = ctx.obj["config"]
    output_dir = cfg["workspace"] / "experiments" / "splade_bakeoff"
    run_bakeoff(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        output_dir=output_dir,
        repo_ids=list(repo) if repo else None,
        models=list(model) if model else None,
        max_queries_per_repo=max_queries,
        verbose=ctx.obj["verbose"],
    )


# ── ce-bakeoff ───────────────────────────────────────────────────


@main.command("ce-bakeoff")
@click.option("--repo", multiple=True,
              help="Specific logical repo IDs (repeatable). Default: auto-discover.")
@click.option("--model", multiple=True,
              help="Model keys to test (repeatable). Default: all three.")
@click.option("--top-k", multiple=True, type=int,
              help="Candidate pool sizes (repeatable). Default: 20,50,100.")
@click.option("--test-body", is_flag=True,
              help="Also test scaffold+body representation.")
@click.option("--max-queries", type=int, default=0,
              help="Max queries per repo (0=all).")
@click.pass_context
def ce_bakeoff(ctx: click.Context, repo: tuple[str, ...], model: tuple[str, ...],
               top_k: tuple[int, ...], test_body: bool, max_queries: int) -> None:
    """Run cross-encoder reranking bakeoff — compare rerankers on code scaffolds."""
    from cpl_lab.experiments.cross_encoder_rerank.run import run_ce_bakeoff

    cfg = ctx.obj["config"]
    output_dir = cfg["workspace"] / "experiments" / "cross_encoder_rerank"
    run_ce_bakeoff(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        output_dir=output_dir,
        repo_ids=list(repo) if repo else None,
        models=list(model) if model else None,
        top_k_values=list(top_k) if top_k else None,
        test_body=test_body,
        max_queries_per_repo=max_queries,
        verbose=ctx.obj["verbose"],
    )


@main.command("ce-export")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None,
              help="Output directory for ONNX model + tokenizer.")
@click.pass_context
def ce_export(ctx: click.Context, output_dir: Path | None) -> None:
    """Export MiniLM-L-6-v2 cross-encoder to ONNX for vendoring into coderecon."""
    from cpl_lab.experiments.cross_encoder_rerank.export_onnx import export

    export(output_dir)


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
