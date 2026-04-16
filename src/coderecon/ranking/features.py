"""Feature extraction from raw retrieval signals.

Converts the candidate pool returned by ``recon_raw_signals()`` into
feature matrices suitable for the LightGBM ranker, cutoff, and gate
models.

See §2.1 of recon-lab/README.md for feature definitions.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any


def extract_ranker_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build per-candidate feature dicts for the ranker model.

    Parameters
    ----------
    candidates
        Raw candidate dicts from ``recon_raw_signals()``.
    query_features
        Query-level features (query_len, has_identifier, etc.).

    Returns
    -------
    list[dict]
        One feature dict per candidate, ready for LightGBM prediction.
    """
    features_out: list[dict[str, Any]] = []

    for cand in candidates:
        f: dict[str, Any] = {}

        # Term match signal
        f["term_match_count"] = cand.get("term_match_count") or 0
        f["term_total_matches"] = cand.get("term_total_matches") or 0
        f["lex_hit_count"] = cand.get("lex_hit_count") or 0
        f["bm25_file_score"] = cand.get("bm25_file_score") or 0.0

        # Graph signal (categorical → encoded)
        edge_type = cand.get("graph_edge_type")
        f["graph_is_callee"] = edge_type == "callee"
        f["graph_is_caller"] = edge_type == "caller"
        f["graph_is_sibling"] = edge_type == "sibling"
        f["graph_is_implementor"] = edge_type == "implementor"
        f["graph_is_doc_xref"] = edge_type == "doc_xref"
        f["graph_seed_rank"] = cand.get("graph_seed_rank") or 0

        # Graph ref tier (ordinal: proven=3, strong=2, anchored=1, unknown/None=0)
        _tier_val = {"proven": 3, "strong": 2, "anchored": 1, "unknown": 0}
        f["graph_caller_tier"] = _tier_val.get(cand.get("graph_caller_max_tier") or "", 0)

        # Symbol signal (categorical → encoded) — includes pin
        sym_src = cand.get("symbol_source")
        f["sym_agent_seed"] = sym_src == "agent_seed"
        f["sym_auto_seed"] = sym_src == "auto_seed"
        f["sym_task_extracted"] = sym_src == "task_extracted"
        f["sym_path_mention"] = sym_src == "path_mention"
        f["sym_pin"] = sym_src == "pin"

        # Import signal (categorical → encoded)
        imp_dir = cand.get("import_direction")
        f["import_forward"] = imp_dir == "forward"
        f["import_reverse"] = imp_dir == "reverse"
        f["import_barrel"] = imp_dir == "barrel"
        f["import_test_pair"] = imp_dir == "test_pair"

        # Retriever agreement
        f["retriever_hits"] = cand.get("retriever_hits", 0)

        # Kind one-hot (function/class/method/variable/interface → other)
        kind = cand.get("kind", "")
        f["kind_function"] = kind == "function"
        f["kind_class"] = kind == "class"
        f["kind_method"] = kind == "method"
        f["kind_variable"] = kind == "variable"
        f["kind_interface"] = kind == "interface"

        # Object metadata
        f["object_size_lines"] = cand.get("object_size_lines", 0)
        f["path_depth"] = cand.get("path_depth", 0)
        f["nesting_depth"] = cand.get("nesting_depth", 0)
        f["hub_score"] = cand.get("hub_score", 0)
        f["is_test"] = cand.get("is_test", False)
        f["is_barrel"] = cand.get("is_barrel", False)
        f["is_endpoint"] = cand.get("is_endpoint", False)
        f["test_coverage_count"] = cand.get("test_coverage_count", 0)
        f["has_docstring"] = cand.get("has_docstring", False)
        f["has_decorators"] = cand.get("has_decorators", False)
        f["has_return_type"] = cand.get("has_return_type", False)
        f["has_parent_scope"] = cand.get("has_parent_scope", False)
        f["has_signature"] = cand.get("signature_text") is not None

        # Structural link signals
        f["shares_file_with_seed"] = cand.get("shares_file_with_seed", False)
        f["is_callee_of_top"] = cand.get("is_callee_of_top", False)
        f["is_imported_by_top"] = cand.get("is_imported_by_top", False)
        f["from_coverage"] = cand.get("from_coverage", False)

        # RRF ensemble score (computed in shared pipeline layer)
        f["rrf_score"] = cand.get("rrf_score", 0.0)

        # Artifact kind one-hot (code/test/config/doc/build)
        ak = cand.get("artifact_kind", "code")
        f["artifact_code"] = ak == "code"
        f["artifact_test"] = ak == "test"
        f["artifact_config"] = ak == "config"
        f["artifact_doc"] = ak == "doc"
        f["artifact_build"] = ak == "build"

        # Locality signals
        f["seed_path_distance"] = cand.get("seed_path_distance", 999)
        f["same_package"] = cand.get("same_package", False)
        f["package_distance"] = cand.get("package_distance", 999)

        # Query features (same for all candidates in a group)
        f["query_len"] = query_features.get("query_len", 0)
        f["has_identifier"] = query_features.get("has_identifier", False)
        f["has_path"] = query_features.get("has_path", False)
        f["identifier_density"] = query_features.get("identifier_density", 0.0)
        f["term_count"] = query_features.get("term_count", 0)
        f["has_numbers"] = query_features.get("has_numbers", False)
        f["has_quoted_strings"] = query_features.get("has_quoted_strings", False)

        # Task intent one-hot
        intent = query_features.get("intent", "unknown")
        f["intent_debug"] = intent == "debug"
        f["intent_implement"] = intent == "implement"
        f["intent_refactor"] = intent == "refactor"
        f["intent_understand"] = intent == "understand"
        f["intent_test"] = intent == "test"

        # Stacktrace / test-driven flags
        f["is_stacktrace_driven"] = query_features.get("is_stacktrace_driven", False)
        f["is_test_driven"] = query_features.get("is_test_driven", False)

        features_out.append(f)

    return features_out


def extract_cutoff_features(
    ranked_candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
) -> dict[str, Any]:
    """Build query-level feature dict for the cutoff model.

    Computes score distribution features from the ranked list.

    Parameters
    ----------
    ranked_candidates
        Candidates sorted by ranker score descending.
    query_features
        Query-level features.
    repo_features
        Repo-level features (object_count, file_count).

    Returns
    -------
    dict
        Feature dict for cutoff prediction.
    """
    scores = [c.get("ranker_score", 0.0) for c in ranked_candidates]
    n = len(scores)

    f: dict[str, Any] = {}

    # Query features
    f["query_len"] = query_features.get("query_len", 0)
    f["has_identifier"] = query_features.get("has_identifier", False)
    f["has_path"] = query_features.get("has_path", False)
    f["has_numbers"] = query_features.get("has_numbers", False)
    f["has_quoted_strings"] = query_features.get("has_quoted_strings", False)
    f["is_stacktrace_driven"] = query_features.get("is_stacktrace_driven", False)
    f["is_test_driven"] = query_features.get("is_test_driven", False)

    # Repo features
    f["object_count"] = repo_features.get("object_count", 0)
    f["file_count"] = repo_features.get("file_count", 0)

    # Score distribution features
    f["total_candidates"] = n
    if n == 0:
        f["top_score"] = 0.0
        f["score_p10"] = 0.0
        f["score_p25"] = 0.0
        f["score_p50"] = 0.0
        f["score_p75"] = 0.0
        f["score_p90"] = 0.0
        f["max_gap"] = 0.0
        f["max_gap_position"] = 0.0
        f["score_variance"] = 0.0
        f["score_entropy"] = 0.0
        f["cumulative_mass_top10"] = 0.0
    else:
        f["top_score"] = scores[0]
        f["score_p10"] = scores[min(int(n * 0.1), n - 1)]
        f["score_p25"] = scores[min(int(n * 0.25), n - 1)]
        f["score_p50"] = scores[min(int(n * 0.5), n - 1)]
        f["score_p75"] = scores[min(int(n * 0.75), n - 1)]
        f["score_p90"] = scores[min(int(n * 0.9), n - 1)]

        # Max gap and its position
        gaps = [scores[i] - scores[i + 1] for i in range(n - 1)] if n > 1 else [0.0]
        f["max_gap"] = max(gaps)
        f["max_gap_position"] = (gaps.index(max(gaps)) + 1) / n if gaps else 0.0

        # Variance
        mean_s = sum(scores) / n
        f["score_variance"] = sum((s - mean_s) ** 2 for s in scores) / n

        # Entropy of score distribution
        total = sum(scores) if sum(scores) > 0 else 1.0
        probs = [s / total for s in scores if s > 0]
        f["score_entropy"] = -sum(p * math.log(p) for p in probs) if probs else 0.0

        # Cumulative mass in top 10
        top10_sum = sum(scores[:10])
        f["cumulative_mass_top10"] = top10_sum / total if total > 0 else 0.0

    # Multi-retriever agreement distribution
    hits = [c.get("retriever_hits", 0) for c in ranked_candidates]
    f["agreement_mean"] = sum(hits) / max(len(hits), 1)
    f["agreement_max"] = max(hits) if hits else 0

    return f


def extract_gate_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
) -> dict[str, Any]:
    """Build query-level feature dict for the gate classifier.

    Computes retrieval distribution features: top score, score decay
    profile, path entropy, cluster count, multi-retriever agreement.

    Parameters
    ----------
    candidates
        Raw candidate dicts from ``recon_raw_signals()``.
    query_features
        Query text features.
    repo_features
        Repo-level features.

    Returns
    -------
    dict
        Feature dict for gate classification.
    """
    f: dict[str, Any] = {}

    # Query features
    f["query_len"] = query_features.get("query_len", 0)
    f["identifier_density"] = query_features.get("identifier_density", 0.0)
    f["has_path"] = query_features.get("has_path", False)
    f["has_numbers"] = query_features.get("has_numbers", False)
    f["has_quoted_strings"] = query_features.get("has_quoted_strings", False)
    f["is_stacktrace_driven"] = query_features.get("is_stacktrace_driven", False)
    f["is_test_driven"] = query_features.get("is_test_driven", False)
    f["has_agent_seeds"] = any(
        c.get("symbol_source") == "agent_seed" for c in candidates
    )
    f["agent_seed_count"] = sum(
        1 for c in candidates if c.get("symbol_source") == "agent_seed"
    )

    # Repo features
    f["object_count"] = repo_features.get("object_count", 0)
    f["file_count"] = repo_features.get("file_count", 0)

    # Candidate pool features
    f["total_candidates"] = len(candidates)

    # Use retriever agreement as the score proxy for distribution features
    pool_scores = sorted(
        [c.get("retriever_hits", 0) for c in candidates], reverse=True,
    )
    n = len(pool_scores)

    if n == 0:
        f["top_score"] = 0.0
        f["score_p25"] = 0.0
        f["score_p50"] = 0.0
        f["score_p75"] = 0.0
        f["path_entropy"] = 0.0
        f["cluster_count"] = 0
    else:
        f["top_score"] = pool_scores[0]
        f["score_p25"] = pool_scores[min(int(n * 0.25), n - 1)]
        f["score_p50"] = pool_scores[min(int(n * 0.5), n - 1)]
        f["score_p75"] = pool_scores[min(int(n * 0.75), n - 1)]

        # Path entropy: Shannon entropy of directory distribution
        dirs = [c.get("parent_dir", "") for c in candidates if c.get("parent_dir")]
        dir_counts = Counter(dirs)
        total_dirs = sum(dir_counts.values())
        if total_dirs > 0:
            probs = [count / total_dirs for count in dir_counts.values()]
            f["path_entropy"] = -sum(p * math.log(p) for p in probs if p > 0)
        else:
            f["path_entropy"] = 0.0

        # Cluster count: number of distinct parent directories at depth 2
        depth2_dirs = set()
        for c in candidates:
            path = c.get("path", "")
            parts = path.split("/")
            if len(parts) >= 2:
                depth2_dirs.add("/".join(parts[:2]))
        f["cluster_count"] = len(depth2_dirs)

    # Multi-retriever agreement
    hits = [c.get("retriever_hits", 0) for c in candidates]
    f["agreement_mean"] = sum(hits) / max(len(hits), 1)
    f["agreement_std"] = (
        (sum((h - f["agreement_mean"]) ** 2 for h in hits) / max(len(hits), 1)) ** 0.5
        if hits
        else 0.0
    )

    return f


def extract_file_ranker_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Aggregate def-level candidates to file-level features for the file ranker.

    Groups candidates by file path and computes aggregate features
    matching the training-time ``_aggregate_file_features`` function.

    Returns
    -------
    tuple
        (file_features, file_to_candidates) where file_features is a list
        of per-file feature dicts and file_to_candidates maps each file path
        to its constituent def-level candidates.
    """
    # First compute per-candidate ranker features to get encoded booleans
    per_cand = extract_ranker_features(candidates, query_features)
    # Attach encoded features back to candidates
    enriched = [{**c, **pf} for c, pf in zip(candidates, per_cand)]

    # Group by file path
    file_to_candidates: dict[str, list[dict[str, Any]]] = {}
    for cand in enriched:
        path = cand.get("path", "")
        file_to_candidates.setdefault(path, []).append(cand)

    file_features: list[dict[str, Any]] = []
    for path, defs in file_to_candidates.items():
        f: dict[str, Any] = {"_path": path}

        # Term match
        f["max_term_match"] = max((d.get("term_match_count", 0) for d in defs), default=0)
        f["sum_term_matches"] = sum(d.get("term_total_matches", 0) for d in defs)
        f["max_lex_hits"] = max((d.get("lex_hit_count", 0) for d in defs), default=0)

        # Graph
        f["any_callee"] = any(d.get("graph_is_callee", False) for d in defs)
        f["any_caller"] = any(d.get("graph_is_caller", False) for d in defs)
        f["any_sibling"] = any(d.get("graph_is_sibling", False) for d in defs)
        f["any_doc_xref"] = any(d.get("graph_is_doc_xref", False) for d in defs)
        f["any_implementor"] = any(d.get("graph_is_implementor", False) for d in defs)
        f["best_graph_seed_rank"] = min((d.get("graph_seed_rank", 0) for d in defs), default=0)
        f["best_caller_tier"] = max((d.get("graph_caller_tier", 0) for d in defs), default=0)

        # Symbol
        f["any_agent_seed"] = any(d.get("sym_agent_seed", False) for d in defs)
        f["any_auto_seed"] = any(d.get("sym_auto_seed", False) for d in defs)
        f["any_task_extracted"] = any(d.get("sym_task_extracted", False) for d in defs)
        f["any_path_mention"] = any(d.get("sym_path_mention", False) for d in defs)

        # Import
        f["any_import_forward"] = any(d.get("import_forward", False) for d in defs)
        f["any_import_reverse"] = any(d.get("import_reverse", False) for d in defs)
        f["any_import_barrel"] = any(d.get("import_barrel", False) for d in defs)
        f["any_import_test_pair"] = any(d.get("import_test_pair", False) for d in defs)

        # Retriever agreement
        f["max_retriever_hits"] = max((d.get("retriever_hits", 0) for d in defs), default=0)
        f["sum_retriever_hits"] = sum(d.get("retriever_hits", 0) for d in defs)

        # File-level metadata
        f["num_defs_in_file"] = len(defs)
        hub_scores = [d.get("hub_score", 0) for d in defs]
        f["mean_hub_score"] = sum(hub_scores) / max(len(hub_scores), 1)
        f["max_hub_score"] = max(hub_scores, default=0)
        f["is_test"] = any(d.get("is_test", False) for d in defs)
        f["path_depth"] = defs[0].get("path_depth", 0)
        f["any_docstring"] = any(d.get("has_docstring", False) for d in defs)
        f["any_decorators"] = any(d.get("has_decorators", False) for d in defs)
        f["any_return_type"] = any(d.get("has_return_type", False) for d in defs)

        # Locality
        f["min_seed_path_distance"] = min(
            (d.get("seed_path_distance", 999) for d in defs), default=999
        )
        f["same_package"] = any(d.get("same_package", False) for d in defs)

        # Query features
        f["query_len"] = query_features.get("query_len", 0)
        f["has_identifier"] = query_features.get("has_identifier", False)
        f["has_path"] = query_features.get("has_path", False)
        f["term_count"] = query_features.get("term_count", 0)

        file_features.append(f)

    # Return original (un-enriched) candidate mapping for downstream
    raw_file_to_candidates: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        path = cand.get("path", "")
        raw_file_to_candidates.setdefault(path, []).append(cand)

    return file_features, raw_file_to_candidates
