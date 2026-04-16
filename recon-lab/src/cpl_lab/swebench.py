"""SWE-bench ground-truth pipeline — combined entry point.

Delegates to:
  - ``swebench_import``  — import instances + index + LLM queries
  - ``swebench_resolve`` — map hunks to indexed defs, expand via coverage
  - ``swebench_common``  — shared dataclass, selection, git helpers

Both import and resolve require the coderecon index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from cpl_lab.swebench_common import (
    SwebenchInstance,
    select_instances,
    workspace_id,
)
from cpl_lab.swebench_import import import_instance, run_swebench_import
from cpl_lab.swebench_resolve import resolve_instance, run_swebench_resolve

# Re-export for CLI backward compatibility
__all__ = [
    "run_swebench",
    "run_swebench_import",
    "run_swebench_resolve",
    "SwebenchInstance",
]


def run_swebench(
    *,
    data_dir: Path,
    clones_dir: Path,
    repo_set: str,
    repo: str | None,
    max_instances: int,
    llm_model: str,
    training_dataset: str,
    training_split: str,
    eval_dataset: str,
    eval_split: str,
    cutoff_mod: int,
    cutoff_remainder: int,
    supplemental_datasets: list[str] | None = None,
    verbose: bool = False,
) -> None:
    """Import SWE-bench instances and emit GT artifacts (both phases)."""
    instances = list(
        select_instances(
            repo_set=repo_set,
            repo_filter=repo,
            training_dataset=training_dataset,
            training_split=training_split,
            eval_dataset=eval_dataset,
            eval_split=eval_split,
            cutoff_mod=cutoff_mod,
            cutoff_remainder=cutoff_remainder,
            supplemental_datasets=supplemental_datasets,
        )
    )
    if max_instances > 0:
        instances = instances[:max_instances]

    if not instances:
        click.echo("No SWE-bench instances matched the requested filters.")
        return

    ok = skipped = failed = 0
    for index, instance in enumerate(instances, start=1):
        click.echo(f"[{index}/{len(instances)}] {instance.instance_id} ({instance.repo_set})")
        try:
            summary = _process_instance(
                instance=instance,
                data_dir=data_dir,
                clones_dir=clones_dir,
                llm_model=llm_model,
                verbose=verbose,
            )
        except Exception as exc:
            failed += 1
            click.echo(f"  FAIL {exc}")
            continue

        if summary["status"] == "skip":
            skipped += 1
            click.echo(f"  SKIP {summary['reason']}")
        else:
            ok += 1
            click.echo(
                f"  OK {summary['workspace_id']} | defs={summary['minimum_defs']}"
            )

    click.echo(f"\nSWE-bench import complete: {ok} ok, {skipped} skipped, {failed} failed")


def _process_instance(
    *,
    instance: SwebenchInstance,
    data_dir: Path,
    clones_dir: Path,
    llm_model: str,
    verbose: bool,
) -> dict[str, Any]:
    """Combined path — runs import + resolve together."""
    wid = workspace_id(instance.instance_id)
    repo_dir = data_dir / wid
    gt_dir = repo_dir / "ground_truth"
    task_path = gt_dir / f"{wid}.json"
    if task_path.exists() and (gt_dir / "non_ok_queries.json").exists():
        return {"status": "skip", "reason": "already imported", "workspace_id": wid}

    import_result = import_instance(
        instance=instance,
        data_dir=data_dir,
        clones_dir=clones_dir,
        llm_model=llm_model,
        verbose=verbose,
    )
    if import_result["status"] != "ok":
        return import_result

    resolve_result = resolve_instance(
        repo_dir=repo_dir,
        clones_dir=clones_dir,
        verbose=verbose,
    )
    if resolve_result["status"] != "ok":
        return {**resolve_result, "workspace_id": wid}

    return {
        "workspace_id": wid,
        "status": "ok",
        "minimum_defs": resolve_result["minimum_defs"],
    }