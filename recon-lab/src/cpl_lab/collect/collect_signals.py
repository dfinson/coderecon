"""Signal collection — batch in-process, multiprocessing with progress.

Each worker loads one repo's index, runs all GT queries through
``raw_signals_pipeline``, streams per-query progress via a shared queue.

Output: ``data/{repo_id}/signals/candidates_rank.parquet``
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


# Explicit schema so every batch has identical types regardless of nulls.
_SIGNALS_SCHEMA = pa.schema([
    # Query identity
    pa.field("task_id", pa.string()),
    pa.field("query_id", pa.string()),
    pa.field("query_type", pa.string()),
    pa.field("candidate_key", pa.string()),
    # Candidate identity
    pa.field("path", pa.string()),
    pa.field("kind", pa.string()),
    pa.field("name", pa.string()),
    pa.field("lexical_path", pa.string()),
    pa.field("qualified_name", pa.string()),
    # Span
    pa.field("start_line", pa.int64()),
    pa.field("end_line", pa.int64()),
    pa.field("object_size_lines", pa.int64()),
    # Path features
    pa.field("file_ext", pa.string()),
    pa.field("parent_dir", pa.string()),
    pa.field("path_depth", pa.int64()),
    # Structural metadata
    pa.field("has_docstring", pa.bool_()),
    pa.field("has_decorators", pa.bool_()),
    pa.field("has_return_type", pa.bool_()),
    pa.field("signature_text", pa.string()),
    pa.field("namespace", pa.string()),
    pa.field("nesting_depth", pa.int64()),
    pa.field("has_parent_scope", pa.bool_()),
    pa.field("hub_score", pa.float64()),
    pa.field("is_test", pa.bool_()),
    pa.field("is_barrel", pa.bool_()),
    pa.field("is_endpoint", pa.bool_()),
    pa.field("test_coverage_count", pa.int64()),
    pa.field("artifact_kind", pa.string()),
    # Term match signal
    pa.field("term_match_count", pa.int64()),
    pa.field("term_total_matches", pa.int64()),
    pa.field("lex_hit_count", pa.int64()),
    pa.field("bm25_file_score", pa.float64()),
    # Graph signal
    pa.field("graph_edge_type", pa.string()),
    pa.field("graph_seed_rank", pa.int64()),
    pa.field("graph_caller_max_tier", pa.string()),
    # Symbol signal
    pa.field("symbol_source", pa.string()),
    # Import signal
    pa.field("import_direction", pa.string()),
    # Structural link signals
    pa.field("shares_file_with_seed", pa.bool_()),
    pa.field("is_callee_of_top", pa.bool_()),
    pa.field("is_imported_by_top", pa.bool_()),
    # SPLADE sparse retrieval score
    pa.field("splade_score", pa.float64()),
    # Cross-encoder score (TinyBERT, all candidates)
    pa.field("ce_score_tiny", pa.float64()),
    # Language family
    pa.field("language_family", pa.string()),
    # Coverage expansion
    pa.field("from_coverage", pa.bool_()),
    # Harvester source flags
    pa.field("from_term_match", pa.bool_()),
    pa.field("from_explicit", pa.bool_()),
    pa.field("from_graph", pa.bool_()),
    pa.field("matched_terms_count", pa.int64()),
    # Agreement
    pa.field("retriever_hits", pa.int64()),
    # RRF score
    pa.field("rrf_score", pa.float64()),
    # Locality
    pa.field("seed_path_distance", pa.int64()),
    pa.field("same_package", pa.bool_()),
    pa.field("package_distance", pa.int64()),
    # Query features
    pa.field("query_len", pa.int64()),
    pa.field("has_identifier", pa.bool_()),
    pa.field("has_path", pa.bool_()),
    pa.field("identifier_density", pa.float64()),
    pa.field("has_numbers", pa.bool_()),
    pa.field("has_quoted_strings", pa.bool_()),
    pa.field("term_count", pa.int64()),
    # Task intent signals
    pa.field("intent", pa.string()),
    pa.field("is_stacktrace_driven", pa.bool_()),
    pa.field("is_test_driven", pa.bool_()),
    # Label
    pa.field("label_relevant", pa.int64()),
])


# ---------------------------------------------------------------------------
# Ground-truth parsing
# ---------------------------------------------------------------------------

def _parse_legacy_gt(gt_file: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Parse legacy ground_truth.jsonl -> (flat_queries, relevance_map)."""
    flat: list[dict[str, Any]] = []
    rel: dict[str, dict[str, int]] = {}
    for ln in gt_file.read_text().splitlines():
        if not ln.strip():
            continue
        task = json.loads(ln)
        tid = task.get("task_id")
        if not tid:
            continue
        tr: dict[str, int] = {}
        for d in task.get("minimum_sufficient_defs", []):
            k = f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"
            tr[k] = 1
        rel[tid] = tr
        for qi, q in enumerate(task.get("queries", [])):
            flat.append({
                "task_id": tid,
                "query_id": f"{tid}/Q{qi}",
                "query_text": q["query_text"],
                "query_type": q.get("query_type", ""),
                "seeds": q.get("seeds", []),
                "pins": q.get("pins", []),
            })
    return flat, rel


def _parse_gt_tables(
    repo_id: str,
    gt_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Parse post-processed GT tables -> (flat_queries, relevance_map)."""
    queries_file = gt_dir / "queries.jsonl"
    touched_file = gt_dir / "touched_objects.jsonl"

    flat: list[dict[str, Any]] = []
    rel: dict[str, dict[str, int]] = {}

    for line in touched_file.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        run_id = row.get("run_id", "")
        if run_id == f"{repo_id}__non_ok":
            continue
        task_id = run_id.removeprefix(f"{repo_id}_")
        if not task_id:
            continue
        rel.setdefault(task_id, {})[row["candidate_key"]] = 1

    for line in queries_file.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        run_id = row.get("run_id", "")
        if run_id == f"{repo_id}__non_ok":
            task_id = "__non_ok"
        else:
            task_id = run_id.removeprefix(f"{repo_id}_")
        if not task_id:
            continue
        flat.append({
            "task_id": task_id,
            "query_id": row["query_id"],
            "query_text": row["query_text"],
            "query_type": row.get("query_type", ""),
            "seeds": row.get("seeds", []),
            "pins": row.get("pins", []),
        })

    return flat, rel


def _parse_raw_task_jsons(gt_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Parse raw per-task JSON files directly when JSONL tables are absent."""
    from cpl_lab.data_manifest import iter_task_json_files

    flat: list[dict[str, Any]] = []
    rel: dict[str, dict[str, int]] = {}

    for task_file in iter_task_json_files(gt_dir):
        task = json.loads(task_file.read_text())
        tid = task.get("task_id")
        if not tid:
            continue

        rel[tid] = {}
        for d in task.get("minimum_sufficient_defs", []):
            key = f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"
            rel[tid][key] = 1

        for qi, q in enumerate(task.get("queries", [])):
            flat.append({
                "task_id": tid,
                "query_id": f"{tid}/Q{qi}",
                "query_text": q["query_text"],
                "query_type": q.get("query_type", ""),
                "seeds": q.get("seeds", []),
                "pins": q.get("pins", []),
            })

    return flat, rel


# Message types for the progress queue
# ("progress", repo_id, queries_done, queries_total)
# ("done", repo_id, summary_dict)

def _worker(args: tuple[str, str, str, str, str], queue: mp.Queue) -> None:  # type: ignore[type-arg]
    """Collect signals for one repo, posting progress to *queue*."""
    repo_id = args[0]
    try:
        _worker_inner(args, queue)
    except Exception as exc:
        queue.put(("done", repo_id, {
            "repo_id": repo_id, "status": "error", "queries_processed": 0,
            "total_candidates": 0, "error": str(exc), "elapsed_sec": 0,
        }))


def _worker_inner(args: tuple[str, str, str, str, str], queue: mp.Queue) -> None:  # type: ignore[type-arg]
    repo_id, data_dir_s, main_clone_dir_s, instance_clone_dir_s, extra_path = args
    data_dir = Path(data_dir_s)
    main_clone_dir = Path(main_clone_dir_s)
    instance_clone_dir = Path(instance_clone_dir_s)

    for p in extra_path.split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    # Silence all logging so it doesn't corrupt the TUI
    logging.disable(logging.CRITICAL)
    try:
        import structlog
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        )
    except Exception:
        pass
    # Redirect stderr to a per-worker log file for post-mortem debugging
    err_log = data_dir / "signals" / "worker_stderr.log"
    err_log.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr = open(err_log, "w")

    gt_dir = data_dir / "ground_truth"
    queries_file = gt_dir / "queries.jsonl"
    touched_file = gt_dir / "touched_objects.jsonl"
    gt_file = data_dir / "ground_truth.jsonl"

    if queries_file.exists() and touched_file.exists():
        queries, rel_map = _parse_gt_tables(repo_id, gt_dir)
    elif gt_file.exists():
        queries, rel_map = _parse_legacy_gt(gt_file)
    else:
        queries, rel_map = _parse_raw_task_jsons(gt_dir)

    if not queries:
        queue.put(("done", repo_id, {
            "repo_id": repo_id, "status": "skip", "queries_processed": 0,
            "total_candidates": 0, "error": "no queries", "elapsed_sec": 0,
        }))
        return

    # Post initial progress
    queue.put(("progress", repo_id, 0, len(queries)))

    from coderecon.sdk.dev import CodeReconDev

    worktree_name = instance_clone_dir.name if instance_clone_dir != main_clone_dir else None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run_collection() -> None:
        nonlocal n_cand, n_q, n_err
        from cpl_lab.config import recon_binary

        async with CodeReconDev(binary=recon_binary()) as sdk:
            reg = await sdk.register(str(main_clone_dir))
            repo_name = reg.repo

            sig_dir = data_dir / "signals"
            sig_dir.mkdir(parents=True, exist_ok=True)
            out_path = sig_dir / "candidates_rank.parquet"
            tmp_path = out_path.with_suffix(".tmp.parquet")

            writer: pq.ParquetWriter | None = None

            for q in queries:
                tr = rel_map.get(q["task_id"], {})
                try:
                    res = await sdk.raw_signals(
                        repo_name,
                        q["query_text"],
                        seeds=q["seeds"] or None,
                        pins=q["pins"] or None,
                        worktree=worktree_name,
                    )
                except Exception:
                    n_err += 1
                    n_q += 1
                    if n_q % 5 == 0 or n_q == len(queries):
                        queue.put(("progress", repo_id, n_q, len(queries)))
                    continue

                rows: list[dict] = []
                qf = res.query_features
                for c in res.candidates:
                    cand_key = f"{c.get('path', '')}:{c.get('kind', '')}:{c.get('name', '')}:{c.get('start_line', 0)}"
                    grade = tr.get(cand_key, 0)
                    row = {
                        "task_id": q["task_id"], "query_id": q["query_id"],
                        "query_type": q["query_type"],
                        "candidate_key": cand_key, **c,
                        "query_len": qf.get("query_len", 0),
                        "has_identifier": qf.get("has_identifier", False),
                        "has_path": qf.get("has_path", False),
                        "identifier_density": qf.get("identifier_density", 0.0),
                        "has_numbers": qf.get("has_numbers", False),
                        "has_quoted_strings": qf.get("has_quoted_strings", False),
                        "term_count": qf.get("term_count", 0),
                        "intent": qf.get("intent", ""),
                        "is_stacktrace_driven": qf.get("is_stacktrace_driven", False),
                        "is_test_driven": qf.get("is_test_driven", False),
                        "label_relevant": grade,
                    }
                    for field in _SIGNALS_SCHEMA:
                        if field.name not in row:
                            if pa.types.is_floating(field.type):
                                row[field.name] = 0.0
                            elif pa.types.is_integer(field.type):
                                row[field.name] = 0
                            elif pa.types.is_boolean(field.type):
                                row[field.name] = False
                            elif pa.types.is_string(field.type):
                                row[field.name] = ""
                    rows.append(row)

                if rows:
                    batch_table = pa.Table.from_pandas(
                        pd.DataFrame(rows), schema=_SIGNALS_SCHEMA, preserve_index=False,
                    )
                    if writer is None:
                        writer = pq.ParquetWriter(tmp_path, _SIGNALS_SCHEMA)
                    writer.write_table(batch_table)
                    n_cand += len(rows)
                    del batch_table, rows

                n_q += 1
                if n_q % 5 == 0 or n_q == len(queries):
                    queue.put(("progress", repo_id, n_q, len(queries)))

            if writer is not None:
                writer.close()
                os.replace(tmp_path, out_path)

    sig_dir = data_dir / "signals"
    n_cand = n_q = n_err = 0
    t0 = time.monotonic()

    try:
        loop.run_until_complete(_run_collection())
    except Exception as e:
        queue.put(("done", repo_id, {
            "repo_id": repo_id, "status": "error", "queries_processed": n_q,
            "total_candidates": n_cand, "error": str(e), "elapsed_sec": round(time.monotonic() - t0, 1),
        }))
        return
    finally:
        loop.close()

    elapsed = round(time.monotonic() - t0, 1)
    summary = {"repo_id": repo_id, "status": "ok", "queries_processed": n_q,
               "total_candidates": n_cand, "errors": n_err, "elapsed_sec": elapsed}
    sig_dir.mkdir(parents=True, exist_ok=True)
    (sig_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    queue.put(("done", repo_id, summary))


# ---------------------------------------------------------------------------
# Parallel orchestrator with progress queue
# ---------------------------------------------------------------------------

def collect_all(
    repo_jobs: list[tuple[str, Path, Path, Path]],
    workers: int = 4,
    on_progress: Any = None,   # (repo_id, done, total)
    on_complete: Any = None,   # (summary_dict)
) -> list[dict[str, Any]]:
    """Run collection with worker processes, streaming progress."""
    extra = os.pathsep.join(sys.path)
    tasks = [(rid, str(dd), str(mcd), str(icd), extra) for rid, dd, mcd, icd in repo_jobs]

    spawn = mp.get_context("spawn")
    queue: mp.Queue = spawn.Queue()  # type: ignore[type-arg]
    results: list[dict[str, Any]] = []
    pending = 0
    idx = 0

    # Launch up to `workers` processes at a time
    active: dict[str, mp.Process] = {}

    def _launch_next() -> None:
        nonlocal idx, pending
        while idx < len(tasks) and len(active) < workers:
            t = tasks[idx]
            rid = t[0]
            p = spawn.Process(target=_worker, args=(t, queue))
            p.start()
            active[rid] = p
            pending += 1
            idx += 1

    _launch_next()

    while pending > 0:
        try:
            msg = queue.get(timeout=1.0)
        except Exception:
            # Reap dead workers
            for rid in list(active):
                if not active[rid].is_alive():
                    active[rid].join()
                    if rid not in [r["repo_id"] for r in results]:
                        s = {"repo_id": rid, "status": "error", "queries_processed": 0,
                             "total_candidates": 0, "error": "worker died", "elapsed_sec": 0}
                        results.append(s)
                        if on_complete:
                            on_complete(s)
                        pending -= 1
                    del active[rid]
                    _launch_next()
            continue

        msg_type = msg[0]
        if msg_type == "progress":
            _, rid, done, total = msg
            if on_progress:
                on_progress(rid, done, total)
        elif msg_type == "done":
            _, rid, summary = msg
            results.append(summary)
            if on_complete:
                on_complete(summary)
            pending -= 1
            if rid in active:
                active[rid].join(timeout=5)
                del active[rid]
            _launch_next()

    # Cleanup stragglers
    for p in active.values():
        p.join(timeout=5)

    return results
