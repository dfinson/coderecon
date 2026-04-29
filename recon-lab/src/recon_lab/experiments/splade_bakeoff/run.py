"""SPLADE model bakeoff — compare sparse encoders on code scaffold retrieval.

Compares three SPLADE-family models on the same scaffolds and queries:
  - naver/splade-v3-distilbert  (67M, quality anchor)
  - rasyosef/splade-mini        (11M, speed anchor)
  - opensearch-neural-sparse-v2 (67M, license-safe anchor)

Protocol:
  1. Load indexed repos (needs existing .recon/index.db)
  2. Build untruncated per-def scaffolds from DB facts
  3. Encode all scaffolds with each model
  4. Load PR-derived queries + ground truth from lab data
  5. For each model × query: compute file Recall@20, def Recall@50
  6. Measure throughput, sparsity, top-K term quality
  7. Write results to JSON + parquet

Inputs:
  - Indexed repos with .recon/index.db (from clone + index-main stages)
  - PR ground truth from pr-import stage (data/{repo_id}/ground_truth/)

Outputs:
  - ${workspace}/experiments/splade_bakeoff/metrics.json
  - ${workspace}/experiments/splade_bakeoff/per_query.parquet
  - ${workspace}/experiments/splade_bakeoff/term_samples.json
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

from recon_lab.experiments.splade_bakeoff.encoder import (
    MODELS,
    EncodeResult,
    SpladeEncoder,
    active_dims,
    aggregate_file_vector,
    l2_norm_sparse,
    sparse_dot,
    top_k_terms,
)
from recon_lab.experiments.splade_bakeoff.scaffold import (
    build_def_scaffold,
    build_file_header_scaffold,
    word_split,
)

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────


@dataclass
class DefRecord:
    """A def from the index with its scaffold text."""

    def_uid: str
    file_path: str
    kind: str
    name: str
    start_line: int
    end_line: int
    scaffold: str


@dataclass
class QueryRecord:
    """A query with ground truth def_uids."""

    query_id: str
    task_id: str
    query_text: str
    query_type: str
    gt_def_uids: set[str]
    gt_file_paths: set[str]


@dataclass
class RepoData:
    """Extracted data for one repo."""

    repo_id: str
    defs: list[DefRecord] = field(default_factory=list)
    file_headers: list[DefRecord] = field(default_factory=list)
    queries: list[QueryRecord] = field(default_factory=list)


# ── Scaffold extraction from index DB ────────────────────────────


def _extract_repo_scaffolds(
    repo_id: str,
    main_clone_dir: Path,
) -> tuple[list[DefRecord], list[DefRecord]]:
    """Extract per-def scaffolds and file-header pseudo-defs from index.db."""

    recon_dir = main_clone_dir / ".recon"
    if not (recon_dir / "index.db").exists():
        logger.warning("No index.db for %s, skipping", repo_id)
        return [], []

    from coderecon.index.db.database import Database
    from coderecon.index.graph.code_graph import FactQueries
    from coderecon.index.models import DefFact, ImportFact, RefFact
    from sqlmodel import Session, select

    db = Database(recon_dir / "index.db")
    engine = db.engine

    defs: list[DefRecord] = []
    file_headers: list[DefRecord] = []

    with Session(engine) as session:
        fq = FactQueries(session)

        # Get all files
        files = fq.list_files(limit=50_000)
        file_map = {f.id: f for f in files}

        # Get all defs
        all_defs = session.exec(select(DefFact)).all()

        # Group defs by file_id
        defs_by_file: dict[int, list[Any]] = {}
        for d in all_defs:
            defs_by_file.setdefault(d.file_id, []).append(d)

        # Get imports by file_id
        all_imports = session.exec(select(ImportFact)).all()
        imports_by_file: dict[int, list[Any]] = {}
        for imp in all_imports:
            imports_by_file.setdefault(imp.file_id, []).append(imp)

        for file_id, file_obj in file_map.items():
            file_path = file_obj.path
            file_defs = defs_by_file.get(file_id, [])
            file_imports = imports_by_file.get(file_id, [])

            # Build file-header pseudo-def
            import_sources = []
            for imp in file_imports:
                src = imp.source_literal or imp.imported_name or ""
                if src:
                    import_sources.append(src)

            header_scaffold = build_file_header_scaffold(
                file_path, import_sources,
            )
            if header_scaffold:
                file_headers.append(DefRecord(
                    def_uid=f"__header__{file_path}",
                    file_path=file_path,
                    kind="file_header",
                    name=file_path,
                    start_line=0,
                    end_line=0,
                    scaffold=header_scaffold,
                ))

            # Build per-def scaffolds
            for d in file_defs:
                # Get callees from ref_facts via FactQueries
                callee_defs = fq.list_callees_in_scope(
                    file_id, d.start_line, d.end_line, limit=50,
                )
                callee_names = [cd.name for cd in callee_defs]

                # Get type references (PROVEN/STRONG tier, not calls)
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
                if scaffold:
                    defs.append(DefRecord(
                        def_uid=d.def_uid,
                        file_path=file_path,
                        kind=d.kind,
                        name=d.name,
                        start_line=d.start_line,
                        end_line=d.end_line,
                        scaffold=scaffold,
                    ))

    return defs, file_headers


# ── Query + ground truth loading ─────────────────────────────────


def _load_queries(repo_id: str, data_dir: Path) -> list[QueryRecord]:
    """Load PR-derived queries and ground truth for a repo.

    ``repo_id`` is the per-PR instance id (e.g. ``python-rich_pr4077``).
    Follows the same schema conventions as ``eval_gt.py``.
    """
    repo_data_dir = data_dir / repo_id
    gt_dir = repo_data_dir / "ground_truth"

    if not gt_dir.is_dir():
        return []

    # ── 1. Load touched objects (ground truth defs) ──────────────
    touched_file = gt_dir / "touched_objects.jsonl"
    gt_by_task: dict[str, set[str]] = {}       # task_id → {candidate_key, ...}
    gt_files_by_task: dict[str, set[str]] = {}  # task_id → {path, ...}

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
            # candidate_key is path:kind:name:start_line — but index
            # is at HEAD while GT is at the PR commit, so start_line
            # drifts.  Match on path:kind:name which is the stable identity.
            ckey = row.get("candidate_key", "")
            path = row.get("path", "")
            if ckey:
                parts = ckey.rsplit(":", 1)  # strip :start_line
                stable_key = parts[0] if len(parts) == 2 and parts[1].isdigit() else ckey
                gt_by_task.setdefault(task_id, set()).add(stable_key)
            if path:
                gt_files_by_task.setdefault(task_id, set()).add(path)

    # ── 2. Load queries ──────────────────────────────────────────
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
            # Only keep OK-gated queries that have ground truth
            if label_gate != "OK":
                continue
            gt_defs = gt_by_task.get(task_id, set())
            if not gt_defs:
                continue  # all GT objects were phantoms
            records.append(QueryRecord(
                query_id=row.get("query_id", ""),
                task_id=task_id,
                query_text=row.get("query_text", ""),
                query_type=row.get("query_type", ""),
                gt_def_uids=gt_defs,
                gt_file_paths=gt_files_by_task.get(task_id, set()),
            ))

    return records


# ── Retrieval evaluation ─────────────────────────────────────────


def _evaluate_model(
    model_key: str,
    encoder: SpladeEncoder,
    all_defs: list[DefRecord],
    file_headers: list[DefRecord],
    queries: list[QueryRecord],
    sample_scaffolds: int = 20,
) -> dict[str, Any]:
    """Run full evaluation for one model. Returns metrics dict."""

    logger.info("Evaluating model: %s (%s)", model_key, encoder.model_name)

    # Track peak RSS before/after model load + encode
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KB on Linux

    # 1. Encode all scaffolds
    all_records = all_defs + file_headers
    all_scaffolds = [r.scaffold for r in all_records]

    logger.info("Encoding %d scaffolds...", len(all_scaffolds))
    doc_result = encoder.encode_documents(all_scaffolds)
    doc_vecs = doc_result.sparse_vecs

    # Split back into def vecs and header vecs
    def_vecs = doc_vecs[:len(all_defs)]
    header_vecs = doc_vecs[len(all_defs):]

    # 2. Build file vectors by aggregation
    # Map: file_path -> list of (def_idx, sparse_vec)
    file_def_vecs: dict[str, list[dict[int, float]]] = {}
    for i, rec in enumerate(all_defs):
        file_def_vecs.setdefault(rec.file_path, []).append(def_vecs[i])
    for i, rec in enumerate(file_headers):
        file_def_vecs.setdefault(rec.file_path, []).append(header_vecs[i])

    file_vectors: dict[str, dict[int, float]] = {}
    for path, vecs in file_def_vecs.items():
        file_vectors[path] = aggregate_file_vector(vecs, normalize=True)

    # 3. Build def lookup
    def_uid_to_vec: dict[str, dict[int, float]] = {}
    def_uid_to_path: dict[str, str] = {}
    for i, rec in enumerate(all_defs):
        def_uid_to_vec[rec.def_uid] = def_vecs[i]
        def_uid_to_path[rec.def_uid] = rec.file_path

    # 4. Encode queries
    query_texts = [q.query_text for q in queries]
    logger.info("Encoding %d queries...", len(query_texts))
    if not query_texts:
        return {"model": model_key, "error": "no queries"}
    query_result = encoder.encode_queries(query_texts)
    query_vecs = query_result.sparse_vecs

    rss_after_encode = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # 5. Evaluate retrieval
    file_recall_at_20: list[float] = []
    def_recall_at_50: list[float] = []
    retrieval_latencies: list[float] = []
    per_query_rows: list[dict[str, Any]] = []

    for qi, q in enumerate(queries):
        qvec = query_vecs[qi]
        t_ret = time.monotonic()

        # File retrieval: score all files, take top-20
        file_scores = {
            path: sparse_dot(qvec, fvec)
            for path, fvec in file_vectors.items()
        }
        ranked_files = sorted(file_scores.items(), key=lambda x: x[1], reverse=True)[:20]
        retrieved_files = {path for path, _ in ranked_files}

        if q.gt_file_paths:
            fr = len(retrieved_files & q.gt_file_paths) / len(q.gt_file_paths)
        else:
            fr = 0.0
        file_recall_at_20.append(fr)

        # Def retrieval: score all defs in top-20 files, take top-50
        candidate_defs: list[tuple[int, float]] = []
        for i, rec in enumerate(all_defs):
            if rec.file_path in retrieved_files:
                score = sparse_dot(qvec, def_vecs[i])
                candidate_defs.append((i, score))
        candidate_defs.sort(key=lambda x: x[1], reverse=True)
        top_50 = candidate_defs[:50]

        # Build candidate keys (path:kind:name) for top-50
        retrieved_keys = set()
        for idx, _ in top_50:
            rec = all_defs[idx]
            retrieved_keys.add(f"{rec.file_path}:{rec.kind}:{rec.name}")

        # Match against GT candidate keys
        gt_total = len(q.gt_def_uids)
        gt_matched = len(q.gt_def_uids & retrieved_keys)

        dr = gt_matched / gt_total if gt_total > 0 else 0.0
        def_recall_at_50.append(dr)

        ret_ms = (time.monotonic() - t_ret) * 1000
        retrieval_latencies.append(ret_ms)

        per_query_rows.append({
            "model": model_key,
            "query_id": q.query_id,
            "task_id": q.task_id,
            "query_type": q.query_type,
            "file_recall_at_20": fr,
            "def_recall_at_50": dr,
            "gt_files": len(q.gt_file_paths),
            "gt_defs": gt_total,
        })

    # 6. Sparsity stats
    dims_per_scaffold = [active_dims(v) for v in def_vecs]

    # 7. Term quality samples
    term_samples: list[dict[str, Any]] = []
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(encoder.model_name)
        sample_indices = np.random.choice(
            len(all_defs), min(sample_scaffolds, len(all_defs)), replace=False,
        )
        for idx in sample_indices:
            rec = all_defs[idx]
            terms = top_k_terms(def_vecs[idx], tokenizer, k=10)
            term_samples.append({
                "def_uid": rec.def_uid,
                "scaffold": rec.scaffold[:200],
                "top_terms": [{"term": t, "weight": round(w, 3)} for t, w in terms],
            })
    except Exception as exc:
        logger.warning("Failed to load tokenizer for term samples: %s", exc)

    metrics = {
        "model": model_key,
        "model_name": encoder.model_name,
        "n_defs": len(all_defs),
        "n_files": len(file_vectors),
        "n_queries": len(queries),
        "encode_throughput_docs_per_sec": round(doc_result.texts_per_sec, 1),
        "encode_throughput_queries_per_sec": round(query_result.texts_per_sec, 1),
        "encode_total_secs": round(doc_result.encode_secs + query_result.encode_secs, 2),
        "peak_rss_mb": round((rss_after_encode - rss_before) / 1024, 1),  # delta MB
        "retrieval_latency_ms_mean": round(float(np.mean(retrieval_latencies)), 2) if retrieval_latencies else 0,
        "retrieval_latency_ms_p95": round(float(np.percentile(retrieval_latencies, 95)), 2) if retrieval_latencies else 0,
        "active_dims_mean": round(float(np.mean(dims_per_scaffold)), 1) if dims_per_scaffold else 0,
        "active_dims_p95": round(float(np.percentile(dims_per_scaffold, 95)), 1) if dims_per_scaffold else 0,
        "file_recall_at_20_mean": round(float(np.mean(file_recall_at_20)), 4) if file_recall_at_20 else 0,
        "def_recall_at_50_mean": round(float(np.mean(def_recall_at_50)), 4) if def_recall_at_50 else 0,
        "file_recall_at_20_median": round(float(np.median(file_recall_at_20)), 4) if file_recall_at_20 else 0,
        "def_recall_at_50_median": round(float(np.median(def_recall_at_50)), 4) if def_recall_at_50 else 0,
    }

    return {
        "metrics": metrics,
        "per_query": per_query_rows,
        "term_samples": term_samples,
    }


# ── Main entry point ─────────────────────────────────────────────


def run_bakeoff(
    data_dir: Path,
    clones_dir: Path,
    output_dir: Path,
    *,
    repo_ids: list[str] | None = None,
    models: list[str] | None = None,
    max_queries_per_repo: int = 0,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run the SPLADE model bakeoff experiment.

    Args:
        data_dir: Lab data directory (contains per-repo ground truth).
        clones_dir: Directory with repo clones (contain .recon/index.db).
        output_dir: Where to write results.
        repo_ids: Specific repos to evaluate (None = auto-discover).
        models: Which models to test (keys from MODELS dict, None = all).
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

    # Cache HF models beside the workspace data to avoid re-downloads
    model_cache_dir = str(clones_dir.parent / "model_cache")

    # 1. Discover PR-instance data dirs, group by logical repo
    from recon_lab.data_manifest import (
        iter_repo_data_dirs,
        logical_repo_id_for_dir,
        main_clone_dir_for_dir,
    )

    # Group: logical_repo_id → (main_clone, [pr_instance_data_dirs])
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

    logger.info("Bakeoff repos: %s", list(repo_groups.keys()))

    # 2. Extract scaffolds (once per logical repo) and queries (from each PR instance)
    all_defs: list[DefRecord] = []
    all_headers: list[DefRecord] = []
    all_queries: list[QueryRecord] = []

    for logical_id, (main_clone, instance_dirs) in repo_groups.items():
        if main_clone is None or not (main_clone / ".recon" / "index.db").exists():
            logger.warning("No indexed clone for %s, skipping", logical_id)
            continue

        logger.info("Extracting scaffolds from %s (%s) ...", logical_id, main_clone.name)
        defs, headers = _extract_repo_scaffolds(logical_id, main_clone)
        logger.info("  %d defs, %d file headers", len(defs), len(headers))

        # Load queries from each PR instance that shares this repo's index
        repo_queries: list[QueryRecord] = []
        for inst_dir in instance_dirs:
            qs = _load_queries(inst_dir.name, data_dir)
            repo_queries.extend(qs)
        if max_queries_per_repo > 0:
            repo_queries = repo_queries[:max_queries_per_repo]
        logger.info("  %d queries from %d instances", len(repo_queries), len(instance_dirs))

        all_defs.extend(defs)
        all_headers.extend(headers)
        all_queries.extend(repo_queries)

    logger.info(
        "Total: %d defs, %d file headers, %d queries across %d repos",
        len(all_defs), len(all_headers), len(all_queries), len(repo_groups),
    )

    if not all_defs or not all_queries:
        logger.error("Insufficient data for bakeoff")
        return {"error": "insufficient data"}

    # 3. Run each model
    all_results: dict[str, Any] = {}
    all_per_query: list[dict[str, Any]] = []

    for model_key in model_keys:
        if model_key not in MODELS:
            logger.warning("Unknown model key: %s", model_key)
            continue

        model_name = MODELS[model_key]
        encoder = SpladeEncoder(model_name=model_name, cache_dir=model_cache_dir)

        try:
            result = _evaluate_model(
                model_key, encoder, all_defs, all_headers, all_queries,
            )
        except Exception as exc:
            logger.error("Model %s failed: %s", model_key, exc, exc_info=True)
            all_results[model_key] = {"model": model_key, "error": str(exc)}
            continue

        all_results[model_key] = result["metrics"]
        all_per_query.extend(result["per_query"])

        # Write term samples per model
        term_path = output_dir / f"term_samples_{model_key}.json"
        term_path.write_text(json.dumps(result["term_samples"], indent=2))
        logger.info("Wrote %s", term_path)

    # 4. Write aggregate metrics
    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(json.dumps(all_results, indent=2))
    logger.info("Wrote %s", metrics_path)

    # 5. Write per-query parquet
    if all_per_query:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist(all_per_query)
        pq_path = output_dir / "per_query.parquet"
        pq.write_table(table, str(pq_path))
        logger.info("Wrote %s", pq_path)

    # 6. Print summary
    logger.info("\n=== SPLADE Bakeoff Results ===")
    for mk, m in all_results.items():
        if "error" in m:
            logger.info("  %s: ERROR — %s", mk, m["error"])
            continue
        logger.info(
            "  %s: file_R@20=%.3f  def_R@50=%.3f  throughput=%.0f docs/s  ret_lat=%.1fms  peak_rss=%.0fMB  dims=%.0f (p95=%.0f)",
            mk,
            m["file_recall_at_20_mean"],
            m["def_recall_at_50_mean"],
            m["encode_throughput_docs_per_sec"],
            m["retrieval_latency_ms_mean"],
            m["peak_rss_mb"],
            m["active_dims_mean"],
            m["active_dims_p95"],
        )

    return all_results



