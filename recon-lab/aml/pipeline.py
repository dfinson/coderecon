"""Submit the Recon Lab pipeline to Azure ML.

Defines the full pipeline DAG matching the DVC stages::

    clone → index-main ──────────────────────────────────────┐
         → pr-select → pr-checkout → index-worktrees ───────┤
                                   → pr-import → non-ok ────┤
                                                             ↓
                                                     collect → merge → train → eval

Usage::

    # Full pipeline
    python -m aml.pipeline

    # Subset: train-only (assumes merged data already in workspace)
    python -m aml.pipeline --stage train

    # Dry-run: print pipeline graph without submitting
    python -m aml.pipeline --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from azure.ai.ml import Input, MLClient, Output, command, dsl, load_component
from azure.ai.ml.constants import AssetTypes, InputOutputModes
from azure.identity import DefaultAzureCredential

# ── Defaults ─────────────────────────────────────────────────────

_SUBSCRIPTION_ID = os.environ.get("AML_SUBSCRIPTION_ID", "")
_RESOURCE_GROUP = "rg-coderecon-lab"
_WORKSPACE_NAME = "mlw-coderecon-lab"
_DATASTORE_NAME = "pipeline_workspace"  # blob container mounted as workspace
_WORKSPACE_PATH = "recon-lab"           # path within the datastore

# Compute targets (override via CLI)
_COMPUTE_DEFAULT = "cpu-cluster"
_COMPUTE_HEAVY = "cpu-cluster-heavy"    # for index / collect (more cores)

# ── Environment ──────────────────────────────────────────────────

_AML_DIR = Path(__file__).resolve().parent
_COMPONENTS_DIR = _AML_DIR / "components"
_ENV_DIR = _AML_DIR / "environments"
_SRC_DIR = _AML_DIR.parent / "src"


def _make_env() -> dict:
    """Inline environment spec shared by all components."""
    return {
        "image": "mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu22.04",
        "conda_file": str(_ENV_DIR / "conda-pipeline.yml"),
    }


# ── Component factories ─────────────────────────────────────────
# Each wraps the corresponding `recon-lab` CLI command, operating on
# the shared workspace mount.  Components are built with the SDK v2
# `command()` helper rather than YAML to keep everything in one file.


def _component(
    name: str,
    display: str,
    cmd: str,
    *,
    inputs: dict | None = None,
    outputs: dict | None = None,
) -> command:
    return command(
        name=name,
        display_name=display,
        environment=_make_env(),
        code=str(_SRC_DIR),
        command=cmd,
        inputs=inputs or {},
        outputs=outputs or {},
    )


def _clone_component():
    return _component(
        "clone",
        "Clone Repos",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab clone --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}} --jobs ${{inputs.jobs}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
            "jobs": Input(type="integer", default=4),
        },
    )


def _index_main_component():
    return _component(
        "index_main",
        "Index Main Clones",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab index-main --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
        },
    )


def _pr_select_component():
    return _component(
        "pr_select",
        "Select PRs",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab pr-select --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
        },
    )


def _pr_checkout_component():
    return _component(
        "pr_checkout",
        "Checkout PR Worktrees",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab pr-checkout --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
        },
    )


def _index_worktrees_component():
    return _component(
        "index_worktrees",
        "Index Worktrees",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab index-worktrees --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
        },
    )


def _pr_import_component():
    return _component(
        "pr_import",
        "PR Import (GT + Queries)",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab pr-import --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}} --workers ${{inputs.workers}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
            "workers": Input(type="integer", default=8),
        },
    )


def _non_ok_queries_component():
    return _component(
        "non_ok_queries",
        "Non-OK Queries",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab non-ok-queries --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
        },
    )


def _index_component():
    return _component(
        "index",
        "Index Repos",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab index --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}} --timeout ${{inputs.timeout}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
            "timeout": Input(type="integer", default=1800),
        },
    )


def _collect_component():
    return _component(
        "collect",
        "Collect Signals",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab collect --workspace ${{inputs.workspace}} "
            "--set ${{inputs.repo_set}} --workers ${{inputs.workers}}"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "repo_set": Input(type="string", default="all"),
            "workers": Input(type="integer", default=6),
        },
    )


def _merge_component():
    return _component(
        "merge",
        "Merge Data",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab merge --workspace ${{inputs.workspace}} --what all"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
        },
    )


def _train_component():
    return _component(
        "train",
        "Train Models",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "recon-lab train --workspace ${{inputs.workspace}} "
            "--output-dir ${{outputs.models}} --skip-merge"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
        },
        outputs={
            "models": Output(type=AssetTypes.URI_FOLDER),
        },
    )


def _eval_component():
    return _component(
        "eval",
        "Evaluate Models",
        (
            "export PYTHONPATH=.:$PYTHONPATH && "
            "cp ${{inputs.models}}/*.lgbm ${{inputs.workspace}}/models/ && "
            "recon-lab eval --workspace ${{inputs.workspace}} && "
            "cp -r ${{inputs.workspace}}/eval/* ${{outputs.metrics}}/"
        ),
        inputs={
            "workspace": Input(type=AssetTypes.URI_FOLDER),
            "models": Input(type=AssetTypes.URI_FOLDER),
        },
        outputs={
            "metrics": Output(type=AssetTypes.URI_FOLDER),
        },
    )


# ── Pipeline DAG ─────────────────────────────────────────────────


@dsl.pipeline(
    name="recon-lab-pipeline",
    description=(
        "Full Recon Lab training pipeline: "
        "clone → index-main + pr-select → pr-checkout → index-worktrees → "
        "pr-import → non-ok-queries → collect → merge → train → eval"
    ),
)
def recon_lab_pipeline(
    workspace: Input,
    repo_set: str = "all",
) -> dict:
    """Build the pipeline DAG.

    The workspace is a mounted blob datastore that all components read from
    and write to, matching the local DVC execution model.
    """
    # Phase 0: Clone repos
    clone = _clone_component()(workspace=workspace, repo_set=repo_set)

    # Phase 1: Index main clones + select PRs (parallel after clone)
    index_main = _index_main_component()(workspace=workspace, repo_set=repo_set)
    index_main.after(clone)

    pr_select = _pr_select_component()(workspace=workspace, repo_set=repo_set)
    pr_select.after(clone)

    # Phase 2: Checkout PR worktrees (needs pr_select)
    pr_checkout = _pr_checkout_component()(workspace=workspace, repo_set=repo_set)
    pr_checkout.after(pr_select)

    # Phase 3: Index worktrees (needs pr_checkout)
    index_worktrees = _index_worktrees_component()(workspace=workspace, repo_set=repo_set)
    index_worktrees.after(pr_checkout)

    # Phase 4: PR import — GT + query generation (needs index_worktrees)
    pr_import = _pr_import_component()(workspace=workspace, repo_set=repo_set)
    pr_import.after(index_worktrees)

    # Phase 5: Non-OK query generation (needs pr_import)
    non_ok = _non_ok_queries_component()(workspace=workspace, repo_set=repo_set)
    non_ok.after(pr_import)

    # Phase 6: Collect signals (needs index_main + non_ok)
    collect = _collect_component()(workspace=workspace, repo_set=repo_set)
    collect.after(index_main)
    collect.after(non_ok)

    # Phase 7: Merge
    merge = _merge_component()(workspace=workspace)
    merge.after(collect)

    # Phase 8: Train
    train = _train_component()(workspace=workspace)
    train.after(merge)

    # Phase 9: Eval
    evaluate = _eval_component()(workspace=workspace, models=train.outputs.models)
    evaluate.after(train)

    return {
        "models": train.outputs.models,
        "metrics": evaluate.outputs.metrics,
    }


# ── Partial pipelines for running subsets ─────────────────────────


@dsl.pipeline(name="recon-lab-train-only", description="Train + eval on existing merged data.")
def train_only_pipeline(workspace: Input) -> dict:
    """Submit just train + eval (for re-training on existing data)."""
    train = _train_component()(workspace=workspace)
    evaluate = _eval_component()(workspace=workspace, models=train.outputs.models)
    return {
        "models": train.outputs.models,
        "metrics": evaluate.outputs.metrics,
    }


@dsl.pipeline(name="recon-lab-collect-to-train", description="Collect → merge → train → eval.")
def collect_to_train_pipeline(workspace: Input, repo_set: str = "all") -> dict:
    """Re-run from collect onwards (new signals, same GT)."""
    collect = _collect_component()(workspace=workspace, repo_set=repo_set)
    merge = _merge_component()(workspace=workspace)
    merge.after(collect)
    train = _train_component()(workspace=workspace)
    train.after(merge)
    evaluate = _eval_component()(workspace=workspace, models=train.outputs.models)
    return {
        "models": train.outputs.models,
        "metrics": evaluate.outputs.metrics,
    }


# ── Submission ───────────────────────────────────────────────────


def _get_client(
    subscription_id: str,
    resource_group: str,
    workspace_name: str,
) -> MLClient:
    credential = DefaultAzureCredential()
    return MLClient(
        credential=credential,
        subscription_id=subscription_id,
        resource_group_name=resource_group,
        workspace_name=workspace_name,
    )


def _workspace_input(datastore: str, path: str) -> Input:
    return Input(
        type=AssetTypes.URI_FOLDER,
        path=f"azureml://datastores/{datastore}/paths/{path}",
        mode=InputOutputModes.RW_MOUNT,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Submit Recon Lab pipeline to AML.")
    parser.add_argument(
        "--stage",
        choices=["full", "train", "collect-to-train"],
        default="full",
        help="Pipeline variant to submit.",
    )
    parser.add_argument("--repo-set", default="all", help="Repo set filter.")
    parser.add_argument("--compute", default=_COMPUTE_DEFAULT, help="Default compute target.")
    parser.add_argument("--compute-heavy", default=_COMPUTE_HEAVY, help="Compute for CPU-heavy stages.")
    parser.add_argument("--subscription", default=_SUBSCRIPTION_ID)
    parser.add_argument("--resource-group", default=_RESOURCE_GROUP)
    parser.add_argument("--workspace", default=_WORKSPACE_NAME)
    parser.add_argument("--datastore", default=_DATASTORE_NAME)
    parser.add_argument("--datastore-path", default=_WORKSPACE_PATH)
    parser.add_argument("--experiment", default="coderecon-ranking", help="AML experiment name.")
    parser.add_argument("--dry-run", action="store_true", help="Print config without submitting.")
    args = parser.parse_args(argv)

    ws_input = _workspace_input(args.datastore, args.datastore_path)

    # Build the requested pipeline
    if args.stage == "full":
        pipeline_job = recon_lab_pipeline(workspace=ws_input, repo_set=args.repo_set)
    elif args.stage == "train":
        pipeline_job = train_only_pipeline(workspace=ws_input)
    elif args.stage == "collect-to-train":
        pipeline_job = collect_to_train_pipeline(workspace=ws_input, repo_set=args.repo_set)
    else:
        print(f"Unknown stage: {args.stage}", file=sys.stderr)
        raise SystemExit(1)

    # Set compute targets
    pipeline_job.settings.default_compute = args.compute
    # Override heavy stages with beefier compute
    for step_name in ("index_main", "index_worktrees", "collect"):
        step = getattr(pipeline_job.jobs, step_name, None)
        if step is not None:
            step.compute = args.compute_heavy

    pipeline_job.experiment_name = args.experiment

    if args.dry_run:
        print("Pipeline configuration:")
        print(f"  Stage:        {args.stage}")
        print(f"  Repo set:     {args.repo_set}")
        print(f"  Compute:      {args.compute}")
        print(f"  Heavy compute:{args.compute_heavy}")
        print(f"  Workspace:    {args.workspace}")
        print(f"  Datastore:    {args.datastore}:{args.datastore_path}")
        print(f"  Experiment:   {args.experiment}")
        print("\nPipeline steps:")
        for name, job in pipeline_job.jobs.items():
            compute = getattr(job, "compute", args.compute) or args.compute
            print(f"  {name:25s} → {compute}")
        print("\n[dry-run] Pipeline not submitted.")
        return

    if not args.subscription:
        print(
            "Subscription ID is required. Set AML_SUBSCRIPTION_ID env var "
            "or pass --subscription.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    client = _get_client(args.subscription, args.resource_group, args.workspace)
    submitted = client.jobs.create_or_update(pipeline_job)
    print(f"Pipeline submitted: {submitted.name}")
    print(f"Studio URL: {submitted.studio_url}")


if __name__ == "__main__":
    main()
