"""Re-generate LLM queries for the 71 instances with confidence=None.

These instances have defs (from earlier pr-import) but failed LLM query
generation.  This script reads the existing task JSON, calls adapt_instance()
to generate queries, and updates the task JSON in-place.

Uses llm_client.py (gh auth token) — NOT litellm.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure the cpl_lab package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cpl_lab.llm.llm_queries import adapt_instance
from cpl_lab.pipeline.clone import clone_dir_for

DATA_DIR = Path.home() / ".recon/recon-lab/data"
CLONES_DIR = Path.home() / ".recon/recon-lab/clones"
LLM_MODEL = "openai/gpt-4-1-nano"


def find_null_confidence_instances() -> list[tuple[str, Path]]:
    """Find PR instances with confidence=None (0 queries)."""
    results = []
    for d in sorted(DATA_DIR.iterdir()):
        if "_pr" not in d.name or not d.is_dir():
            continue
        gt = d / "ground_truth"
        if not gt.is_dir():
            continue
        task_json = gt / f"{d.name}.json"
        if not task_json.exists():
            continue
        task = json.loads(task_json.read_text())
        if task.get("confidence") is None:
            results.append((d.name, task_json))
    return results


def regen_one(instance_id: str, task_json_path: Path) -> dict:
    """Regenerate queries for one instance."""
    task = json.loads(task_json_path.read_text())
    rid = task["repo_id"]
    title = task.get("title", "")
    body = task.get("body", "")
    diff_text = task.get("diff", "")
    min_defs = task.get("minimum_sufficient_defs", [])

    main_clone = clone_dir_for(rid, CLONES_DIR)
    if main_clone is None:
        return {"instance_id": instance_id, "status": "error", "error": f"no clone for {rid}"}

    main_index_db = main_clone / ".recon" / "index.db"
    if not main_index_db.exists():
        return {"instance_id": instance_id, "status": "error", "error": "no index.db"}

    problem = title
    if body and body.strip():
        problem += "\n\n" + body[:2000]

    try:
        result = adapt_instance(
            model=LLM_MODEL,
            instance_id=instance_id,
            repo=rid,
            problem_statement=problem,
            hints_text="",
            patch_text=diff_text[:6000],
            index_db=main_index_db,
            clone_dir=main_clone,
            minimum_sufficient_defs=min_defs,
        )
    except Exception as exc:
        return {"instance_id": instance_id, "status": "error", "error": str(exc)}

    # Update the task JSON
    task["task_complexity"] = result.task_complexity
    task["confidence"] = result.confidence
    task["solve_notes"] = result.solve_notes
    task["queries"] = result.queries
    task["non_ok_queries"] = result.non_ok_queries

    task_json_path.write_text(json.dumps(task, indent=2, ensure_ascii=False))

    return {
        "instance_id": instance_id,
        "status": "ok",
        "queries": len(result.queries),
        "confidence": result.confidence,
    }


def main():
    targets = find_null_confidence_instances()
    print(f"Found {len(targets)} instances with confidence=None")

    if not targets:
        print("Nothing to do.")
        return

    # Test with 1 first
    print(f"\nTesting with {targets[0][0]}...")
    test_result = regen_one(targets[0][0], targets[0][1])
    print(f"  Result: {json.dumps(test_result)}")

    if test_result["status"] != "ok":
        print("Test failed — aborting.")
        sys.exit(1)

    # Process remaining with workers
    remaining = targets[1:]
    if not remaining:
        print("Done (only 1 instance).")
        return

    print(f"\nProcessing remaining {len(remaining)} instances with 4 workers...")
    t0 = time.monotonic()
    ok = errors = 0

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(regen_one, iid, path): iid
            for iid, path in remaining
        }
        for future in as_completed(futures):
            iid = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"instance_id": iid, "status": "error", "error": str(exc)}

            if result["status"] == "ok":
                ok += 1
            else:
                errors += 1
                print(f"  FAIL: {iid}: {result.get('error', 'unknown')}")

    elapsed = time.monotonic() - t0
    # +1 for the test instance
    print(f"\nDone in {elapsed:.0f}s: {ok + 1} ok, {errors} errors")


if __name__ == "__main__":
    main()
