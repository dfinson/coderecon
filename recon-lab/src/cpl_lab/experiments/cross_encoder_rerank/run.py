"""Cross-encoder reranking bakeoff — compare rerankers on code scaffold retrieval.

Compares three MS MARCO cross-encoder models on the same scaffolds and queries:
  - cross-encoder/ms-marco-TinyBERT-L-2-v2   (4.4M, speed anchor)
  - cross-encoder/ms-marco-MiniLM-L-6-v2     (22.7M, value anchor)
  - cross-encoder/ms-marco-MiniLM-L-12-v2    (33.4M, quality anchor)

Protocol:
  1. Load indexed repos (needs existing .recon/index.db)
  2. Build per-def scaffolds from DB facts (reuses splade_bakeoff.scaffold)
  3. Simulate first-stage retrieval via BM25/term-match candidate ranking
  4. Cross-encoder rerank top-K candidates per query
  5. Measure def Recall@10/20/50, NDCG@10/20, latency, throughput
  6. Compare representations: scaffold-only vs scaffold+body
  7. Write results to JSON + parquet

Inputs:
  - Indexed repos with .recon/index.db (from clone + index-main stages)
  - PR ground truth from pr-import stage (data/{repo_id}/ground_truth/)

Outputs:
  - ${workspace}/experiments/cross_encoder_rerank/metrics.json
  - ${workspace}/experiments/cross_encoder_rerank/per_query.parquet
  - ${workspace}/experiments/cross_encoder_rerank/latency_profile.json
"""

from __future__ import annotations

import json
import logging
import resource
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cpl_lab.experiments.cross_encoder_rerank.encoder import (
    MODELS,
    CrossEncoderModel,
    RerankResult,
    ndcg_at_k,
    rerank_indices,
)
from cpl_lab.experiments.splade_bakeoff.scaffold import (
    build_def_scaffold,
    build_file_header_scaffold,
    word_split,
)

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────


@dataclass
class DefRecord:
    """A def from the index with its scaffold and optional raw body."""

    def_uid: str
    file_path: str
    kind: str
    name: str
    start_line: int
    end_line: int
    scaffold: str
    raw_body: str = ""


@dataclass
class QueryRecord:
    """A query with ground truth def keys and file paths."""

    query_id: str
    task_id: str
    query_text: str
    query_type: str
    gt_def_keys: set[str]   # path:kind:name stable keys
    gt_file_paths: set[str]


@dataclass
class RepoData:
    """Extracted data for one logical repo."""

    repo_id: str
    defs: list[DefRecord] = field(default_factory=list)
    queries: list[QueryRecord] = field(default_factory=list)


# ── Scaffold + body extraction from index DB ─────────────────────


def _extract_repo_defs(
    repo_id: str,
    main_clone_dir: Path,
    include_body: bool = False,
) -> list[DefRecord]:
    """Extract per-def scaffolds (and optionally raw bodies) from index.db."""

    recon_dir = main_clone_dir / ".recon"
    if not (recon_dir / "index.db").exists():
        logger.warning("No index.db for %s, skipping", repo_id)
        return []

    from coderecon.index._internal.db.database import Database
    from coderecon.index._internal.indexing.graph import FactQueries
    from coderecon.index.models import DefFact, ImportFact, RefFact
    from sqlmodel import Session, select

    db = Database(recon_dir / "index.db")
    engine = db.engine

    defs: list[DefRecord] = []

    with Session(engine) as session:
        fq = FactQueries(session)

        files = fq.list_files(limit=50_000)
        file_map = {f.id: f for f in files}

        all_defs = session.exec(select(DefFact)).all()

        defs_by_file: dict[int, list[Any]] = {}
        for d in all_defs:
            defs_by_file.setdefault(d.file_id, []).append(d)

        for file_id, file_obj in file_map.items():
            file_path = file_obj.path
            file_defs = defs_by_file.get(file_id, [])

            for d in file_defs:
                callee_defs = fq.list_callees_in_scope(
                    file_id, d.start_line, d.end_line, limit=50,
                )
                callee_names = [cd.name for cd in callee_defs]

                refs = session.exec(
                    select(RefFact).where(
                        RefFact.file_id == file_id,
                        RefFact.start_line >= d.start_line,
                        RefFact.start_line <= d.end_line,
                        RefFact.ref_tier.in_(["PROVEN", "STRONG"]),  # type: ignore[union-attr]
                    ).limit(50)
                ).all()
                type_ref_names = sorted({
                    " ".join(word_split(r.token_text))
                    for r in refs
                    if r.token_text and len(r.token_text) >= 2
                })

                scaffold = build_def_scaffold(
                    file_path,
                    kind=d.kind,
                    name=d.name,
                    signature_text=d.signature_text,
                    qualified_name=d.qualified_name,
                    lexical_path=d.lexical_path,
                    docstring=d.docstring,
                    callee_names=callee_names,
                    type_ref_names=type_ref_names,
                )
                if not scaffold:
                    continue

                raw_body = ""
                if include_body and hasattr(d, "body_text") and d.body_text:
                    # Trim body to ~512 chars to stay within cross-encoder window
                    raw_body = d.body_text[:512]

                defs.append(DefRecord(
                    def_uid=d.def_uid,
                    file_path=file_path,
                    kind=d.kind,
                    name=d.name,
                    start_line=d.start_line,
                    end_line=d.end_line,
                    scaffold=scaffold,
                    raw_body=raw_body,
                ))

    return defs


# ── Query + ground truth loading ─────────────────────────────────


def _load_queries(repo_id: str, data_dir: Path) -> list[QueryRecord]:
    """Load PR-derived queries and ground truth for a repo instance."""
    repo_data_dir = data_dir / repo_id
    gt_dir = repo_data_dir / "ground_truth"

    if not gt_dir.is_dir():
        return []

    # 1. Load touched objects (ground truth defs)
    touched_file = gt_dir / "touched_objects.jsonl"
    gt_by_task: dict[str, set[str]] = {}
    gt_files_by_task: dict[str, set[str]] = {}

    if touched_file.exists():
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
            ckey = row.get("candidate_key", "")
            path = row.get("path", "")
            if ckey:
                parts = ckey.rsplit(":", 1)
                stable_key = parts[0] if len(parts) == 2 and parts[1].isdigit() else ckey
                gt_by_task.setdefault(task_id, set()).add(stable_key)
            if path:
                gt_files_by_task.setdefault(task_id, set()).add(path)

    # 2. Load queries
    queries_file = gt_dir / "queries.jsonl"
    records: list[QueryRecord] = []

    if queries_file.exists():
        for line in queries_file.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            run_id = row.get("run_id", "")
            if run_id == f"{repo_id}__non_ok":
                continue
            task_id = run_id.removeprefix(f"{repo_id}_")
            label_gate = row.get("label_gate", "OK")
            if label_gate != "OK":
                continue
            gt_defs = gt_by_task.get(task_id, set())
            if not gt_defs:
                continue
            records.append(QueryRecord(
                query_id=row.get("query_id", ""),
                task_id=task_id,
                query_text=row.get("query_text", ""),
                query_type=row.get("query_type", ""),
                gt_def_keys=gt_defs,
                gt_file_paths=gt_files_by_task.get(task_id, set()),
            ))

    return records


# ── First-stage simulation ───────────────────────────────────────


def _simulate_first_stage(
    query: QueryRecord,
    all_defs: list[DefRecord],
    top_k_files: int = 20,
) -> list[int]:
    """Simulate first-stage retrieval: return def indices for candidate pool.

    Uses simple term-overlap scoring as a proxy for BM25/SPLADE first stage.
    The point is not to replicate the real first stage perfectly — it is to
    produce a realistic candidate pool that the cross-encoder must rerank.

    Returns indices into *all_defs* for the candidate pool.
    """
    query_terms = set(query.query_text.lower().split())

    # Score files by term overlap with query
    file_scores: dict[str, float] = {}
    for i, rec in enumerate(all_defs):
        scaffold_terms = set(rec.scaffold.lower().split())
        overlap = len(query_terms & scaffold_terms)
        path = rec.file_path
        file_scores[path] = max(file_scores.get(path, 0.0), overlap)

    # Take top-K files
    ranked_files = sorted(file_scores.items(), key=lambda x: x[1], reverse=True)
    top_files = {path for path, _ in ranked_files[:top_k_files]}

    # Return def indices within those files, scored by term overlap
    candidates: list[tuple[int, float]] = []
    for i, rec in enumerate(all_defs):
        if rec.file_path in top_files:
            scaffold_terms = set(rec.scaffold.lower().split())
            score = len(query_terms & scaffold_terms)
            candidates.append((i, score))

    # Sort by first-stage score descending
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in candidates]


# ── Per-model evaluation ─────────────────────────────────────────


def _build_rerank_text(rec: DefRecord, use_body: bool) -> str:
    """Build the document text fed to the cross-encoder."""
    if use_body and rec.raw_body:
        return rec.scaffold + "\n\n" + rec.raw_body
    return rec.scaffold


def _evaluate_model(
    model_key: str,
    encoder: CrossEncoderModel,
    all_defs: list[DefRecord],
    queries: list[QueryRecord],
    top_k_rerank: int = 50,
    use_body: bool = False,
) -> dict[str, Any]:
    """Run full reranking evaluation for one model. Returns metrics dict."""

    logger.info(
        "Evaluating model: %s (%s), top_k=%d, use_body=%s",
        model_key, encoder.model_name, top_k_rerank, use_body,
    )

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # Precompute all candidate pools (first-stage simulation)
    # Scale file pool with rerank K so larger K gets broader coverage
    top_k_files = max(20, top_k_rerank // 3)
    logger.info(
        "Simulating first-stage retrieval for %d queries (top_k_files=%d)...",
        len(queries), top_k_files,
    )
    candidate_pools: list[list[int]] = []
    for q in queries:
        pool = _simulate_first_stage(q, all_defs, top_k_files=top_k_files)
        candidate_pools.append(pool)

    # Score all queries
    recall_at_10: list[float] = []
    recall_at_20: list[float] = []
    recall_at_50: list[float] = []
    ndcg_at_10: list[float] = []
    ndcg_at_20: list[float] = []
    rerank_latencies: list[float] = []
    first_stage_recall: list[float] = []
    per_query_rows: list[dict[str, Any]] = []

    # Baseline: first-stage recall (before reranking)
    baseline_recall_at_50: list[float] = []

    for qi, q in enumerate(queries):
        pool = candidate_pools[qi]
        # Truncate to top_k_rerank candidates
        rerank_pool = pool[:top_k_rerank]

        if not rerank_pool:
            per_query_rows.append({
                "model": model_key,
                "query_id": q.query_id,
                "task_id": q.task_id,
                "query_type": q.query_type,
                "top_k": top_k_rerank,
                "use_body": use_body,
                "pool_size": 0,
                "recall_at_10": 0.0,
                "recall_at_20": 0.0,
                "recall_at_50": 0.0,
                "ndcg_at_10": 0.0,
                "ndcg_at_20": 0.0,
                "baseline_recall_at_50": 0.0,
                "rerank_latency_ms": 0.0,
                "gt_defs": len(q.gt_def_keys),
            })
            continue

        # Build document texts for the rerank pool
        docs = [_build_rerank_text(all_defs[idx], use_body) for idx in rerank_pool]

        # Cross-encoder scoring
        t0 = time.monotonic()
        result = encoder.score(q.query_text, docs)
        rerank_ms = (time.monotonic() - t0) * 1000
        rerank_latencies.append(rerank_ms)

        # Rerank by cross-encoder score
        reranked_order = rerank_indices(result.scores)

        # Build relevance labels for reranked order
        relevances: list[int] = []
        for rank_pos in reranked_order:
            def_idx = rerank_pool[rank_pos]
            rec = all_defs[def_idx]
            key = f"{rec.file_path}:{rec.kind}:{rec.name}"
            relevances.append(1 if key in q.gt_def_keys else 0)

        # Compute metrics
        gt_total = len(q.gt_def_keys)
        if gt_total == 0:
            continue

        def _recall_at(k: int) -> float:
            top_keys = set()
            for rank_pos in reranked_order[:k]:
                def_idx = rerank_pool[rank_pos]
                rec = all_defs[def_idx]
                top_keys.add(f"{rec.file_path}:{rec.kind}:{rec.name}")
            return len(top_keys & q.gt_def_keys) / gt_total

        r10 = _recall_at(10)
        r20 = _recall_at(20)
        r50 = _recall_at(min(50, len(rerank_pool)))
        n10 = ndcg_at_k(relevances, 10)
        n20 = ndcg_at_k(relevances, 20)

        recall_at_10.append(r10)
        recall_at_20.append(r20)
        recall_at_50.append(r50)
        ndcg_at_10.append(n10)
        ndcg_at_20.append(n20)

        # Baseline: first-stage order recall@50 (no reranking)
        baseline_keys = set()
        for idx in rerank_pool[:50]:
            rec = all_defs[idx]
            baseline_keys.add(f"{rec.file_path}:{rec.kind}:{rec.name}")
        bl_r50 = len(baseline_keys & q.gt_def_keys) / gt_total
        baseline_recall_at_50.append(bl_r50)

        # First-stage recall: are GT items even in the pool?
        pool_keys = set()
        for idx in pool:
            rec = all_defs[idx]
            pool_keys.add(f"{rec.file_path}:{rec.kind}:{rec.name}")
        fs_recall = len(pool_keys & q.gt_def_keys) / gt_total
        first_stage_recall.append(fs_recall)

        per_query_rows.append({
            "model": model_key,
            "query_id": q.query_id,
            "task_id": q.task_id,
            "query_type": q.query_type,
            "top_k": top_k_rerank,
            "use_body": use_body,
            "pool_size": len(rerank_pool),
            "recall_at_10": r10,
            "recall_at_20": r20,
            "recall_at_50": r50,
            "ndcg_at_10": n10,
            "ndcg_at_20": n20,
            "baseline_recall_at_50": bl_r50,
            "first_stage_recall": fs_recall,
            "rerank_latency_ms": rerank_ms,
            "gt_defs": gt_total,
        })

    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    metrics = {
        "model": model_key,
        "model_name": encoder.model_name,
        "top_k": top_k_rerank,
        "use_body": use_body,
        "n_defs": len(all_defs),
        "n_queries": len(queries),
        "n_evaluated": len(recall_at_10),
        # Cross-encoder metrics
        "recall_at_10_mean": round(float(np.mean(recall_at_10)), 4) if recall_at_10 else 0,
        "recall_at_20_mean": round(float(np.mean(recall_at_20)), 4) if recall_at_20 else 0,
        "recall_at_50_mean": round(float(np.mean(recall_at_50)), 4) if recall_at_50 else 0,
        "ndcg_at_10_mean": round(float(np.mean(ndcg_at_10)), 4) if ndcg_at_10 else 0,
        "ndcg_at_20_mean": round(float(np.mean(ndcg_at_20)), 4) if ndcg_at_20 else 0,
        # Baseline (first-stage order, no reranking)
        "baseline_recall_at_50_mean": round(float(np.mean(baseline_recall_at_50)), 4) if baseline_recall_at_50 else 0,
        # First-stage pool recall (upper bound)
        "first_stage_pool_recall_mean": round(float(np.mean(first_stage_recall)), 4) if first_stage_recall else 0,
        # Latency
        "rerank_latency_ms_mean": round(float(np.mean(rerank_latencies)), 2) if rerank_latencies else 0,
        "rerank_latency_ms_p95": round(float(np.percentile(rerank_latencies, 95)), 2) if rerank_latencies else 0,
        "rerank_latency_ms_p99": round(float(np.percentile(rerank_latencies, 99)), 2) if rerank_latencies else 0,
        # Resource
        "peak_rss_delta_mb": round((rss_after - rss_before) / 1024, 1),
    }

    return {
        "metrics": metrics,
        "per_query": per_query_rows,
    }


# ── Main entry point ─────────────────────────────────────────────


def run_ce_bakeoff(
    data_dir: Path,
    clones_dir: Path,
    output_dir: Path,
    *,
    repo_ids: list[str] | None = None,
    models: list[str] | None = None,
    top_k_values: list[int] | None = None,
    test_body: bool = False,
    max_queries_per_repo: int = 0,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run the cross-encoder reranking bakeoff experiment.

    Args:
        data_dir: Lab data directory (contains per-repo ground truth).
        clones_dir: Directory with repo clones (contain .recon/index.db).
        output_dir: Where to write results.
        repo_ids: Specific logical repos to evaluate (None = auto-discover).
        models: Which models to test (keys from MODELS dict, None = all).
        top_k_values: Candidate pool sizes to test (default: [20, 50, 100]).
        test_body: Also test scaffold+body representation.
        max_queries_per_repo: Limit queries per repo (0 = all).
        verbose: Enable debug logging.

    Returns:
        Summary metrics dict.
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_keys = models or list(MODELS.keys())
    top_k_list = top_k_values or [20, 50, 100]

    model_cache_dir = str(clones_dir.parent / "model_cache")

    # 1. Discover repos and load data
    from cpl_lab.data_manifest import (
        iter_repo_data_dirs,
        logical_repo_id_for_dir,
        main_clone_dir_for_dir,
    )

    repo_groups: dict[str, tuple[Path | None, list[Path]]] = {}
    for repo_dir in iter_repo_data_dirs(data_dir):
        gt_dir = repo_dir / "ground_truth"
        if not (gt_dir / "queries.jsonl").exists():
            continue
        logical_id = logical_repo_id_for_dir(repo_dir)
        if repo_ids and logical_id not in repo_ids:
            continue
        if logical_id not in repo_groups:
            main_clone = main_clone_dir_for_dir(repo_dir, clones_dir)
            repo_groups[logical_id] = (main_clone, [])
        repo_groups[logical_id][1].append(repo_dir)

    if not repo_groups:
        logger.error("No repos with ground truth found in %s", data_dir)
        return {"error": "no repos"}

    logger.info("CE bakeoff repos: %s", list(repo_groups.keys()))

    # 2. Extract scaffolds and queries
    all_defs: list[DefRecord] = []
    all_queries: list[QueryRecord] = []

    for logical_id, (main_clone, instance_dirs) in repo_groups.items():
        if main_clone is None or not (main_clone / ".recon" / "index.db").exists():
            logger.warning("No indexed clone for %s, skipping", logical_id)
            continue

        logger.info("Extracting defs from %s (%s) ...", logical_id, main_clone.name)
        defs = _extract_repo_defs(
            logical_id, main_clone, include_body=test_body,
        )
        logger.info("  %d defs", len(defs))

        repo_queries: list[QueryRecord] = []
        for inst_dir in instance_dirs:
            qs = _load_queries(inst_dir.name, data_dir)
            repo_queries.extend(qs)
        if max_queries_per_repo > 0:
            repo_queries = repo_queries[:max_queries_per_repo]
        logger.info("  %d queries from %d instances", len(repo_queries), len(instance_dirs))

        all_defs.extend(defs)
        all_queries.extend(repo_queries)

    logger.info(
        "Total: %d defs, %d queries across %d repos",
        len(all_defs), len(all_queries), len(repo_groups),
    )

    if not all_defs or not all_queries:
        logger.error("Insufficient data for bakeoff")
        return {"error": "insufficient data"}

    # 3. Run model × top_k × representation grid
    all_results: dict[str, Any] = {}
    all_per_query: list[dict[str, Any]] = []
    latency_profile: list[dict[str, Any]] = []

    for model_key in model_keys:
        if model_key not in MODELS:
            logger.warning("Unknown model key: %s", model_key)
            continue

        model_name = MODELS[model_key]
        encoder = CrossEncoderModel(
            model_name=model_name, cache_dir=model_cache_dir,
        )

        representations = [False]
        if test_body:
            representations.append(True)

        for use_body in representations:
            for top_k in top_k_list:
                variant_key = f"{model_key}_top{top_k}"
                if use_body:
                    variant_key += "_body"

                try:
                    result = _evaluate_model(
                        model_key=variant_key,
                        encoder=encoder,
                        all_defs=all_defs,
                        queries=all_queries,
                        top_k_rerank=top_k,
                        use_body=use_body,
                    )
                except Exception as exc:
                    logger.error(
                        "Variant %s failed: %s", variant_key, exc, exc_info=True,
                    )
                    all_results[variant_key] = {
                        "model": variant_key, "error": str(exc),
                    }
                    continue

                all_results[variant_key] = result["metrics"]
                all_per_query.extend(result["per_query"])

                latency_profile.append({
                    "variant": variant_key,
                    "model": model_key,
                    "top_k": top_k,
                    "use_body": use_body,
                    "latency_ms_mean": result["metrics"]["rerank_latency_ms_mean"],
                    "latency_ms_p95": result["metrics"]["rerank_latency_ms_p95"],
                    "recall_at_10": result["metrics"]["recall_at_10_mean"],
                    "recall_at_50": result["metrics"]["recall_at_50_mean"],
                    "ndcg_at_10": result["metrics"]["ndcg_at_10_mean"],
                })

    # 4. Write results
    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(json.dumps(all_results, indent=2))
    logger.info("Wrote %s", metrics_path)

    latency_path = output_dir / "latency_profile.json"
    latency_path.write_text(json.dumps(latency_profile, indent=2))
    logger.info("Wrote %s", latency_path)

    if all_per_query:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist(all_per_query)
        pq_path = output_dir / "per_query.parquet"
        pq.write_table(table, str(pq_path))
        logger.info("Wrote %s", pq_path)

    # 5. Print summary
    logger.info("\n=== Cross-Encoder Reranking Bakeoff Results ===")
    for vk, m in all_results.items():
        if "error" in m:
            logger.info("  %s: ERROR — %s", vk, m["error"])
            continue
        logger.info(
            "  %s: R@10=%.3f  R@20=%.3f  R@50=%.3f  NDCG@10=%.3f  "
            "baseline_R@50=%.3f  lat=%.0fms(p95=%.0fms)  pool_recall=%.3f",
            vk,
            m["recall_at_10_mean"],
            m["recall_at_20_mean"],
            m["recall_at_50_mean"],
            m["ndcg_at_10_mean"],
            m["baseline_recall_at_50_mean"],
            m["rerank_latency_ms_mean"],
            m["rerank_latency_ms_p95"],
            m["first_stage_pool_recall_mean"],
        )

    return all_results
