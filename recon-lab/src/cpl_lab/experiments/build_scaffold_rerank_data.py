"""Build scaffold reranking fixture using coderecon index + SWE-bench.

GT determination:
    Parse the gold patch diff to get base-file line numbers with removals.
    Query the coderecon index to find DefFacts whose [start_line, end_line]
    spans overlap those lines — AST-accurate, no regex heuristics.

Candidate pool:
    raw_signals_pipeline on the problem statement — the same multi-retriever
    query used in production — with proper signature_text from the index.
    GT defs not surfaced by retrieval are injected into the pool so the LLM
    always sees what needs to change.

baseline_rank:
    Candidates are ordered by retriever agreement (term-match/graph).
    Candidates not found by any retriever get a large fallback rank.

Prerequisites:
    Run ``recon init`` on ONE worktree per repo — all SWE-bench instances of that
    repo share the same index.  The worktrees already exist as git worktrees in
    ``~/.recon/recon-lab/clones/instances/``; just pick one per repo and index it:

        # Index one flask instance (~30s), covers all 11 flask instances:
        python -m coderecon.cli.main init \
            ~/.recon/recon-lab/clones/instances/pallets__flask_4045

        # Index one pytest instance (~25s), covers all 119 pytest instances:
        python -m coderecon.cli.main init \
            ~/.recon/recon-lab/clones/instances/pytest_dev__pytest_8399

Usage:
    python -m cpl_lab.experiments.build_scaffold_rerank_data --repo-filter pytest-dev/pytest
    python -m cpl_lab.experiments.build_scaffold_rerank_data --max-tasks 50
    python -m cpl_lab.experiments.build_scaffold_rerank_data --instances-dir ~/my/clones/instances

Usage:
    python -m cpl_lab.experiments.build_scaffold_rerank_data
    python -m cpl_lab.experiments.build_scaffold_rerank_data --max-tasks 50
    python -m cpl_lab.experiments.build_scaffold_rerank_data --instances-dir ~/my/clones/instances

Output JSONL (one record per SWE-bench instance):
    {
        "task_id":           "astropy__astropy-12345",
        "repo_id":           "astropy__astropy",
        "problem_statement": "...",
        "query_type":        "TASK",
        "candidates": [
            {
                "def_key":        "src/mod.py:function:parse:42",
                "path":           "src/mod.py",
                "kind":           "function",
                "name":           "parse",
                "start_line":     42,
                "end_line":       55,
                "signature_text": "def parse(text: str) -> dict:",
                "namespace":      "",
                "baseline_rank":  3,
                "is_gt_edited":   true,
                "is_gt_read":     false
            }
        ],
        "gt_edited_keys": ["src/mod.py:function:parse:42"],
        "gt_read_keys":   []
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coderecon.mcp.context import AppContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Patch parsing — changed lines only (no def detection)
# ---------------------------------------------------------------------------

_HUNK_HEADER_RE = re.compile(r'^@@\s+-(\d+)(?:,\d+)?')


def _normalise_path(raw: str) -> str:
    for prefix in ("b/", "a/"):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def _changed_lines_per_file(patch: str) -> dict[str, set[int]]:
    """Parse unified diff -> {rel_path: set of base-file line numbers for '-' lines}.

    '-' lines are base-file lines that were removed or replaced.  A DefFact
    whose [start_line, end_line] span contains any such line was edited.
    """
    result: dict[str, set[int]] = {}
    current_file = ""
    base_line = 0

    for raw in patch.splitlines():
        if raw.startswith("diff --git "):
            current_file = ""
            base_line = 0
            continue

        if raw.startswith("--- "):
            path = raw[4:].strip()
            current_file = "" if path == "/dev/null" else _normalise_path(path)
            continue

        if raw.startswith("+++ "):
            # current_file already set from "---"
            continue

        if raw.startswith(("index ", "new file", "deleted file", "old mode",
                            "new mode", "rename ", "similarity ", "Binary ")):
            continue

        if raw.startswith("@@"):
            hm = _HUNK_HEADER_RE.match(raw)
            if hm:
                base_line = int(hm.group(1)) - 1  # first body line wil increment it
            continue

        if not raw:
            base_line += 1
            continue

        change_char = raw[0]
        if change_char not in (" ", "+", "-"):
            continue

        if change_char in (" ", "-"):
            base_line += 1

        if change_char == "-" and current_file:
            result.setdefault(current_file, set()).add(base_line)

    return result


# ---------------------------------------------------------------------------
# Repo prefix helpers — link workspace_ids back to their repo
# ---------------------------------------------------------------------------

def _repo_prefix_from_wid(workspace_id: str) -> str:
    """Strip trailing issue number from a workspace_id to get the repo prefix.

    e.g. 'pytest_dev__pytest_8399'  -> 'pytest_dev__pytest'
         'pallets__flask_4045'      -> 'pallets__flask'
    """
    return re.sub(r'_\d+$', '', workspace_id)


def _repo_prefix_from_slug(repo_slug: str) -> str:
    """Convert a SWE-bench ``repo`` field to the same prefix format.

    e.g. 'pytest-dev/pytest' -> 'pytest_dev__pytest'
         'pallets/flask'     -> 'pallets__flask'
    """
    with_double_underscore = repo_slug.replace('/', '__')
    return ''.join(c if c.isalnum() else '_' for c in with_double_underscore)


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def _def_key(path: str, kind: str, name: str, start_line: int) -> str:
    """path:kind:name:start_line -- matches raw_signals_pipeline and collect_signals."""
    return f"{path}:{kind}:{name}:{start_line}"


def _find_gt_from_index(
    ctx: "AppContext",
    changed_lines: dict[str, set[int]],
) -> list[dict[str, Any]]:
    """Find indexed DefFacts whose span overlaps the changed line numbers.

    Returns list of candidate-schema dicts (same fields as raw_signals_pipeline
    produces) for GT injection.
    """
    from coderecon.index._internal.indexing.graph import FactQueries

    gt: list[dict[str, Any]] = []
    with ctx.coordinator.db.session() as session:
        fq = FactQueries(session)
        for rel_path, lines in changed_lines.items():
            file_rec = fq.get_file_by_path(rel_path)
            if file_rec is None or file_rec.id is None:
                continue
            for d in fq.list_defs_in_file(file_rec.id):
                end = d.end_line or d.start_line
                if any(d.start_line <= ln <= end for ln in lines):
                    gt.append({
                        "path":           rel_path,
                        "kind":           d.kind,
                        "name":           d.name,
                        "start_line":     d.start_line,
                        "end_line":       end,
                        "signature_text": d.signature_text or "",
                        "namespace":      d.namespace or "",
                        "is_gt_edited":   True,
                        "is_gt_read":     False,
                    })
    return gt


# ---------------------------------------------------------------------------
# Per-repo context loading
# ---------------------------------------------------------------------------

def _load_context(clone_dir: Path) -> tuple["AppContext", asyncio.AbstractEventLoop]:
    """Load AppContext + event loop for a cloned indexed repo."""
    from coderecon.mcp.context import AppContext

    cp = clone_dir / ".recon"
    logging.disable(logging.INFO)
    ctx = AppContext.standalone(
        repo_root=clone_dir,
        db_path=cp / "index.db",
        tantivy_path=cp / "tantivy",
    )
    logging.disable(logging.NOTSET)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ctx.coordinator.load_existing())
    return ctx, loop


# ---------------------------------------------------------------------------
# Per-instance record builder
# ---------------------------------------------------------------------------

def _build_record(
    ctx: "AppContext",
    loop: asyncio.AbstractEventLoop,
    instance_id: str,
    problem_statement: str,
    patch: str,
    *,
    max_candidates: int,
) -> dict[str, Any] | None:
    """Build one fixture record.  Returns None if no GT defs found in index."""
    from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline

    # 1. Identify GT defs via index-accurate span overlap
    changed = _changed_lines_per_file(patch)
    if not changed:
        return None

    gt_defs = _find_gt_from_index(ctx, changed)
    if not gt_defs:
        return None

    gt_keys = {_def_key(d["path"], d["kind"], d["name"], d["start_line"]) for d in gt_defs}

    # 2. Retrieve candidate pool from raw_signals_pipeline
    try:
        raw = loop.run_until_complete(
            raw_signals_pipeline(ctx, problem_statement[:2000])
        )
        retrieved = raw.get("candidates", [])
    except Exception as exc:
        logger.warning("%s: raw_signals_pipeline failed (%s) — GT-only pool", instance_id, exc)
        retrieved = []

    # 3. Build merged pool: top-N retrieved + any GT not already in it
    seen_keys: set[str] = set()
    pool: list[dict[str, Any]] = []

    for c in retrieved[:max_candidates]:
        dk = _def_key(c["path"], c["kind"], c["name"], c["start_line"])
        if dk not in seen_keys:
            seen_keys.add(dk)
            pool.append({
                "def_key":        dk,
                "path":           c["path"],
                "kind":           c["kind"],
                "name":           c["name"],
                "start_line":     c["start_line"],
                "end_line":       c.get("end_line") or c["start_line"],
                "signature_text": c.get("signature_text") or "",
                "namespace":      c.get("namespace") or "",
                "baseline_rank":  idx,
                "is_gt_edited":   dk in gt_keys,
                "is_gt_read":     False,
            })

    # Inject GT defs not surfaced by retrieval
    for d in gt_defs:
        dk = _def_key(d["path"], d["kind"], d["name"], d["start_line"])
        if dk not in seen_keys:
            seen_keys.add(dk)
            pool.append({
                "def_key":        dk,
                "path":           d["path"],
                "kind":           d["kind"],
                "name":           d["name"],
                "start_line":     d["start_line"],
                "end_line":       d["end_line"],
                "signature_text": d["signature_text"],
                "namespace":      d["namespace"],
                "baseline_rank":  len(retrieved) + len(pool),
                "is_gt_edited":   True,
                "is_gt_read":     False,
            })

    if not pool:
        return None

    return {
        "task_id":           instance_id,
        "repo_id":           instance_id.rsplit("-", 1)[0] if "-" in instance_id else instance_id,
        "problem_statement": problem_statement,
        "query_type":        "TASK",
        "candidates":        pool,
        "gt_edited_keys":    list(gt_keys),
        "gt_read_keys":      [],
    }


# ---------------------------------------------------------------------------
# SWE-bench loading
# ---------------------------------------------------------------------------

# SWE-bench_Verified is a strict subset of SWE-bench test split — loading both
# deduplicates all Verified rows away.  Use base SWE-bench as the default.
_DEFAULT_DATASETS: list[tuple[str, str]] = [
    ("princeton-nlp/SWE-bench", "test"),
]

_VERIFIED_ONLY_DATASETS: list[tuple[str, str]] = [
    ("princeton-nlp/SWE-bench_Verified", "test"),
]


def _workspace_id_from_instance_id(instance_id: str) -> str:
    """Reproduce swebench_common.workspace_id() without importing it."""
    return "".join(c if c.isalnum() else "_" for c in instance_id)


def _load_swebench(
    datasets: list[tuple[str, str]],
    repo_filter: str | None,
) -> dict[str, list[tuple[str, str, str]]]:
    """Load SWE-bench instances grouped by repo_prefix.

    Returns {repo_prefix: [(instance_id, problem_statement, patch)]}.
    repo_prefix matches the prefix of workspace_id dirs, e.g. 'pallets__flask'.
    """
    from datasets import load_dataset

    result: dict[str, list[tuple[str, str, str]]] = {}
    seen: set[str] = set()

    for ds_name, split in datasets:
        print(f"Loading {ds_name} ({split})...", flush=True)
        try:
            ds = load_dataset(ds_name, split=split)
        except Exception as exc:
            logger.warning("Failed to load %s:%s -- %s", ds_name, split, exc)
            continue

        for row in ds:
            iid = str(row["instance_id"])
            if iid in seen:
                continue
            seen.add(iid)

            repo_slug = str(row.get("repo", ""))
            if repo_filter and repo_filter.lower() not in repo_slug.lower():
                continue

            ps = str(row.get("problem_statement", ""))
            patch = str(row.get("patch", ""))
            if not ps or not patch:
                continue

            prefix = _repo_prefix_from_slug(repo_slug)
            result.setdefault(prefix, []).append((iid, ps, patch))

    total = sum(len(v) for v in result.values())
    print(f"  {total} instances across {len(result)} repos", flush=True)
    return result


# ---------------------------------------------------------------------------
# Main fixture builder
# ---------------------------------------------------------------------------

def build_fixture(
    out_file: Path,
    *,
    instances_dir: Path,
    datasets: list[tuple[str, str]] | None = None,
    max_tasks: int = 0,
    max_candidates: int = 30,
    min_gt_edited: int = 1,
    repo_filter: str | None = None,
) -> int:
    """Build the scaffold rerank fixture JSONL.

    Architecture — one index per repo (not per instance):
        SWE-bench instances are git worktrees that share the same underlying
        git object store.  Running ``recon init`` on ONE worktree per repo
        produces an index that is valid for all instances of that repo
        (raw_signals_pipeline queries the tantivy
        index, neither of which require reading live files from disk).

        The first worktree with a ``.recon/index.db`` for each repo is chosen
        as the "anchor".  Its AppContext is reused for every SWE-bench instance
        of the same repo.

    Args:
        out_file:        Destination JSONL path.
        instances_dir:   Directory of per-instance SWE-bench worktree checkouts
                         (``~/.recon/recon-lab/clones/instances/`` by default).
        datasets:        SWE-bench datasets to load (default: SWE-bench test).
        max_tasks:       Total records cap (0 = no cap).
        max_candidates:  Max retrieved candidates per task (GT always injected).
        min_gt_edited:   Skip tasks with fewer GT-edited defs.
        repo_filter:     Case-insensitive substring filter on ``repo`` field.

    Returns:
        Number of records written.
    """
    if datasets is None:
        datasets = _DEFAULT_DATASETS

    if not instances_dir.is_dir():
        raise FileNotFoundError(
            f"Instances directory not found: {instances_dir}\n"
            "Run: recon-lab swebench import (to check out instances)"
        )

    # Find one indexed anchor worktree per repo (first found wins)
    anchors: dict[str, Path] = {}  # repo_prefix -> anchor worktree dir
    for d in sorted(instances_dir.iterdir()):
        if d.is_dir() and (d / ".recon" / "index.db").exists():
            prefix = _repo_prefix_from_wid(d.name)
            if prefix not in anchors:
                anchors[prefix] = d

    if not anchors:
        raise FileNotFoundError(
            f"No indexed worktrees found in {instances_dir}.\n"
            "Run: python -m coderecon.cli.main init <instances_dir>/<workspace_id>"
        )
    print(
        f"Found {len(anchors)} indexed repos "
        f"(anchor worktrees: {', '.join(sorted(anchors))})",
        flush=True,
    )

    # Load SWE-bench instances grouped by repo_prefix
    swebench_by_repo = _load_swebench(datasets, repo_filter)

    matched_repos = set(anchors) & set(swebench_by_repo)
    unindexed_repos = set(swebench_by_repo) - set(anchors)
    print(f"  {len(matched_repos)} repos have both an anchor index and SWE-bench instances", flush=True)
    if unindexed_repos:
        print(
            f"  {len(unindexed_repos)} repos skipped (no anchor index): "
            + ", ".join(sorted(unindexed_repos)[:5])
            + (" ..." if len(unindexed_repos) > 5 else ""),
            flush=True,
        )

    records: list[dict[str, Any]] = []
    n_no_gt = 0
    n_errors = 0

    for repo_prefix in sorted(matched_repos):
        anchor = anchors[repo_prefix]
        instances = swebench_by_repo[repo_prefix]
        print(f"  {repo_prefix}: {len(instances)} instances (anchor: {anchor.name})", flush=True)

        try:
            ctx, loop = _load_context(anchor)
        except Exception as exc:
            logger.warning("%s: failed to load context from %s: %s", repo_prefix, anchor, exc)
            n_errors += len(instances)
            continue

        try:
            for iid, problem_statement, patch in instances:
                if max_tasks > 0 and len(records) >= max_tasks:
                    break

                try:
                    rec = _build_record(
                        ctx, loop, iid, problem_statement, patch,
                        max_candidates=max_candidates,
                    )
                except Exception as exc:
                    logger.warning("%s: build_record failed: %s", iid, exc)
                    n_errors += 1
                    continue

                if rec is None:
                    n_no_gt += 1
                    continue

                if len(rec["gt_edited_keys"]) < min_gt_edited:
                    n_no_gt += 1
                    continue

                records.append(rec)

        finally:
            try:
                ctx.coordinator.close()
            except Exception:
                pass
            loop.close()

        if max_tasks > 0 and len(records) >= max_tasks:
            break

    logger.info(
        "Built %d records | no-gt: %d  errors: %d",
        len(records), n_no_gt, n_errors,
    )
    print(
        f"Done: {len(records)} records "
        f"(no-gt: {n_no_gt}, errors: {n_errors})",
        flush=True,
    )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    return len(records)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build scaffold reranking fixture from SWE-bench + coderecon index. "
            "Repos must be cloned and indexed first."
        )
    )
    parser.add_argument(
        "--out",
        default="~/.recon/recon-lab/data/scaffold_rerank_data.jsonl",
        help="Output JSONL path",
    )
    parser.add_argument(
        "--instances-dir",
        default="~/.recon/recon-lab/clones/instances",
        help="Directory of per-instance SWE-bench checkouts (default: ~/.recon/recon-lab/clones/instances)",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="Max task records to write (0 = all)",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=30,
        help="Max retrieved candidates per task from raw_signals_pipeline (GT always injected)",
    )
    parser.add_argument(
        "--verified-only",
        action="store_true",
        help="Use only SWE-bench_Verified for problem statements (~500 instances)",
    )
    parser.add_argument(
        "--repo-filter",
        default=None,
        help="Case-insensitive repo substring filter (e.g. astropy)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    dsets = _VERIFIED_ONLY_DATASETS if args.verified_only else _DEFAULT_DATASETS
    n = build_fixture(
        Path(args.out).expanduser(),
        instances_dir=Path(args.instances_dir).expanduser(),
        datasets=dsets,
        max_tasks=args.max_tasks,
        max_candidates=args.max_candidates,
        repo_filter=args.repo_filter,
    )
    print(f"Wrote {n} records -> {args.out}")


if __name__ == "__main__":
    main()
