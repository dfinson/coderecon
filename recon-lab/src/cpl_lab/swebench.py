"""SWE-bench-backed ground-truth importer."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import click
from datasets import load_dataset

from cpl_lab.data_manifest import write_repo_manifest
from cpl_lab.llm_filter import filter_candidates
from cpl_lab.patch_ground_truth import DefEntry, map_hunks_to_defs, parse_unified_diff
from cpl_lab.swebench_llm import adapt_instance

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwebenchInstance:
    instance_id: str
    repo: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str
    hints_text: str
    source_dataset: str
    source_split: str
    repo_set: str


def run_swebench(
    *,
    data_dir: Path,
    clones_dir: Path,
    repo_set: str,
    repo: str | None,
    max_instances: int,
    llm_model: str,
    filter_model: str,
    training_dataset: str,
    training_split: str,
    eval_dataset: str,
    eval_split: str,
    cutoff_mod: int,
    cutoff_remainder: int,
    verbose: bool = False,
) -> None:
    """Import SWE-bench instances and emit GT artifacts."""
    instances = list(
        _select_instances(
            repo_set=repo_set,
            repo_filter=repo,
            training_dataset=training_dataset,
            training_split=training_split,
            eval_dataset=eval_dataset,
            eval_split=eval_split,
            cutoff_mod=cutoff_mod,
            cutoff_remainder=cutoff_remainder,
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
                filter_model=filter_model,
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
                f"  OK {summary['workspace_id']} | defs={summary['minimum_defs']}/{summary['thrash_defs']}"
            )

    click.echo(f"\nSWE-bench import complete: {ok} ok, {skipped} skipped, {failed} failed")


def _select_instances(
    *,
    repo_set: str,
    repo_filter: str | None,
    training_dataset: str,
    training_split: str,
    eval_dataset: str,
    eval_split: str,
    cutoff_mod: int,
    cutoff_remainder: int,
) -> Iterable[SwebenchInstance]:
    requested_sets = {repo_set} if repo_set != "all" else {"ranker-gate", "cutoff", "eval"}

    if requested_sets & {"ranker-gate", "cutoff"}:
        for row in load_dataset(training_dataset, split=training_split):
            instance = _row_to_instance(
                row,
                source_dataset=training_dataset,
                source_split=training_split,
                repo_set=_training_repo_set(
                    instance_id=row["instance_id"],
                    cutoff_mod=cutoff_mod,
                    cutoff_remainder=cutoff_remainder,
                ),
            )
            if instance.repo_set not in requested_sets:
                continue
            if _repo_matches(instance, repo_filter):
                yield instance

    if "eval" in requested_sets:
        for row in load_dataset(eval_dataset, split=eval_split):
            instance = _row_to_instance(
                row,
                source_dataset=eval_dataset,
                source_split=eval_split,
                repo_set="eval",
            )
            if _repo_matches(instance, repo_filter):
                yield instance


def _training_repo_set(*, instance_id: str, cutoff_mod: int, cutoff_remainder: int) -> str:
    digest = hashlib.sha1(instance_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % max(cutoff_mod, 1)
    return "cutoff" if bucket == cutoff_remainder else "ranker-gate"


def _repo_matches(instance: SwebenchInstance, repo_filter: str | None) -> bool:
    if not repo_filter:
        return True
    repo_filter = repo_filter.strip().lower()
    logical_repo_id = _logical_repo_id(instance.repo)
    return repo_filter in {instance.repo.lower(), logical_repo_id.lower()}


def _row_to_instance(
    row: dict[str, Any],
    *,
    source_dataset: str,
    source_split: str,
    repo_set: str,
) -> SwebenchInstance:
    return SwebenchInstance(
        instance_id=str(row["instance_id"]),
        repo=str(row["repo"]),
        base_commit=str(row["base_commit"]),
        patch=str(row.get("patch", "") or ""),
        test_patch=str(row.get("test_patch", "") or ""),
        problem_statement=str(row.get("problem_statement", "") or ""),
        hints_text=str(row.get("hints_text", "") or ""),
        source_dataset=source_dataset,
        source_split=source_split,
        repo_set=repo_set,
    )


def _process_instance(
    *,
    instance: SwebenchInstance,
    data_dir: Path,
    clones_dir: Path,
    llm_model: str,
    filter_model: str,
    verbose: bool,
) -> dict[str, Any]:
    t0 = time.monotonic()
    workspace_id = _workspace_id(instance.instance_id)
    repo_dir = data_dir / workspace_id
    gt_dir = repo_dir / "ground_truth"
    task_path = gt_dir / f"{workspace_id}.json"
    if task_path.exists() and (gt_dir / "non_ok_queries.json").exists():
        return {"status": "skip", "reason": "already imported", "workspace_id": workspace_id}

    clone_dir = _ensure_instance_checkout(instance, clones_dir)
    index_db = clone_dir / ".recon" / "index.db"
    if not index_db.exists():
        raise FileNotFoundError(f"Missing index.db for {instance.instance_id}")

    diff_text = _combined_patch(instance)
    file_diffs = parse_unified_diff(diff_text)
    if not file_diffs:
        return {"status": "skip", "reason": "empty patch", "workspace_id": workspace_id}

    minimum_sufficient, thrash_preventing, excluded = map_hunks_to_defs(file_diffs, index_db)
    if not minimum_sufficient:
        return {"status": "skip", "reason": "no indexed defs mapped", "workspace_id": workspace_id}

    min_suff_dicts = [_def_to_dict(entry) for entry in minimum_sufficient]
    thrash_candidate_dicts = [_def_to_dict(entry) for entry in thrash_preventing]
    filter_result = filter_candidates(
        issue_title=_first_line(instance.problem_statement),
        issue_body=instance.problem_statement,
        min_suff_defs=min_suff_dicts,
        thrash_prev_defs=thrash_candidate_dicts,
        model=filter_model,
    )
    filtered_thrash = [_dict_to_def(entry) for entry in filter_result.kept]

    adaptation = adapt_instance(
        model=llm_model,
        instance_id=instance.instance_id,
        repo=instance.repo,
        problem_statement=instance.problem_statement,
        hints_text=instance.hints_text,
        patch_text=diff_text,
        minimum_sufficient_defs=min_suff_dicts,
        thrash_preventing_defs=[_def_to_dict(entry) for entry in filtered_thrash],
    )

    gt_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "task_id": workspace_id,
        "task_complexity": adaptation.task_complexity,
        "task_text": instance.problem_statement,
        "diff": diff_text,
        "solve_notes": adaptation.solve_notes,
        "confidence": adaptation.confidence,
        "source": "swebench",
        "source_dataset": instance.source_dataset,
        "source_split": instance.source_split,
        "source_instance_id": instance.instance_id,
        "source_repo": instance.repo,
        "base_commit": instance.base_commit,
        "logical_repo_id": _logical_repo_id(instance.repo),
        "minimum_sufficient_defs": [_def_to_dict(entry) for entry in minimum_sufficient],
        "thrash_preventing_defs": [_def_to_dict(entry) for entry in filtered_thrash],
        "tier_difference_reasoning": adaptation.tier_difference_reasoning,
        "excluded_defs": [_def_to_dict(entry) for entry in excluded],
        "queries": adaptation.queries,
    }
    task_path.write_text(json.dumps(task, indent=2))

    non_ok = {
        "repo_id": workspace_id,
        "logical_repo_id": _logical_repo_id(instance.repo),
        "source_instance_id": instance.instance_id,
        "non_ok_queries": adaptation.non_ok_queries,
    }
    (gt_dir / "non_ok_queries.json").write_text(json.dumps(non_ok, indent=2))

    write_repo_manifest(
        repo_dir,
        {
            "workspace_id": workspace_id,
            "repo_set": instance.repo_set,
            "logical_repo_id": _logical_repo_id(instance.repo),
            "source_repo": instance.repo,
            "source_dataset": instance.source_dataset,
            "source_split": instance.source_split,
            "instance_id": instance.instance_id,
            "base_commit": instance.base_commit,
            "clone_dir": str(clone_dir),
        },
    )

    summary = {
        "workspace_id": workspace_id,
        "logical_repo_id": _logical_repo_id(instance.repo),
        "status": "ok",
        "minimum_defs": len(minimum_sufficient),
        "thrash_defs": len(filtered_thrash),
        "elapsed_sec": round(time.monotonic() - t0, 1),
    }
    (repo_dir / "swebench_summary.json").write_text(json.dumps(summary, indent=2))
    if verbose:
        logger.info("swebench.instance_imported", extra=summary)
    return summary


def _ensure_instance_checkout(instance: SwebenchInstance, clones_dir: Path) -> Path:
    mirror_root = clones_dir / "_swebench_mirrors"
    instance_root = clones_dir / "instances"
    mirror_root.mkdir(parents=True, exist_ok=True)
    instance_root.mkdir(parents=True, exist_ok=True)

    repo_slug = instance.repo.replace("/", "__")
    mirror_dir = mirror_root / repo_slug
    if not mirror_dir.exists():
        _run_git(["clone", "--mirror", f"https://github.com/{instance.repo}.git", str(mirror_dir)])
    else:
        _run_git(["remote", "update", "--prune"], cwd=mirror_dir)

    try:
        _run_git(["rev-parse", "--verify", instance.base_commit], cwd=mirror_dir)
    except RuntimeError:
        _run_git(["fetch", "origin", instance.base_commit, "--depth=1"], cwd=mirror_dir)

    instance_dir = instance_root / _workspace_id(instance.instance_id)
    if not instance_dir.exists():
        _run_git(["worktree", "add", "--detach", str(instance_dir), instance.base_commit], cwd=mirror_dir)
    else:
        current = _run_git(["rev-parse", "HEAD"], cwd=instance_dir).strip()
        if current != instance.base_commit:
            raise RuntimeError(
                f"Existing instance checkout at {instance_dir} is pinned to {current[:12]}, expected {instance.base_commit[:12]}"
            )

    if not (instance_dir / ".recon" / "index.db").exists():
        from cpl_lab.index import _find_cpl

        subprocess.run([_find_cpl(), "init", str(instance_dir)], check=True, capture_output=True, text=True)

    return instance_dir


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


def _combined_patch(instance: SwebenchInstance) -> str:
    patches = [instance.patch.strip(), instance.test_patch.strip()]
    return "\n".join(part for part in patches if part)


def _def_to_dict(entry: DefEntry) -> dict[str, Any]:
    return {
        "path": entry.path,
        "name": entry.name,
        "kind": entry.kind,
        "start_line": entry.start_line,
        "end_line": entry.end_line,
        "reason": entry.reason,
    }


def _dict_to_def(entry: dict[str, Any]) -> DefEntry:
    return DefEntry(
        path=str(entry["path"]),
        name=str(entry["name"]),
        kind=str(entry["kind"]),
        start_line=int(entry["start_line"]),
        end_line=int(entry["end_line"]),
        reason=str(entry.get("reason", "")),
    )


def _workspace_id(instance_id: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in instance_id)


def _logical_repo_id(repo: str) -> str:
    return repo.replace("/", "__")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return "SWE-bench issue"