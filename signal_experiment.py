#!/usr/bin/env python3
"""Signal quality experiment: run GT-mirroring queries and analyze signal separation.

Mirrors the 8 OK query tiers from the GT dataset spec against 3 realistic
tasks at different complexity levels (narrow, medium, wide) on the coderecon
repo itself. Collects raw signals and measures separation between GT-relevant
and irrelevant candidates.
"""

import json
import time
from dataclasses import dataclass, field

import requests

MCP_URL = "http://127.0.0.1:7654/mcp"
SESSION_ID: str | None = None


def _init_session() -> str:
    """Initialize MCP session and return session ID."""
    resp = requests.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "signal_experiment", "version": "0.1"},
            },
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    resp.raise_for_status()
    sid = resp.headers.get("mcp-session-id")
    if not sid:
        raise RuntimeError("No mcp-session-id in response headers")
    # Send initialized notification
    requests.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "mcp-session-id": sid,
        },
    )
    return sid

# ─── Ground truth: 3 tasks at 3 complexity levels ──────────────────────

# Task N1 (narrow): Fix how file_embedding handles empty docstrings
# GT defs: defs the developer would actually need to read/edit
GT_N1 = {
    "task": "Fix file embedding to skip files with empty docstrings instead of embedding empty text",
    "complexity": "narrow",
    "gt_names": {
        "build_doc_section_chunks",  # the chunking function to fix
        "FileEmbeddingIndex.add_file",  # entry point that calls chunking
        "_split_doc_sections",  # helper that produces sections
    },
    "gt_paths": [
        "src/coderecon/index/_internal/indexing/file_embedding.py",
    ],
}

# Task M1 (medium): Add ref_tier tracking to graph harvester
# GT defs: the signal gap implementation we just did
GT_M1 = {
    "task": "Track ref_tier quality per caller edge in the graph harvester and propagate graph_caller_max_tier to HarvestCandidate",
    "complexity": "medium",
    "gt_names": {
        "_harvest_graph",       # main function edited
        "HarvestCandidate",     # dataclass with new field
        "list_refs_by_def_uid", # query method used for refs
        "FactQueries",          # class containing query methods
        "extract_ranker_features",  # downstream consumer of the signal
        "_prepare_features",    # training pipeline feature prep
    },
    "gt_paths": [
        "src/coderecon/mcp/tools/recon/harvesters.py",
        "src/coderecon/mcp/tools/recon/models.py",
        "src/coderecon/index/_internal/indexing/graph.py",
        "src/coderecon/ranking/features.py",
    ],
}

# Task W1 (wide): Add test selection via coverage analysis
# GT defs: spread across multiple subsystems
GT_W1 = {
    "task": "Add test selection by analyzing which pre-existing test functions cover changed lines, using coverage data, import graph, and diff analysis",
    "complexity": "wide",
    "gt_names": {
        "batch_count_test_coverage",   # graph query for coverage
        "TestCoverageFact",            # the coverage fact model
        "_enrich_candidates",          # merge step that resolves coverage
        "raw_signals_pipeline",        # where coverage count appears in output
        "extract_ranker_features",     # ranker feature extraction
        "_harvest_graph",              # graph harvester
        "_harvest_term_match",         # term harvester
        "HarvestCandidate",            # dataclass
        "RANKER_FEATURES",             # training feature list
        "collect_raw_signals",         # top-level raw signals
    },
    "gt_paths": [
        "src/coderecon/mcp/tools/recon/harvesters.py",
        "src/coderecon/mcp/tools/recon/merge.py",
        "src/coderecon/mcp/tools/recon/raw_signals.py",
        "src/coderecon/mcp/tools/recon/models.py",
        "src/coderecon/index/_internal/indexing/graph.py",
        "src/coderecon/ranking/features.py",
    ],
}

TASKS = {"N1": GT_N1, "M1": GT_M1, "W1": GT_W1}


# ─── Query tier definitions ──────────────────────────────────────────

@dataclass
class Query:
    tier: str
    text: str
    seeds: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)


def build_queries(task: dict) -> list[Query]:
    """Build 8 OK query tiers for a task, mirroring the GT spec."""
    gt_names = list(task["gt_names"])
    gt_paths = task["gt_paths"]
    task_text = task["task"]

    queries = []

    # 1. Q_SEMANTIC — pure natural language, no identifiers
    queries.append(Query(
        tier="Q_SEMANTIC",
        text=task_text,
    ))

    # 2. Q_LEXICAL — query with code-like terms for BM25
    lex_terms = " ".join(gt_names[:3])
    queries.append(Query(
        tier="Q_LEXICAL",
        text=lex_terms,
    ))

    # 3. Q_IDENTIFIER — identifier names for SQL LIKE
    queries.append(Query(
        tier="Q_IDENTIFIER",
        text=" ".join(gt_names[:4]),
    ))

    # 4. Q_STRUCTURAL — graph walk from 1-2 seeds
    queries.append(Query(
        tier="Q_STRUCTURAL",
        text=task_text,
        seeds=gt_names[:2],
    ))

    # 5. Q_NAVIGATIONAL — explicit paths, no seeds
    queries.append(Query(
        tier="Q_NAVIGATIONAL",
        text=task_text,
        pins=gt_paths[:3],
    ))

    # 6. Q_SEM_IDENT — semantic + identifier hints
    queries.append(Query(
        tier="Q_SEM_IDENT",
        text=task_text,
        seeds=gt_names[:3],
    ))

    # 7. Q_IDENT_NAV — identifiers + path hints
    queries.append(Query(
        tier="Q_IDENT_NAV",
        text=" ".join(gt_names[:4]),
        seeds=gt_names[:4],
        pins=gt_paths[:3],
    ))

    # 8. Q_FULL — all signals
    queries.append(Query(
        tier="Q_FULL",
        text=task_text,
        seeds=gt_names[:4],
        pins=gt_paths[:3],
    ))

    return queries


# ─── MCP call ──────────────────────────────────────────────────────

def call_raw_signals(query: Query) -> dict:
    """Call recon_raw_signals via MCP JSON-RPC."""
    global SESSION_ID
    if SESSION_ID is None:
        SESSION_ID = _init_session()
        print(f"  [MCP session: {SESSION_ID}]")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "recon_raw_signals",
            "arguments": {
                "query": query.text,
                "seeds": query.seeds,
                "pins": query.pins,
            },
        },
    }
    resp = requests.post(
        MCP_URL,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "mcp-session-id": SESSION_ID,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    content = data.get("result", {}).get("content", [])
    if not content:
        raise RuntimeError(f"No content in response: {data}")
    return json.loads(content[0]["text"])


# ─── Analysis ──────────────────────────────────────────────────────

def is_gt_candidate(cand: dict, gt_task: dict) -> bool:
    """Check if a candidate matches ground truth (by name or path)."""
    name = cand.get("name", "")
    qname = cand.get("qualified_name", "")

    # Match by name
    for gt_name in gt_task["gt_names"]:
        if gt_name == name:
            return True
        if gt_name in (qname or ""):
            return True

    return False


def analyze_signals(candidates: list[dict], gt_task: dict) -> dict:
    """Analyze signal separation between GT and non-GT candidates."""
    gt_cands = [c for c in candidates if is_gt_candidate(c, gt_task)]
    non_gt_cands = [c for c in candidates if not is_gt_candidate(c, gt_task)]

    if not gt_cands:
        return {"found": 0, "total_gt": len(gt_task["gt_names"]), "recall": 0.0}

    # Which GT names were found?
    found_names = set()
    for c in gt_cands:
        for gt_name in gt_task["gt_names"]:
            if gt_name == c["name"] or gt_name in (c.get("qualified_name") or ""):
                found_names.add(gt_name)

    result = {
        "found": len(found_names),
        "found_names": sorted(found_names),
        "missing_names": sorted(gt_task["gt_names"] - found_names),
        "total_gt": len(gt_task["gt_names"]),
        "recall": len(found_names) / len(gt_task["gt_names"]),
        "total_candidates": len(candidates),
        "gt_candidates": len(gt_cands),
    }

    # Signal separation analysis
    signals = [
        ("emb_score", True),         # higher = better
        ("retriever_hits", True),
        ("term_match_count", True),
        ("lex_hit_count", True),
        ("graph_seed_rank", False),   # lower = better
        ("seed_path_distance", False),
        ("package_distance", False),
    ]

    for sig_name, higher_better in signals:
        gt_vals = [c[sig_name] for c in gt_cands if c.get(sig_name) is not None]
        non_gt_vals = [c[sig_name] for c in non_gt_cands if c.get(sig_name) is not None]

        if gt_vals and non_gt_vals:
            gt_mean = sum(gt_vals) / len(gt_vals)
            non_gt_mean = sum(non_gt_vals) / len(non_gt_vals)
            if non_gt_mean != 0:
                separation = gt_mean / non_gt_mean
            else:
                separation = float("inf") if gt_mean > 0 else 1.0
            result[f"{sig_name}_gt_mean"] = round(gt_mean, 4)
            result[f"{sig_name}_nongt_mean"] = round(non_gt_mean, 4)
            result[f"{sig_name}_separation"] = round(separation, 2)
        elif gt_vals:
            result[f"{sig_name}_gt_mean"] = round(sum(gt_vals) / len(gt_vals), 4)
            result[f"{sig_name}_nongt_mean"] = None
            result[f"{sig_name}_separation"] = "∞"

    # Boolean/categorical signal enrichment
    bool_signals = ["is_endpoint", "is_test", "has_docstring"]
    for sig_name in bool_signals:
        gt_rate = sum(1 for c in gt_cands if c.get(sig_name)) / len(gt_cands) if gt_cands else 0
        non_gt_rate = sum(1 for c in non_gt_cands if c.get(sig_name)) / len(non_gt_cands) if non_gt_cands else 0
        result[f"{sig_name}_gt_rate"] = round(gt_rate, 3)
        result[f"{sig_name}_nongt_rate"] = round(non_gt_rate, 3)

    # Graph edge type distribution for GT
    gt_edge_types = {}
    for c in gt_cands:
        et = c.get("graph_edge_type")
        if et:
            gt_edge_types[et] = gt_edge_types.get(et, 0) + 1
    result["gt_edge_types"] = gt_edge_types

    # graph_caller_max_tier for GT
    gt_tiers = {}
    for c in gt_cands:
        tier = c.get("graph_caller_max_tier")
        if tier:
            gt_tiers[tier] = gt_tiers.get(tier, 0) + 1
    result["gt_caller_tiers"] = gt_tiers

    # New signals: test_coverage_count, same_package
    for sig_name in ["test_coverage_count", "same_package"]:
        gt_vals = [c[sig_name] for c in gt_cands if c.get(sig_name) is not None]
        non_gt_vals = [c[sig_name] for c in non_gt_cands if c.get(sig_name) is not None]
        if gt_vals:
            gt_mean = sum(gt_vals) / len(gt_vals)
            result[f"{sig_name}_gt_mean"] = round(gt_mean, 4)
        if non_gt_vals:
            non_gt_mean = sum(non_gt_vals) / len(non_gt_vals)
            result[f"{sig_name}_nongt_mean"] = round(non_gt_mean, 4)

    # Rank analysis: if we sort by retriever_hits desc, what rank are GT defs?
    ranked = sorted(candidates, key=lambda c: -(c.get("retriever_hits") or 0))
    gt_ranks = []
    for rank, c in enumerate(ranked, 1):
        if is_gt_candidate(c, gt_task):
            gt_ranks.append(rank)
    result["gt_ranks_by_retriever_hits"] = gt_ranks[:20]

    # Precision at various K
    for k in [5, 10, 20, 50]:
        top_k = ranked[:k]
        tp = sum(1 for c in top_k if is_gt_candidate(c, gt_task))
        p_at_k = tp / k
        r_at_k = tp / len(gt_task["gt_names"]) if gt_task["gt_names"] else 0
        f1_at_k = 2 * p_at_k * r_at_k / (p_at_k + r_at_k) if (p_at_k + r_at_k) > 0 else 0
        result[f"P@{k}"] = round(p_at_k, 3)
        result[f"R@{k}"] = round(r_at_k, 3)
        result[f"F1@{k}"] = round(f1_at_k, 3)

    return result


# ─── Main ──────────────────────────────────────────────────────────

def main():
    all_results = {}

    for task_id, task in TASKS.items():
        print(f"\n{'='*70}")
        print(f"Task {task_id} ({task['complexity']}): {task['task'][:60]}...")
        print(f"GT defs: {len(task['gt_names'])} | GT paths: {len(task['gt_paths'])}")
        print(f"{'='*70}")

        queries = build_queries(task)
        task_results = {}

        for q in queries:
            t0 = time.monotonic()
            try:
                raw = call_raw_signals(q)
            except Exception as e:
                print(f"  {q.tier}: ERROR — {e}")
                continue
            elapsed = time.monotonic() - t0

            candidates = raw.get("candidates", [])
            diag = raw.get("diagnostics", {})

            analysis = analyze_signals(candidates, task)

            task_results[q.tier] = {
                "elapsed_ms": round(elapsed * 1000),
                "candidates": len(candidates),
                "diagnostics": {
                    "emb": diag.get("emb_hits", 0),
                    "term": diag.get("term_hits", 0),
                    "lex": diag.get("lex_hits", 0),
                    "graph": diag.get("graph_hits", 0),
                    "sym": diag.get("symbol_hits", 0),
                },
                "analysis": analysis,
            }

            found = analysis.get("found", 0)
            total = analysis.get("total_gt", 0)
            recall = analysis.get("recall", 0)
            f1_5 = analysis.get("F1@5", 0)
            f1_20 = analysis.get("F1@20", 0)
            emb_sep = analysis.get("emb_score_separation", "-")
            hits_sep = analysis.get("retriever_hits_separation", "-")

            seeds_str = f" seeds={len(q.seeds)}" if q.seeds else ""
            pins_str = f" pins={len(q.pins)}" if q.pins else ""
            print(
                f"  {q.tier:16s} | "
                f"cands={len(candidates):5d} | "
                f"found={found}/{total} | "
                f"R={recall:.0%} | "
                f"F1@5={f1_5:.3f} F1@20={f1_20:.3f} | "
                f"EmbSep={emb_sep} HitSep={hits_sep}"
                f"{seeds_str}{pins_str} | "
                f"{round(elapsed * 1000)}ms"
            )

        all_results[task_id] = task_results

    # Summary table
    print(f"\n\n{'='*90}")
    print("SUMMARY TABLE")
    print(f"{'='*90}")
    print(f"{'Task':4s} {'Tier':16s} {'Cands':>6s} {'Found':>6s} {'Recall':>7s} "
          f"{'F1@5':>6s} {'F1@10':>6s} {'F1@20':>6s} {'EmbSep':>7s} {'HitSep':>7s} "
          f"{'PathSep':>8s}")
    print("-" * 90)

    for task_id, task_results in all_results.items():
        for tier, data in task_results.items():
            a = data["analysis"]
            found = a.get("found", 0)
            total = a.get("total_gt", 0)
            recall = a.get("recall", 0)
            print(
                f"{task_id:4s} {tier:16s} "
                f"{data['candidates']:6d} "
                f"{found:2d}/{total:<3d} "
                f"{recall:6.0%}  "
                f"{a.get('F1@5', 0):6.3f} "
                f"{a.get('F1@10', 0):6.3f} "
                f"{a.get('F1@20', 0):6.3f} "
                f"{str(a.get('emb_score_separation', '-')):>7s} "
                f"{str(a.get('retriever_hits_separation', '-')):>7s} "
                f"{str(a.get('seed_path_distance_separation', '-')):>8s}"
            )
        print()

    # New signals audit
    print(f"\n{'='*90}")
    print("NEW SIGNAL AUDIT (per task, Q_FULL tier)")
    print(f"{'='*90}")
    for task_id, task_results in all_results.items():
        full = task_results.get("Q_FULL", {}).get("analysis", {})
        if not full:
            continue
        print(f"\n--- {task_id} ({TASKS[task_id]['complexity']}) ---")
        print(f"  GT edge types:          {full.get('gt_edge_types', {})}")
        print(f"  GT caller tiers:        {full.get('gt_caller_tiers', {})}")
        print(f"  test_coverage GT mean:  {full.get('test_coverage_count_gt_mean', 'N/A')}")
        print(f"  test_coverage nonGT:    {full.get('test_coverage_count_nongt_mean', 'N/A')}")
        print(f"  same_package GT mean:   {full.get('same_package_gt_mean', 'N/A')}")
        print(f"  same_package nonGT:     {full.get('same_package_nongt_mean', 'N/A')}")
        print(f"  lex_hit_count GT mean:  {full.get('lex_hit_count_gt_mean', 'N/A')}")
        print(f"  lex_hit_count nonGT:    {full.get('lex_hit_count_nongt_mean', 'N/A')}")
        print(f"  path_distance GT mean:  {full.get('seed_path_distance_gt_mean', 'N/A')}")
        print(f"  path_distance nonGT:    {full.get('seed_path_distance_nongt_mean', 'N/A')}")
        print(f"  package_distance GT:    {full.get('package_distance_gt_mean', 'N/A')}")
        print(f"  package_distance nonGT: {full.get('package_distance_nongt_mean', 'N/A')}")
        print(f"  has_docstring GT rate:  {full.get('has_docstring_gt_rate', 'N/A')}")
        print(f"  has_docstring nonGT:    {full.get('has_docstring_nongt_rate', 'N/A')}")
        print(f"  GT ranks (by hits):     {full.get('gt_ranks_by_retriever_hits', [])}")

    # Dump full JSON for deeper analysis
    with open("/tmp/signal_experiment_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nFull results written to /tmp/signal_experiment_results.json")


if __name__ == "__main__":
    main()
