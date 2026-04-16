"""PR import — generate ground truth defs and LLM queries for PR instances.

For each PR in ``pr_instances.jsonl``:
  1. Re-map diff hunks against the **worktree** index (not main).
  2. Generate 8 OK + 6 non-OK queries via ``adapt_instance()``
     (same GPT-4.1-nano flow as SWE-bench).
  3. Write per-instance ground truth + query files.

No coverage expansion — GT is purely the patch defs.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import click

from cpl_lab.clone import clone_dir_for

logger = logging.getLogger(__name__)


def _build_problem_statement(title: str, body: str) -> str:
    """Combine PR title and body into a problem statement for LLM context."""
    parts = [title]
    if body and body.strip():
        parts.append("")
        parts.append(body[:2000])
    return "\n".join(parts)


def import_single_instance(
    inst: dict[str, Any],
    instances_dir: Path,
    data_dir: Path,
    llm_model: str,
    prior_broad: list[str],
    prior_ambig: list[str],
    verbose: bool = False,
) -> dict[str, Any]:
    """Import a single PR instance: GT defs + queries.

    Returns a summary dict with status and counts.
    """
    from cpl_lab.patch_ground_truth import map_hunks_to_defs, parse_unified_diff
    from cpl_lab.llm_queries import adapt_instance

    iid = inst["instance_id"]
    rid = inst["repo_id"]
    diff_text = inst["diff_text"]
    title = inst["title"]
    body = inst.get("body", "")

    wt_dir = instances_dir / iid
    index_db = wt_dir / ".recon" / "index.db"

    summary: dict[str, Any] = {
        "instance_id": iid,
        "repo_id": rid,
        "status": "error",
        "gt_defs": 0,
        "queries": 0,
    }

    # Check worktree is indexed
    if not index_db.exists():
        summary["error"] = "worktree not indexed"
        return summary

    # Parse diff and map to defs
    try:
        file_diffs = parse_unified_diff(diff_text)
    except Exception as exc:
        summary["error"] = f"diff parse failed: {exc}"
        return summary

    if not file_diffs:
        summary["error"] = "empty diff"
        return summary

    try:
        gt_defs = map_hunks_to_defs(file_diffs, index_db)
    except Exception as exc:
        summary["error"] = f"hunk mapping failed: {exc}"
        return summary

    if not gt_defs:
        summary["error"] = "no defs overlap with diff hunks"
        return summary

    # Build minimum_sufficient_defs in the expected format
    min_defs = [
        {
            "path": d.path,
            "name": d.name,
            "kind": d.kind,
            "start_line": d.start_line,
            "end_line": d.end_line,
            "reason": d.reason,
        }
        for d in gt_defs
    ]

    # Generate queries via LLM
    problem = _build_problem_statement(title, body)
    try:
        result = adapt_instance(
            model=llm_model,
            instance_id=iid,
            repo=rid,
            problem_statement=problem,
            hints_text="",
            patch_text=diff_text[:6000],
            index_db=index_db,
            clone_dir=wt_dir,
            minimum_sufficient_defs=min_defs,
            prior_broad=prior_broad,
            prior_ambig=prior_ambig,
        )
    except Exception as exc:
        summary["error"] = f"LLM adaptation failed: {exc}"
        # Still write the GT even if queries fail
        result = None

    # Write output files
    inst_data_dir = data_dir / iid / "ground_truth"
    inst_data_dir.mkdir(parents=True, exist_ok=True)

    # Per-task JSON that _parse_raw_task_jsons can consume.
    # Must have: task_id, minimum_sufficient_defs, queries.
    task_json: dict[str, Any] = {
        "task_id": iid,
        "instance_id": iid,
        "repo_id": rid,
        "repo_set": inst.get("repo_set", ""),
        "pr_number": inst.get("pr_number"),
        "base_commit": inst.get("base_commit"),
        "merge_commit": inst.get("merge_commit"),
        "title": title,
        "body": body[:5000],
        "diff": diff_text,
        "minimum_sufficient_defs": min_defs,
        "files": [
            {"path": fd.path, "hunks": len(fd.hunks), "is_new": fd.is_new_file}
            for fd in file_diffs
        ],
    }
    if result:
        task_json["task_complexity"] = result.task_complexity
        task_json["confidence"] = result.confidence
        task_json["solve_notes"] = result.solve_notes
        task_json["queries"] = result.queries
        task_json["non_ok_queries"] = result.non_ok_queries
    else:
        task_json["queries"] = []
        task_json["non_ok_queries"] = []

    # Write as {instance_id}.json (the filename _parse_raw_task_jsons expects)
    (inst_data_dir / f"{iid}.json").write_text(
        json.dumps(task_json, indent=2, ensure_ascii=False)
    )

    # manifest.json — provenance for downstream stages
    from cpl_lab.data_manifest import write_repo_manifest
    write_repo_manifest(data_dir / iid, {
        "workspace_id": iid,
        "repo_id": rid,
        "repo_set": inst.get("repo_set", ""),
        "logical_repo_id": rid,
        "clone_dir": str(wt_dir),
        "source": "pr",
        "pr_number": inst.get("pr_number"),
        "base_commit": inst.get("base_commit"),
        "merge_commit": inst.get("merge_commit"),
    })

    summary["status"] = "ok" if result else "gt_only"
    summary["gt_defs"] = len(min_defs)
    summary["queries"] = len(task_json.get("queries", []))
    summary.pop("error", None)
    return summary


def run_pr_import(
    data_dir: Path,
    clones_dir: Path,
    llm_model: str = "openai/gpt-4-1-nano",
    repo_set: str = "all",
    repo: str | None = None,
    max_instances: int = 0,
    verbose: bool = False,
) -> None:
    """Import all PR instances: diff→GT + query generation."""
    instances_dir = clones_dir / "instances"

    pr_file = data_dir / "pr_instances.jsonl"
    if not pr_file.exists():
        raise click.ClickException(f"PR instances file not found: {pr_file}")

    # Load instances
    instances: list[dict] = []
    for line in pr_file.read_text().splitlines():
        if not line.strip():
            continue
        inst = json.loads(line)
        if repo and inst["repo_id"] != repo:
            continue
        if repo_set != "all" and inst.get("repo_set") != repo_set:
            continue
        instances.append(inst)

    if max_instances > 0:
        instances = instances[:max_instances]

    if not instances:
        click.echo("No instances to import.")
        return

    # Skip already-imported instances
    todo: list[dict] = []
    for inst in instances:
        gt_dir = data_dir / inst["instance_id"] / "ground_truth"
        task_json = gt_dir / f"{inst['instance_id']}.json"
        if task_json.exists():
            continue
        todo.append(inst)

    click.echo(f"Importing {len(todo)} PR instances ({len(instances) - len(todo)} already done)")

    # Per-repo accumulators for BROAD/AMBIG diversity
    prior_broad: dict[str, list[str]] = {}
    prior_ambig: dict[str, list[str]] = {}

    ok = gt_only = errors = 0
    t0 = time.monotonic()

    for i, inst in enumerate(todo, 1):
        rid = inst["repo_id"]
        iid = inst["instance_id"]

        if verbose or i % 10 == 0 or i == len(todo):
            click.echo(f"  [{i}/{len(todo)}] {iid}")

        pb = prior_broad.setdefault(rid, [])
        pa = prior_ambig.setdefault(rid, [])

        summary = import_single_instance(
            inst, instances_dir, data_dir, llm_model, pb, pa, verbose=verbose,
        )

        if summary["status"] == "ok":
            ok += 1
        elif summary["status"] == "gt_only":
            gt_only += 1
        else:
            errors += 1
            if verbose:
                click.echo(f"    ERROR: {summary.get('error', 'unknown')}")

    elapsed = time.monotonic() - t0
    click.echo(f"\nDone in {elapsed:.0f}s: {ok} ok, {gt_only} gt-only, {errors} errors")

    # Write import stamp
    stamp = data_dir / "pr_import.stamp"
    stamp.write_text(json.dumps({
        "total": len(todo),
        "ok": ok,
        "gt_only": gt_only,
        "errors": errors,
        "elapsed_sec": round(elapsed, 1),
    }, indent=2))
