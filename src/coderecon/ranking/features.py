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

from coderecon.ranking._feature_keys import (
    _K,
    ARTIFACT_KINDS,
    CALLER_TIER_ORDINALS,
    DEF_KINDS,
    GRAPH_EDGE_TYPES,
    IMPORT_DIRECTIONS,
    INTENT_TYPES,
    LANG_DATA_FAMILIES,
    LANG_FAMILIES,
    SYMBOL_SOURCES,
)
from coderecon.ranking._feature_types import (
    AggPair,
    CutoffFeatures,
    FieldSpec,
    FileRankerFeatures,
    GateFeatures,
    RankerFeatures,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _one_hot(
    prefix: str, value: str | None, categories: tuple[str, ...],
) -> dict[str, bool]:
    """Encode a categorical as ``{prefix}_{cat}: value == cat`` booleans."""
    return {f"{prefix}_{cat}": value == cat for cat in categories}


def _copy_fields(source: dict[str, Any], spec: FieldSpec) -> dict[str, Any]:
    """Copy fields from *source* using ``(key, default)`` pairs."""
    return {key: source.get(key, default) for key, default in spec}


def _copy_or(source: dict[str, Any], spec: FieldSpec) -> dict[str, Any]:
    """Like :func:`_copy_fields` but uses ``or`` to coerce falsy values."""
    return {key: source.get(key) or default for key, default in spec}


def _any_of(defs: list[dict[str, Any]], spec: AggPair) -> dict[str, bool]:
    """Aggregate booleans: ``out = any(d[inp] for d in defs)``."""
    return {out: any(d.get(inp, False) for d in defs) for out, inp in spec}


def _max_of(
    defs: list[dict[str, Any]], spec: tuple[tuple[str, str, Any], ...],
) -> dict[str, Any]:
    """Aggregate numerics: ``out = max(d[inp] for d in defs)``."""
    return {
        out: max((d.get(inp, default) for d in defs), default=default)
        for out, inp, default in spec
    }


def _sum_of(
    defs: list[dict[str, Any]], spec: tuple[tuple[str, str, Any], ...],
) -> dict[str, Any]:
    """Aggregate numerics: ``out = sum(d[inp] for d in defs)``."""
    return {out: sum(d.get(inp, default) for d in defs) for out, inp, default in spec}


# ---------------------------------------------------------------------------
# Per-model field specs — each model takes a different subset.
# ---------------------------------------------------------------------------

_RANKER_SCORE_FIELDS: FieldSpec = (
    (_K.TERM_MATCH_COUNT, 0), (_K.TERM_TOTAL_MATCHES, 0), (_K.LEX_HIT_COUNT, 0),
    (_K.BM25_FILE_SCORE, 0.0), (_K.SPLADE_SCORE, 0.0),
    (_K.CE_SCORE, 0.0), (_K.CE_SCORE_TINY, 0.0),
)

_RANKER_METADATA_FIELDS: FieldSpec = (
    (_K.OBJECT_SIZE_LINES, 0), (_K.PATH_DEPTH, 0), (_K.NESTING_DEPTH, 0),
    (_K.HUB_SCORE, 0), (_K.IS_TEST, False), (_K.IS_BARREL, False),
    (_K.IS_ENDPOINT, False), (_K.TEST_COVERAGE_COUNT, 0),
    (_K.HAS_DOCSTRING, False), (_K.HAS_DECORATORS, False),
    (_K.HAS_RETURN_TYPE, False), (_K.HAS_PARENT_SCOPE, False),
)

_RANKER_STRUCTURAL_FIELDS: FieldSpec = (
    (_K.SHARES_FILE_WITH_SEED, False), (_K.IS_CALLEE_OF_TOP, False),
    (_K.IS_IMPORTED_BY_TOP, False), (_K.FROM_COVERAGE, False),
    (_K.FROM_TERM_MATCH, False), (_K.FROM_EXPLICIT, False),
    (_K.FROM_GRAPH, False),
)

_RANKER_LOCALITY_FIELDS: FieldSpec = (
    (_K.SEED_PATH_DISTANCE, 999), (_K.SAME_PACKAGE, False),
    (_K.PACKAGE_DISTANCE, 999),
)

_RANKER_QUERY_FIELDS: FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.HAS_IDENTIFIER, False), (_K.HAS_PATH, False),
    (_K.IDENTIFIER_DENSITY, 0.0), (_K.TERM_COUNT, 0),
    (_K.HAS_NUMBERS, False), (_K.HAS_QUOTED_STRINGS, False),
    (_K.IS_STACKTRACE_DRIVEN, False), (_K.IS_TEST_DRIVEN, False),
)

_CUTOFF_QUERY_FIELDS: FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.HAS_IDENTIFIER, False), (_K.HAS_PATH, False),
    (_K.HAS_NUMBERS, False), (_K.HAS_QUOTED_STRINGS, False),
    (_K.IS_STACKTRACE_DRIVEN, False), (_K.IS_TEST_DRIVEN, False),
)

_GATE_QUERY_FIELDS: FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.IDENTIFIER_DENSITY, 0.0), (_K.HAS_PATH, False),
    (_K.HAS_NUMBERS, False), (_K.HAS_QUOTED_STRINGS, False),
    (_K.IS_STACKTRACE_DRIVEN, False), (_K.IS_TEST_DRIVEN, False),
)

_FILE_RANKER_QUERY_FIELDS: FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.HAS_IDENTIFIER, False), (_K.HAS_PATH, False),
    (_K.TERM_COUNT, 0),
)

_REPO_FIELDS: FieldSpec = (
    (_K.OBJECT_COUNT, 0), (_K.FILE_COUNT, 0),
)

# ---------------------------------------------------------------------------
# File ranker aggregation specs — (output_key, input_key[, default]).
# ---------------------------------------------------------------------------

_FILE_ANY_FIELDS: AggPair = (
    # Graph
    (_K.ANY_CALLEE, _K.GRAPH_IS_CALLEE),
    (_K.ANY_CALLER, _K.GRAPH_IS_CALLER),
    (_K.ANY_SIBLING, _K.GRAPH_IS_SIBLING),
    (_K.ANY_DOC_XREF, _K.GRAPH_IS_DOC_XREF),
    (_K.ANY_IMPLEMENTOR, _K.GRAPH_IS_IMPLEMENTOR),
    # Symbol
    (_K.ANY_AGENT_SEED, _K.SYM_AGENT_SEED),
    (_K.ANY_AUTO_SEED, _K.SYM_AUTO_SEED),
    (_K.ANY_TASK_EXTRACTED, _K.SYM_TASK_EXTRACTED),
    (_K.ANY_PATH_MENTION, _K.SYM_PATH_MENTION),
    # Import
    (_K.ANY_IMPORT_FORWARD, _K.IMPORT_FORWARD),
    (_K.ANY_IMPORT_REVERSE, _K.IMPORT_REVERSE),
    (_K.ANY_IMPORT_BARREL, _K.IMPORT_BARREL),
    (_K.ANY_IMPORT_TEST_PAIR, _K.IMPORT_TEST_PAIR),
    # Coverage / structural
    (_K.ANY_FROM_COVERAGE, _K.FROM_COVERAGE),
    (_K.ANY_SHARES_FILE_WITH_SEED, _K.SHARES_FILE_WITH_SEED),
    (_K.ANY_CALLEE_OF_TOP, _K.IS_CALLEE_OF_TOP),
    (_K.ANY_IMPORTED_BY_TOP, _K.IS_IMPORTED_BY_TOP),
    # File metadata
    (_K.IS_TEST, _K.IS_TEST),
    (_K.IS_BARREL, _K.IS_BARREL),
    (_K.ANY_DOCSTRING, _K.HAS_DOCSTRING),
    (_K.ANY_DECORATORS, _K.HAS_DECORATORS),
    (_K.ANY_RETURN_TYPE, _K.HAS_RETURN_TYPE),
    (_K.SAME_PACKAGE, _K.SAME_PACKAGE),
)

_FILE_MAX_FIELDS: tuple[tuple[str, str, Any], ...] = (
    (_K.MAX_TERM_MATCH, _K.TERM_MATCH_COUNT, 0),
    (_K.MAX_LEX_HITS, _K.LEX_HIT_COUNT, 0),
    (_K.MAX_BM25_FILE_SCORE, _K.BM25_FILE_SCORE, 0.0),
    (_K.BEST_CALLER_TIER, _K.GRAPH_CALLER_TIER, 0),
    (_K.MAX_RETRIEVER_HITS, _K.RETRIEVER_HITS, 0),
    (_K.MAX_RRF_SCORE, _K.RRF_SCORE, 0.0),
    (_K.MAX_SPLADE_SCORE, _K.SPLADE_SCORE, 0.0),
    (_K.MAX_HUB_SCORE, _K.HUB_SCORE, 0),
)

_FILE_SUM_FIELDS: tuple[tuple[str, str, Any], ...] = (
    (_K.SUM_TERM_MATCHES, _K.TERM_TOTAL_MATCHES, 0),
    (_K.SUM_RETRIEVER_HITS, _K.RETRIEVER_HITS, 0),
)


# ---------------------------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------------------------

def extract_ranker_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
) -> list[RankerFeatures]:
    """Build per-candidate feature dicts for the ranker model."""
    features_out: list[RankerFeatures] = []

    for cand in candidates:
        features: dict[str, Any] = _copy_or(cand, _RANKER_SCORE_FIELDS)

        # Language family one-hot
        lang = cand.get(_K.LANGUAGE_FAMILY, "")
        features.update(_one_hot(_K.PFX_LANG, lang, LANG_FAMILIES))
        features[_K.LANG_DATA] = lang in LANG_DATA_FAMILIES

        # Graph signal one-hot
        edge_type = cand.get(_K.GRAPH_EDGE_TYPE)
        features.update(_one_hot(_K.PFX_GRAPH_IS, edge_type, GRAPH_EDGE_TYPES))
        features[_K.GRAPH_SEED_RANK] = cand.get(_K.GRAPH_SEED_RANK) or 0
        features[_K.GRAPH_CALLER_TIER] = CALLER_TIER_ORDINALS.get(
            cand.get(_K.GRAPH_CALLER_MAX_TIER) or "", 0,
        )

        # Symbol source one-hot
        features.update(_one_hot(_K.PFX_SYM, cand.get(_K.SYMBOL_SOURCE), SYMBOL_SOURCES))

        # Import direction one-hot
        features.update(_one_hot(_K.PFX_IMPORT, cand.get(_K.IMPORT_DIRECTION), IMPORT_DIRECTIONS))
        features[_K.RETRIEVER_HITS] = cand.get(_K.RETRIEVER_HITS, 0)

        # Def kind one-hot
        features.update(_one_hot(_K.PFX_KIND, cand.get(_K.KIND, ""), DEF_KINDS))

        # Object metadata
        features.update(_copy_fields(cand, _RANKER_METADATA_FIELDS))
        features[_K.HAS_SIGNATURE] = cand.get(_K.SIGNATURE_TEXT) is not None

        # Structural links
        features.update(_copy_fields(cand, _RANKER_STRUCTURAL_FIELDS))

        # Derived
        matched_count = cand.get(_K.MATCHED_TERMS_COUNT, 0)
        total_terms = query_features.get(_K.TERM_COUNT, 0)
        features[_K.TERM_COVERAGE] = matched_count / total_terms if total_terms > 0 else 0.0
        features[_K.RRF_SCORE] = cand.get(_K.RRF_SCORE, 0.0)

        # Artifact kind one-hot
        features.update(_one_hot(
            _K.PFX_ARTIFACT, cand.get(_K.ARTIFACT_KIND, _K.VAL_CODE), ARTIFACT_KINDS,
        ))

        # Locality
        features.update(_copy_fields(cand, _RANKER_LOCALITY_FIELDS))

        # Query features (same for all candidates in a group)
        features.update(_copy_fields(query_features, _RANKER_QUERY_FIELDS))

        # Task intent one-hot
        features.update(_one_hot(
            _K.PFX_INTENT, query_features.get(_K.INTENT, _K.VAL_UNKNOWN), INTENT_TYPES,
        ))

        features_out.append(features)  # type: ignore[arg-type]

    return features_out


def extract_cutoff_features(
    ranked_candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
) -> CutoffFeatures:
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
    """
    scores = [c.get(_K.RANKER_SCORE, 0.0) for c in ranked_candidates]
    n = len(scores)

    features: dict[str, Any] = _copy_fields(query_features, _CUTOFF_QUERY_FIELDS)
    features.update(_copy_fields(repo_features, _REPO_FIELDS))

    # Score distribution features
    features[_K.TOTAL_CANDIDATES] = n
    if n == 0:
        features[_K.TOP_SCORE] = 0.0
        features[_K.SCORE_P10] = 0.0
        features[_K.SCORE_P25] = 0.0
        features[_K.SCORE_P50] = 0.0
        features[_K.SCORE_P75] = 0.0
        features[_K.SCORE_P90] = 0.0
        features[_K.MAX_GAP] = 0.0
        features[_K.MAX_GAP_POS] = 0.0
        features[_K.SCORE_VAR] = 0.0
        features[_K.SCORE_ENTROPY] = 0.0
        features[_K.CUMULATIVE_MASS_TOP10] = 0.0
    else:
        features[_K.TOP_SCORE] = scores[0]
        features[_K.SCORE_P10] = scores[min(int(n * 0.1), n - 1)]
        features[_K.SCORE_P25] = scores[min(int(n * 0.25), n - 1)]
        features[_K.SCORE_P50] = scores[min(int(n * 0.5), n - 1)]
        features[_K.SCORE_P75] = scores[min(int(n * 0.75), n - 1)]
        features[_K.SCORE_P90] = scores[min(int(n * 0.9), n - 1)]

        # Max gap and its position
        gaps = [scores[i] - scores[i + 1] for i in range(n - 1)] if n > 1 else [0.0]
        features[_K.MAX_GAP] = max(gaps)
        features[_K.MAX_GAP_POS] = (gaps.index(max(gaps)) + 1) / n if gaps else 0.0

        # Variance
        mean_s = sum(scores) / n
        features[_K.SCORE_VAR] = sum((s - mean_s) ** 2 for s in scores) / n

        # Entropy of score distribution
        total = sum(scores) if sum(scores) > 0 else 1.0
        probs = [s / total for s in scores if s > 0]
        features[_K.SCORE_ENTROPY] = -sum(p * math.log(p) for p in probs) if probs else 0.0

        # Cumulative mass in top 10
        top10_sum = sum(scores[:10])
        features[_K.CUMULATIVE_MASS_TOP10] = top10_sum / total if total > 0 else 0.0

    # Multi-retriever agreement distribution
    hits = [c.get(_K.RETRIEVER_HITS, 0) for c in ranked_candidates]
    features[_K.AGREEMENT_MEAN] = sum(hits) / max(len(hits), 1)
    features[_K.AGREEMENT_MAX] = max(hits) if hits else 0

    # Retriever composition: what fraction of ranked candidates came from each source
    n_ranked = max(len(ranked_candidates), 1)
    features[_K.FRACTION_FROM_TERM] = sum(
        1 for c in ranked_candidates if c.get(_K.FROM_TERM_MATCH)
    ) / n_ranked
    features[_K.FRACTION_FROM_GRAPH] = sum(
        1 for c in ranked_candidates if c.get(_K.FROM_GRAPH)
    ) / n_ranked
    features[_K.FRACTION_FROM_EXPLICIT] = sum(
        1 for c in ranked_candidates if c.get(_K.FROM_EXPLICIT)
    ) / n_ranked
    features[_K.FRACTION_FROM_SPLADE] = sum(
        1 for c in ranked_candidates if c.get(_K.SPLADE_SCORE, 0) > 0
    ) / n_ranked

    return features  # type: ignore[return-value]


def extract_gate_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
) -> GateFeatures:
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
    """
    features: dict[str, Any] = _copy_fields(query_features, _GATE_QUERY_FIELDS)
    features[_K.HAS_AGENT_SEEDS] = any(
        c.get(_K.SYMBOL_SOURCE) == _K.VAL_AGENT_SEED for c in candidates
    )
    features[_K.AGENT_SEED_COUNT] = sum(
        1 for c in candidates if c.get(_K.SYMBOL_SOURCE) == _K.VAL_AGENT_SEED
    )

    features.update(_copy_fields(repo_features, _REPO_FIELDS))

    # Candidate pool features
    features[_K.TOTAL_CANDIDATES] = len(candidates)

    # Use retriever agreement as the score proxy for distribution features
    pool_scores = sorted(
        [c.get(_K.RETRIEVER_HITS, 0) for c in candidates], reverse=True,
    )
    n = len(pool_scores)

    if n == 0:
        features[_K.TOP_SCORE] = 0.0
        features[_K.SCORE_P25] = 0.0
        features[_K.SCORE_P50] = 0.0
        features[_K.SCORE_P75] = 0.0
        features[_K.PATH_ENTROPY] = 0.0
        features[_K.CLUSTER_COUNT] = 0
        features[_K.MAX_SPLADE_SCORE] = 0.0
        features[_K.MAX_BM25_SCORE] = 0.0
        features[_K.HAS_GRAPH_CANDIDATES] = False
        features[_K.HAS_EXPLICIT_CANDIDATES] = False
    else:
        features[_K.TOP_SCORE] = pool_scores[0]
        features[_K.SCORE_P25] = pool_scores[min(int(n * 0.25), n - 1)]
        features[_K.SCORE_P50] = pool_scores[min(int(n * 0.5), n - 1)]
        features[_K.SCORE_P75] = pool_scores[min(int(n * 0.75), n - 1)]

        # Continuous score peaks from best retrievers
        features[_K.MAX_SPLADE_SCORE] = max(
            (c.get(_K.SPLADE_SCORE, 0.0) for c in candidates), default=0.0,
        )
        features[_K.MAX_BM25_SCORE] = max(
            (c.get(_K.BM25_FILE_SCORE, 0.0) for c in candidates), default=0.0,
        )
        features[_K.HAS_GRAPH_CANDIDATES] = any(
            c.get(_K.GRAPH_EDGE_TYPE) is not None for c in candidates
        )
        features[_K.HAS_EXPLICIT_CANDIDATES] = any(
            c.get(_K.SYMBOL_SOURCE) is not None for c in candidates
        )

        # Path entropy: Shannon entropy of directory distribution
        dirs = [c.get(_K.PARENT_DIR, "") for c in candidates if c.get(_K.PARENT_DIR)]
        dir_counts = Counter(dirs)
        total_dirs = sum(dir_counts.values())
        if total_dirs > 0:
            probs = [count / total_dirs for count in dir_counts.values()]
            features[_K.PATH_ENTROPY] = -sum(p * math.log(p) for p in probs if p > 0)
        else:
            features[_K.PATH_ENTROPY] = 0.0

        # Cluster count: number of distinct parent directories at depth 2
        depth2_dirs = set()
        for c in candidates:
            path = c.get(_K.PATH, "")
            parts = path.split("/")
            if len(parts) >= 2:
                depth2_dirs.add("/".join(parts[:2]))
        features[_K.CLUSTER_COUNT] = len(depth2_dirs)

    # Multi-retriever agreement
    hits = [c.get(_K.RETRIEVER_HITS, 0) for c in candidates]
    features[_K.AGREEMENT_MEAN] = sum(hits) / max(len(hits), 1)
    features[_K.AGREEMENT_STD] = (
        (sum((h - features[_K.AGREEMENT_MEAN]) ** 2 for h in hits) / max(len(hits), 1)) ** 0.5
        if hits
        else 0.0
    )

    return features  # type: ignore[return-value]


def extract_file_ranker_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
) -> tuple[list[FileRankerFeatures], dict[str, list[dict[str, Any]]]]:
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
    enriched = [{**c, **pf} for c, pf in zip(candidates, per_cand, strict=True)]

    # Group by file path
    file_to_candidates: dict[str, list[dict[str, Any]]] = {}
    for cand in enriched:
        path = cand.get(_K.PATH, "")
        file_to_candidates.setdefault(path, []).append(cand)

    file_features: list[FileRankerFeatures] = []
    for path, defs in file_to_candidates.items():
        features: dict[str, Any] = {_K.FILE_PATH: path}

        # Boolean aggregations (any def in file has this signal)
        features.update(_any_of(defs, _FILE_ANY_FIELDS))

        # Numeric aggregations
        features.update(_max_of(defs, _FILE_MAX_FIELDS))
        features.update(_sum_of(defs, _FILE_SUM_FIELDS))

        # Graph seed rank — best (minimum) rank
        features[_K.BEST_GRAPH_SEED_RANK] = min(
            (d.get(_K.GRAPH_SEED_RANK, 0) for d in defs), default=0,
        )

        # TinyBERT cross-encoder (all defs, pre-file-prune)
        ce_tiny_scores = [d.get(_K.CE_SCORE_TINY, 0.0) for d in defs]
        features[_K.MAX_CE_TINY] = max(ce_tiny_scores, default=0.0)
        features[_K.MEAN_CE_TINY] = sum(ce_tiny_scores) / max(len(ce_tiny_scores), 1)

        # Artifact kind
        features[_K.ANY_ARTIFACT_TEST] = any(
            d.get(_K.ARTIFACT_KIND) == _K.VAL_TEST for d in defs
        )
        features[_K.ANY_ARTIFACT_CONFIG] = any(
            d.get(_K.ARTIFACT_KIND) == _K.VAL_CONFIG for d in defs
        )
        features[_K.ANY_ARTIFACT_DOC] = any(
            d.get(_K.ARTIFACT_KIND) == _K.VAL_DOC for d in defs
        )

        # File-level metadata
        features[_K.NUM_DEFS_IN_FILE] = len(defs)
        hub_scores = [d.get(_K.HUB_SCORE, 0) for d in defs]
        features[_K.MEAN_HUB_SCORE] = sum(hub_scores) / max(len(hub_scores), 1)
        features[_K.PATH_DEPTH] = defs[0].get(_K.PATH_DEPTH, 0)  # all defs share the same file

        # Locality
        features[_K.MIN_SEED_PATH_DISTANCE] = min(
            (d.get(_K.SEED_PATH_DISTANCE, 999) for d in defs), default=999,
        )

        # Query features
        features.update(_copy_fields(query_features, _FILE_RANKER_QUERY_FIELDS))

        file_features.append(features)  # type: ignore[arg-type]

    # Return original (un-enriched) candidate mapping for downstream
    raw_file_to_candidates: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        path = cand.get(_K.PATH, "")
        raw_file_to_candidates.setdefault(path, []).append(cand)

    return file_features, raw_file_to_candidates
