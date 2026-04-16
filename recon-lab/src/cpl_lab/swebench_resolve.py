"""SWE-bench resolve — map patch hunks to indexed defs, expand via coverage."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import click

from cpl_lab.patch_ground_truth import parse_unified_diff, expand_defs_via_coverage
from cpl_lab.swebench_common import (
    def_to_dict,
    repo_name_matches,
)

logger = logging.getLogger(__name__)


def run_swebench_resolve(
    *,
    data_dir: Path,
    clones_dir: Path,
    repo_set: str,
    repo: str | None,
    verbose: bool = False,
) -> None:
    """Resolve defs — map patch hunks to indexed defs, expand via coverage."""
    from cpl_lab.data_manifest import iter_repo_data_dirs, load_repo_manifest

    candidates = []
    for repo_dir in iter_repo_data_dirs(data_dir):
        manifest = load_repo_manifest(repo_dir)
        raw_path = repo_dir / "ground_truth" / "raw_instance.json"
        if not raw_path.exists():
            continue
        inst_set = manifest.get("repo_set", "")
        if repo_set != "all" and inst_set != repo_set:
            continue
        if repo and not repo_name_matches(manifest, repo):
            continue
        candidates.append(repo_dir)

    if not candidates:
        click.echo("No unresolved SWE-bench instances found.")
        return

    ok = skipped = failed = 0
    for index, repo_dir in enumerate(candidates, start=1):
        workspace_id = repo_dir.name
        click.echo(f"[{index}/{len(candidates)}] {workspace_id}")
        try:
            summary = resolve_instance(
                repo_dir=repo_dir,
                clones_dir=clones_dir,
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
            cov = summary.get('coverage_linked_defs', 0)
            cov_str = f" +{cov} coverage" if cov else ""
            click.echo(
                f"  OK defs={summary['minimum_defs']}{cov_str}"
            )

    click.echo(f"\nSWE-bench resolve: {ok} ok, {skipped} skipped, {failed} failed")


def resolve_instance(
    *,
    repo_dir: Path,
    clones_dir: Path,
    verbose: bool,
) -> dict[str, Any]:
    """Resolve a single instance: map hunks to indexed defs.

    Reads ``raw_instance.json`` and ``queries.json``, produces the merged
    ``{workspace_id}.json`` task file used by downstream pipeline stages.
    """
    from cpl_lab.data_manifest import load_repo_manifest
    from cpl_lab.patch_ground_truth import map_hunks_to_defs

    t0 = time.monotonic()
    workspace_id = repo_dir.name
    gt_dir = repo_dir / "ground_truth"
    task_path = gt_dir / f"{workspace_id}.json"

    if task_path.exists():
        return {"status": "skip", "reason": "already resolved"}

    raw_path = gt_dir / "raw_instance.json"
    queries_path = gt_dir / "queries.json"
    if not raw_path.exists():
        return {"status": "skip", "reason": "no raw_instance.json — run import first"}

    raw = json.loads(raw_path.read_text())
    diff_text = raw["diff"]
    file_diffs = parse_unified_diff(diff_text)
    if not file_diffs:
        return {"status": "skip", "reason": "empty patch"}

    manifest = load_repo_manifest(repo_dir)
    clone_dir_str = manifest.get("clone_dir")
    if not clone_dir_str:
        return {"status": "skip", "reason": "no clone_dir in manifest"}
    clone_dir = Path(clone_dir_str)

    index_db = clone_dir / ".recon" / "index.db"
    if not index_db.exists():
        raise FileNotFoundError(
            f"Missing index.db for {workspace_id} at {index_db} — "
            f"import stage should have created it"
        )

    minimum_sufficient = map_hunks_to_defs(file_diffs, index_db)
    if not minimum_sufficient:
        return {"status": "skip", "reason": "no indexed defs mapped"}

    min_suff_dicts = [def_to_dict(entry) for entry in minimum_sufficient]

    # Expand GT via deterministic test↔source coverage links
    coverage_linked = expand_defs_via_coverage(minimum_sufficient, index_db)
    coverage_linked_dicts = [def_to_dict(entry) for entry in coverage_linked]

    ok_queries: list[dict[str, Any]] = []
    if queries_path.exists():
        ok_queries = json.loads(queries_path.read_text()).get("queries", [])

    task = {
        "task_id": workspace_id,
        "task_complexity": raw.get("task_complexity", "unknown"),
        "task_text": raw["problem_statement"],
        "diff": diff_text,
        "solve_notes": raw.get("solve_notes", ""),
        "confidence": raw.get("confidence", "unknown"),
        "source": "swebench",
        "source_dataset": raw.get("source_dataset", ""),
        "source_split": raw.get("source_split", ""),
        "source_instance_id": raw.get("source_instance_id", ""),
        "source_repo": raw.get("source_repo", ""),
        "base_commit": raw.get("base_commit", ""),
        "logical_repo_id": raw.get("logical_repo_id", ""),
        "minimum_sufficient_defs": min_suff_dicts,
        "coverage_linked_defs": coverage_linked_dicts,
        "excluded_defs": [],
        "queries": ok_queries,
    }
    task_path.write_text(json.dumps(task, indent=2))

    summary = {
        "workspace_id": workspace_id,
        "status": "ok",
        "minimum_defs": len(minimum_sufficient),
        "coverage_linked_defs": len(coverage_linked),
        "elapsed_sec": round(time.monotonic() - t0, 1),
    }
    (repo_dir / "swebench_summary.json").write_text(json.dumps(summary, indent=2))
    if verbose:
        logger.info("swebench.resolve_ok", extra=summary)
    return summary
