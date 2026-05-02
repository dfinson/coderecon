"""recon-lab — unified CLI for the Recon Lab pipeline.

Subcommand groups:

  recon-lab data       Data acquisition and indexing
  recon-lab pipeline   Signal collection, merging, and training
  recon-lab eval       Evaluation and validation
  recon-lab experiment Bakeoff experiments and model export

  recon-lab status     (top-level) Show pipeline state
"""

from __future__ import annotations

from pathlib import Path

import click

from recon_lab.config import get_config


# ── Preflight checks ────────────────────────────────────────────

# Stages that need specific credentials/tools.
_NEEDS_RECON_BINARY = frozenset({"index-main", "index-worktrees", "collect"})
_NEEDS_AZURE_LLM = frozenset({"pr-import", "non-ok-queries"})


def _preflight(ctx: click.Context) -> None:
    """Validate credentials and tools BEFORE burning compute time."""
    cmd = ctx.info_name or ""
    issues: list[str] = []

    if cmd in _NEEDS_RECON_BINARY:
        try:
            from recon_lab.config import recon_binary

            recon_binary()
        except FileNotFoundError as e:
            issues.append(str(e))

    if cmd in _NEEDS_AZURE_LLM:
        import os
        import shutil

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            issues.append(
                "AZURE_OPENAI_ENDPOINT not set. "
                "Export it to point at your Azure OpenAI resource."
            )
        if not shutil.which("az"):
            issues.append(
                "Azure CLI not found. Install it and run `az login` "
                "for AAD token auth."
            )

    if issues:
        click.echo("Preflight check FAILED:", err=True)
        for issue in issues:
            click.echo(f"  ✗ {issue}", err=True)
        raise SystemExit(1)

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


# ── Subcommand groups ────────────────────────────────────────────


@main.group()
@click.pass_context
def data(ctx: click.Context) -> None:
    """Data acquisition and indexing."""


@main.group()
@click.pass_context
def pipeline(ctx: click.Context) -> None:
    """Signal collection, merging, and training."""


@main.group("eval")
@click.pass_context
def eval_group(ctx: click.Context) -> None:
    """Evaluation and validation."""


@main.group()
@click.pass_context
def experiment(ctx: click.Context) -> None:
    """Bakeoff experiments and model export."""


# ── data: clone ──────────────────────────────────────────────────


@data.command()
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to clone.")
@click.option("--jobs", type=int, default=None, help="Parallel clone jobs.")
@click.pass_context
def clone(ctx: click.Context, repo_set: str, jobs: int | None) -> None:
    """Clone repos to the workspace (full history, no indexing)."""
    from recon_lab.pipeline.clone import run_clone

    cfg = ctx.obj["config"]
    jobs = jobs or cfg["clone"]["jobs"]
    run_clone(
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        jobs=jobs,
        dry_run=ctx.obj["dry_run"],
        verbose=ctx.obj["verbose"],
    )


# ── data: index-main ─────────────────────────────────────────────


@data.command("index-main")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to index.")
@click.pass_context
def index_main(ctx: click.Context, repo_set: str) -> None:
    """Start daemon, register + index all main repos."""
    _preflight(ctx)
    from recon_lab.pipeline.pr_index import run_index_main

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


# ── data: pr-select ──────────────────────────────────────────────


@data.command("pr-select")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to select PRs for.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.option("--prs-per-repo", type=int, default=None,
              help="Max PRs to select per repo.")
@click.pass_context
def pr_select(ctx: click.Context, repo_set: str, repo: str | None,
              prs_per_repo: int | None) -> None:
    """Select merged PRs from GitHub for each repo."""
    _preflight(ctx)
    from recon_lab.pipeline.pr_select import run_pr_select

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


# ── data: pr-checkout ────────────────────────────────────────────


@data.command("pr-checkout")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.pass_context
def pr_checkout(ctx: click.Context, repo_set: str, repo: str | None) -> None:
    """Create git worktrees for all selected PR instances."""
    import json, time
    from recon_lab.pipeline.pr_checkout import run_pr_checkout

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


# ── data: index-worktrees ────────────────────────────────────────


@data.command("index-worktrees")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.pass_context
def index_worktrees(ctx: click.Context, repo_set: str, repo: str | None) -> None:
    """Run recon init on each PR worktree and register with daemon."""
    _preflight(ctx)
    import json, time
    from recon_lab.pipeline.pr_index import run_index_worktrees

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


# ── data: pr-import ──────────────────────────────────────────────


@data.command("pr-import")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to import.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.option("--max-instances", type=int, default=0,
              help="Limit imported instances (0=all).")
@click.option("--llm-model", default=None,
              help="LLM model for query generation.")
@click.option("--gt-label-model", default=None,
              help="LLM model for GT relevance labeling (default: gpt-4-1-mini).")
@click.option("--workers", type=int, default=8,
              help="Parallel workers for instance processing.")
@click.pass_context
def pr_import(ctx: click.Context, repo_set: str, repo: str | None,
              max_instances: int, llm_model: str | None,
              gt_label_model: str | None, workers: int) -> None:
    """Import PR instances: diff → GT defs + LLM query generation."""
    _preflight(ctx)
    from recon_lab.pipeline.pr_import import run_pr_import

    cfg = ctx.obj["config"]
    pr_cfg = cfg.get("pr_select", {})
    run_pr_import(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        llm_model=llm_model or pr_cfg.get("llm_model", "openai/gpt-4-1-nano"),
        gt_label_model=gt_label_model,
        repo_set=repo_set,
        repo=repo,
        max_instances=max_instances,
        workers=workers,
        verbose=ctx.obj["verbose"],
    )


# ── data: non-ok-queries ─────────────────────────────────────────


@data.command("non-ok-queries")
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
    from recon_lab.pipeline.non_ok_queries import run_non_ok_queries

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


# ── pipeline: collect ─────────────────────────────────────────────


@pipeline.command()
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to collect signals for.")
@click.option("--repo", default=None, help="Single repo ID.")
@click.option("--workers", default=0, help="Parallel workers (0=auto).")
@click.pass_context
def collect(ctx: click.Context, repo_set: str, repo: str | None, workers: int) -> None:
    """Collect retrieval signals (direct, no MCP server needed)."""
    _preflight(ctx)
    from recon_lab.collect.collect import run_collect

    cfg = ctx.obj["config"]
    run_collect(
        data_dir=cfg["data_dir"],
        clones_dir=cfg["clones_dir"],
        repo_set=repo_set,
        repo=repo,
        workers=workers,
        verbose=ctx.obj["verbose"],
    )


# ── pipeline: merge ──────────────────────────────────────────────


@pipeline.command()
@click.option("--what", type=click.Choice(MERGE_TARGETS), default="all",
              help="What to merge: gt, signals, or all.")
@click.pass_context
def merge(ctx: click.Context, what: str) -> None:
    """Merge per-repo data into unified parquet tables."""
    cfg = ctx.obj["config"]

    if what in ("gt", "all"):
        click.echo("=== Merging Ground Truth ===")
        from recon_lab.collect.merge_ground_truth import merge_ground_truth

        summary = merge_ground_truth(cfg["data_dir"], clones_dir=cfg["clones_dir"],
                                     verbose=ctx.obj["verbose"])
        for table, count in summary["counts"].items():
            click.echo(f"  {table}: {count} rows")

    if what in ("signals", "all"):
        from recon_lab.collect.merge_signals import merge_signals

        summary = merge_signals(cfg["data_dir"])
        pos = summary['total_candidates'] * summary['positive_rate']
        click.echo(f"  {summary['total_candidates']:,} candidates merged, "
                   f"{int(pos):,} positive ({summary['positive_rate']*100:.4f}%)")

    click.echo("\nMerge complete.")


# ── pipeline: train ──────────────────────────────────────────────


@pipeline.command()
@click.option("--output-dir", type=click.Path(), default=None,
              help="Override model output directory.")
@click.option("--skip-merge", is_flag=True, help="Skip signal merge step.")
@click.pass_context
def train(ctx: click.Context, output_dir: str | None, skip_merge: bool) -> None:
    """Train all 4 models (gate, file-ranker, def-ranker, cutoff)."""
    from recon_lab.training.train_all import train_all

    cfg = ctx.obj["config"]
    from pathlib import Path as P
    out = P(output_dir) if output_dir else cfg["models_dir"]
    train_all(
        data_dir=cfg["data_dir"],
        output_dir=out,
        skip_merge=skip_merge,
    )


# ── eval: run ────────────────────────────────────────────────────


@eval_group.command("run")
@click.option("--experiment", default=None, help="Experiment set: 'ranking' (default).")
@click.pass_context
def eval_cmd(ctx: click.Context, experiment: str | None) -> None:
    """Run Inspect AI evaluation of the ranking pipeline."""
    from recon_lab.eval.run import run

    label = experiment or "ranking"
    click.echo(f"Running Inspect AI evaluation: {label}")
    run(experiment)


# ── eval: micro ──────────────────────────────────────────────────


@eval_group.command("micro")
@click.pass_context
def micro_eval_cmd(ctx: click.Context) -> None:
    """Offline ranking sanity check on merged parquet — no daemon needed."""
    from recon_lab.eval.run import run

    click.echo("Running micro-eval (offline, from merged parquet)")
    run("micro")


@eval_group.command("compare")
@click.pass_context
def micro_compare_cmd(ctx: click.Context) -> None:
    """Compare LGBM ranker vs RRF-only vs CE-only baselines (offline)."""
    from recon_lab.eval.run import run

    click.echo("Running micro-compare: LGBM vs RRF vs CE-only")
    run("micro-compare")


@eval_group.command("gt-discovery")
@click.option("--model", "model_override", default=None,
              help="Override model (default: openai/azure/gpt-4o-mini).")
@click.pass_context
def gt_discovery_cmd(ctx: click.Context, model_override: str | None) -> None:
    """Run GT discovery experiment — agent-driven context exploration."""
    from recon_lab.eval.run import run

    click.echo("Running GT discovery experiment")
    run("gt-discovery", model_override=model_override)


# ── eval: validate ───────────────────────────────────────────────


@eval_group.command()
@click.option("--repo", default=None, help="Single repo ID to validate.")
@click.option("--set", "repo_set", type=click.Choice(SETS), default="all",
              help="Which repo set to validate.")
@click.pass_context
def validate(ctx: click.Context, repo: str | None, repo_set: str) -> None:
    """Validate ground truth JSON against schema."""
    from recon_lab.data_manifest import iter_repo_data_dirs, repo_set_for_dir
    from recon_lab.training.validate_ground_truth import validate_repo

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


# ── experiment: splade-bakeoff ──────────────────────────────────


@experiment.command("splade-bakeoff")
@click.option("--repo", multiple=True, help="Specific repo IDs (repeatable). Default: auto-discover.")
@click.option("--model", multiple=True,
              help="Model keys to test (repeatable). Default: all three.")
@click.option("--max-queries", type=int, default=0,
              help="Max queries per repo (0=all).")
@click.pass_context
def splade_bakeoff(ctx: click.Context, repo: tuple[str, ...], model: tuple[str, ...],
                   max_queries: int) -> None:
    """Run SPLADE model bakeoff — compare sparse encoders on code scaffolds."""
    from recon_lab.experiments.splade_bakeoff.run import run_bakeoff

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


# ── experiment: ce-bakeoff ───────────────────────────────────────


@experiment.command("ce-bakeoff")
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
    from recon_lab.experiments.cross_encoder_rerank.run import run_ce_bakeoff

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


@experiment.command("ce-export")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None,
              help="Output directory for ONNX model + tokenizer.")
@click.pass_context
def ce_export(ctx: click.Context, output_dir: Path | None) -> None:
    """Export MiniLM-L-6-v2 cross-encoder to ONNX for vendoring into coderecon."""
    from recon_lab.experiments.cross_encoder_rerank.export_onnx import export

    export(output_dir)


# ── status ───────────────────────────────────────────────────────


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show pipeline state across all stages."""
    from recon_lab.status import run_status

    cfg = ctx.obj["config"]
    run_status(config=cfg, verbose=ctx.obj["verbose"])


if __name__ == "__main__":
    main()
