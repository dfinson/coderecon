"""Re-import the 71 PR instances that had zero queries.

These worktrees lack .recon/index.db so `import_single_instance` fails at
`map_hunks_to_defs`.  This script bypasses that by reading the original
minimum_sufficient_defs from the audit records, then calling
`adapt_instance()` for the LLM queries and writing the task JSON directly.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO, format="%(levelname)s  %(message)s", stream=sys.stderr
)
logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".recon/recon-lab/data"
CLONES_DIR = Path.home() / ".recon/recon-lab/clones"
INSTANCES_DIR = CLONES_DIR / "instances"
LLM_MODEL = "openai/gpt-4-1-nano"
WORKERS = 4


def _find_missing_task_json_instances() -> list[str]:
    """Find instance IDs whose ground_truth dir exists but has no task JSON."""
    missing = []
    for d in sorted(DATA_DIR.iterdir()):
        gt = d / "ground_truth"
        if not gt.is_dir():
            continue
        # PR worktrees have '_pr' in instance ID (e.g. cpp-fmt_pr4643)
        if "_pr" not in d.name:
            continue
        task_jsons = [f for f in gt.iterdir() if f.suffix == ".json" and f.name != "summary.json"]
        if not task_jsons:
            missing.append(d.name)
    return missing


def _load_pr_instances() -> dict[str, dict[str, Any]]:
    """Load pr_instances.jsonl into a dict keyed by instance_id."""
    pr_file = DATA_DIR / "pr_instances.jsonl"
    instances = {}
    for line in pr_file.read_text().splitlines():
        if not line.strip():
            continue
        inst = json.loads(line)
        instances[inst["instance_id"]] = inst
    return instances


def _load_audit_defs(instance_id: str) -> list[dict[str, Any]]:
    """Load minimum_sufficient_defs from the audit record."""
    audit_file = DATA_DIR / instance_id / "audit" / "audit_records.jsonl"
    if not audit_file.exists():
        return []
    first_line = audit_file.read_text().split("\n", 1)[0].strip()
    if not first_line:
        return []
    record = json.loads(first_line)
    return record.get("justifications", {}).get("minimum_sufficient_defs", [])


def reimport_one(
    inst: dict[str, Any],
    min_defs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Re-import one instance using cached defs, calling LLM for queries."""
    from cpl_lab.llm.llm_queries import adapt_instance
    from cpl_lab.pipeline.patch_ground_truth import parse_unified_diff
    from cpl_lab.data_manifest import write_repo_manifest

    iid = inst["instance_id"]
    rid = inst["repo_id"]
    diff_text = inst["diff_text"]
    title = inst["title"]
    body = inst.get("body", "")
    wt_dir = INSTANCES_DIR / iid

    # We need file_diffs for the files field
    try:
        file_diffs = parse_unified_diff(diff_text)
    except Exception:
        file_diffs = []

    # Build problem statement
    parts = [title]
    if body and body.strip():
        parts.append("")
        parts.append(body[:2000])
    problem = "\n".join(parts)

    # Find main repo index
    from cpl_lab.pipeline.clone import clone_dir_for
    main_clone_dir = clone_dir_for(rid, CLONES_DIR)
    main_index_db = main_clone_dir / ".recon" / "index.db" if main_clone_dir else None

    # Call LLM for queries
    result = None
    try:
        result = adapt_instance(
            model=LLM_MODEL,
            instance_id=iid,
            repo=rid,
            problem_statement=problem,
            hints_text="",
            patch_text=diff_text[:6000],
            index_db=main_index_db or Path("/dev/null"),
            clone_dir=main_clone_dir or Path("/dev/null"),
            minimum_sufficient_defs=min_defs,
        )
    except Exception as exc:
        logger.warning("%s: LLM failed: %s", iid, exc)

    # Write task JSON
    inst_data_dir = DATA_DIR / iid / "ground_truth"
    inst_data_dir.mkdir(parents=True, exist_ok=True)

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

    (inst_data_dir / f"{iid}.json").write_text(
        json.dumps(task_json, indent=2, ensure_ascii=False)
    )

    # Write manifest
    write_repo_manifest(DATA_DIR / iid, {
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

    n_queries = len(task_json.get("queries", []))
    status = "ok" if result and n_queries > 0 else "no_queries"
    return {"instance_id": iid, "status": status, "queries": n_queries}


def main() -> None:
    missing = _find_missing_task_json_instances()
    logger.info("Found %d instances with missing task JSONs", len(missing))
    if not missing:
        return

    pr_instances = _load_pr_instances()

    # Prepare work items
    work: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for iid in missing:
        inst = pr_instances.get(iid)
        if inst is None:
            logger.warning("%s: not found in pr_instances.jsonl, skipping", iid)
            continue
        defs = _load_audit_defs(iid)
        if not defs:
            logger.warning("%s: no defs in audit record, skipping", iid)
            continue
        work.append((inst, defs))

    logger.info("Importing %d instances (%d workers)", len(work), WORKERS)
    t0 = time.monotonic()

    ok = errors = no_queries = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(reimport_one, inst, defs): inst["instance_id"] for inst, defs in work}
        for i, fut in enumerate(as_completed(futures), 1):
            iid = futures[fut]
            try:
                res = fut.result()
                if res["status"] == "ok":
                    ok += 1
                else:
                    no_queries += 1
                if i % 10 == 0 or i == len(work):
                    logger.info("  [%d/%d] %s → %s (%d queries)", i, len(work), iid, res["status"], res["queries"])
            except Exception as exc:
                errors += 1
                logger.error("  [%d/%d] %s → error: %s", i, len(work), iid, exc)

    elapsed = time.monotonic() - t0
    logger.info("Done in %.0fs: %d ok, %d no_queries, %d errors", elapsed, ok, no_queries, errors)


if __name__ == "__main__":
    main()
