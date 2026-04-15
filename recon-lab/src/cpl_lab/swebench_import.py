"""SWE-bench Phase 1 — import raw instances + generate LLM queries.

Does NOT require coderecon.  Clones the repo (bare mirror + worktree),
parses the patch into file diffs, and runs LLM adaptation to generate
queries.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import click

from cpl_lab.data_manifest import write_repo_manifest
from cpl_lab.patch_ground_truth import parse_unified_diff
from cpl_lab.swebench_common import (
    SwebenchInstance,
    combined_patch,
    ensure_repo_checkout,
    logical_repo_id_from_slug,
    select_instances,
    workspace_id,
)
from cpl_lab.swebench_llm import adapt_instance

logger = logging.getLogger(__name__)


def run_swebench_import(
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
    workers: int = 1,
    verbose: bool = False,
) -> None:
    """Phase 1: Import SWE-bench instances — raw GT + LLM-generated queries."""
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

    workers = max(1, min(workers, len(instances)))

    if workers == 1:
        _import_serial(instances, data_dir, clones_dir, llm_model, verbose)
    else:
        _import_parallel(instances, data_dir, clones_dir, llm_model, verbose, workers)


def _import_serial(
    instances: list[SwebenchInstance],
    data_dir: Path,
    clones_dir: Path,
    llm_model: str,
    verbose: bool,
) -> None:
    ok = skipped = failed = 0
    for index, instance in enumerate(instances, start=1):
        click.echo(f"[{index}/{len(instances)}] {instance.instance_id} ({instance.repo_set})")
        try:
            summary = import_instance(
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
            n_queries = summary.get("queries", 0)
            click.echo(f"  OK {summary['workspace_id']} | queries={n_queries}")

    click.echo(f"\nSWE-bench import (phase 1): {ok} ok, {skipped} skipped, {failed} failed")


def _import_parallel(
    instances: list[SwebenchInstance],
    data_dir: Path,
    clones_dir: Path,
    llm_model: str,
    verbose: bool,
    workers: int,
) -> None:
    total = len(instances)
    ok = skipped = failed = 0
    done = 0

    def _do(instance: SwebenchInstance) -> tuple[SwebenchInstance, dict[str, Any] | None, Exception | None]:
        try:
            summary = import_instance(
                instance=instance,
                data_dir=data_dir,
                clones_dir=clones_dir,
                llm_model=llm_model,
                verbose=verbose,
            )
            return (instance, summary, None)
        except Exception as exc:
            return (instance, None, exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_do, inst): inst for inst in instances}
        for future in as_completed(futures):
            done += 1
            inst, summary, exc = future.result()
            prefix = f"[{done}/{total}]"
            if exc is not None:
                failed += 1
                click.echo(f"{prefix} {inst.instance_id} FAIL {exc}")
            elif summary["status"] == "skip":
                skipped += 1
                click.echo(f"{prefix} {inst.instance_id} SKIP {summary['reason']}")
            else:
                ok += 1
                n_queries = summary.get("queries", 0)
                click.echo(f"{prefix} {inst.instance_id} OK queries={n_queries}")

    click.echo(f"\nSWE-bench import (phase 1): {ok} ok, {skipped} skipped, {failed} failed")


def import_instance(
    *,
    instance: SwebenchInstance,
    data_dir: Path,
    clones_dir: Path,
    llm_model: str,
    verbose: bool,
) -> dict[str, Any]:
    """Import a single SWE-bench instance (Phase 1).

    Writes:
      - ``ground_truth/raw_instance.json`` — instance metadata + parsed hunks
      - ``ground_truth/queries.json``       — 8 OK queries from LLM
      - ``ground_truth/non_ok_queries.json`` — 6+ non-OK queries from LLM
      - ``manifest.json``                   — instance provenance
    """
    t0 = time.monotonic()
    wid = workspace_id(instance.instance_id)
    repo_dir = data_dir / wid
    gt_dir = repo_dir / "ground_truth"
    raw_path = gt_dir / "raw_instance.json"

    if raw_path.exists() and (gt_dir / "queries.json").exists():
        return {"status": "skip", "reason": "already imported (phase 1)", "workspace_id": wid}

    clone_dir = ensure_repo_checkout(instance, clones_dir)

    diff_text = combined_patch(instance)
    file_diffs = parse_unified_diff(diff_text)
    if not file_diffs:
        return {"status": "skip", "reason": "empty patch", "workspace_id": wid}

    patch_summary = [
        {
            "path": fd.path,
            "is_new_file": fd.is_new_file,
            "is_deleted": fd.is_deleted,
            "hunks": [{"start_line": h.start_line, "line_count": h.line_count} for h in fd.hunks],
        }
        for fd in file_diffs
    ]

    adaptation = adapt_instance(
        model=llm_model,
        instance_id=instance.instance_id,
        repo=instance.repo,
        problem_statement=instance.problem_statement,
        hints_text=instance.hints_text,
        patch_text=diff_text,
    )

    gt_dir.mkdir(parents=True, exist_ok=True)

    raw_instance = {
        "workspace_id": wid,
        "source": "swebench",
        "source_dataset": instance.source_dataset,
        "source_split": instance.source_split,
        "source_instance_id": instance.instance_id,
        "source_repo": instance.repo,
        "base_commit": instance.base_commit,
        "logical_repo_id": logical_repo_id_from_slug(instance.repo),
        "problem_statement": instance.problem_statement,
        "hints_text": instance.hints_text,
        "diff": diff_text,
        "patch_files": patch_summary,
        "task_complexity": adaptation.task_complexity,
        "confidence": adaptation.confidence,
        "solve_notes": adaptation.solve_notes,
        "tier_difference_reasoning": adaptation.tier_difference_reasoning,
    }
    raw_path.write_text(json.dumps(raw_instance, indent=2))

    queries = {
        "workspace_id": wid,
        "queries": adaptation.queries,
    }
    (gt_dir / "queries.json").write_text(json.dumps(queries, indent=2))

    non_ok = {
        "repo_id": wid,
        "logical_repo_id": logical_repo_id_from_slug(instance.repo),
        "source_instance_id": instance.instance_id,
        "non_ok_queries": adaptation.non_ok_queries,
    }
    (gt_dir / "non_ok_queries.json").write_text(json.dumps(non_ok, indent=2))

    write_repo_manifest(
        repo_dir,
        {
            "workspace_id": wid,
            "repo_set": instance.repo_set,
            "logical_repo_id": logical_repo_id_from_slug(instance.repo),
            "source_repo": instance.repo,
            "source_dataset": instance.source_dataset,
            "source_split": instance.source_split,
            "instance_id": instance.instance_id,
            "base_commit": instance.base_commit,
            "clone_dir": str(clone_dir),
        },
    )

    elapsed = round(time.monotonic() - t0, 1)
    if verbose:
        logger.info("swebench.import_ok", extra={"workspace_id": wid, "elapsed_sec": elapsed})

    return {
        "workspace_id": wid,
        "status": "ok",
        "queries": len(adaptation.queries) + len(adaptation.non_ok_queries),
    }
