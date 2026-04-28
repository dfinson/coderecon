"""Feature extraction from raw retrieval signals.

Converts the candidate pool returned by ``recon_raw_signals()`` into
feature matrices suitable for the LightGBM ranker, cutoff, and gate
models.

See §2.1 of recon-lab/README.md for feature definitions.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Key constants — single source of truth for all dict key access.
# Renaming any value breaks trained LightGBM models.
# ---------------------------------------------------------------------------

class _K:
    """String constants for candidate signal keys, feature output keys,
    query/repo keys, one-hot prefixes, and categorical comparison values.
    """

    __slots__ = ()

    # -- One-hot encoding prefixes --
    PFX_LANG = "lang"
    PFX_GRAPH_IS = "graph_is"
    PFX_SYM = "sym"
    PFX_IMPORT = "import"
    PFX_KIND = "kind"
    PFX_ARTIFACT = "artifact"
    PFX_INTENT = "intent"

    # -- Categorical comparison values --
    VAL_AGENT_SEED = "agent_seed"
    VAL_CODE = "code"
    VAL_UNKNOWN = "unknown"
    VAL_TEST = "test"
    VAL_CONFIG = "config"
    VAL_DOC = "doc"

    # -- Candidate / signal input keys --
    LANGUAGE_FAMILY = "language_family"
    GRAPH_EDGE_TYPE = "graph_edge_type"
    GRAPH_SEED_RANK = "graph_seed_rank"
    GRAPH_CALLER_MAX_TIER = "graph_caller_max_tier"
    SYMBOL_SOURCE = "symbol_source"
    IMPORT_DIRECTION = "import_direction"
    KIND = "kind"
    SIGNATURE_TEXT = "signature_text"
    MATCHED_TERMS_COUNT = "matched_terms_count"
    ARTIFACT_KIND = "artifact_kind"
    RANKER_SCORE = "ranker_score"
    PARENT_DIR = "parent_dir"
    PATH = "path"
    INTENT = "intent"

    # -- Passthrough keys (same in candidate input and feature output) --
    TERM_MATCH_COUNT = "term_match_count"
    TERM_TOTAL_MATCHES = "term_total_matches"
    LEX_HIT_COUNT = "lex_hit_count"
    BM25_FILE_SCORE = "bm25_file_score"
    SPLADE_SCORE = "splade_score"
    CE_SCORE = "ce_score"
    CE_SCORE_TINY = "ce_score_tiny"
    RETRIEVER_HITS = "retriever_hits"
    RRF_SCORE = "rrf_score"
    OBJECT_SIZE_LINES = "object_size_lines"
    PATH_DEPTH = "path_depth"
    NESTING_DEPTH = "nesting_depth"
    HUB_SCORE = "hub_score"
    IS_TEST = "is_test"
    IS_BARREL = "is_barrel"
    IS_ENDPOINT = "is_endpoint"
    TEST_COVERAGE_COUNT = "test_coverage_count"
    HAS_DOCSTRING = "has_docstring"
    HAS_DECORATORS = "has_decorators"
    HAS_RETURN_TYPE = "has_return_type"
    HAS_PARENT_SCOPE = "has_parent_scope"
    SHARES_FILE_WITH_SEED = "shares_file_with_seed"
    IS_CALLEE_OF_TOP = "is_callee_of_top"
    IS_IMPORTED_BY_TOP = "is_imported_by_top"
    FROM_COVERAGE = "from_coverage"
    FROM_TERM_MATCH = "from_term_match"
    FROM_EXPLICIT = "from_explicit"
    FROM_GRAPH = "from_graph"
    SEED_PATH_DISTANCE = "seed_path_distance"
    SAME_PACKAGE = "same_package"
    PACKAGE_DISTANCE = "package_distance"

    # -- Query / repo keys --
    QUERY_LEN = "query_len"
    HAS_IDENTIFIER = "has_identifier"
    HAS_PATH = "has_path"
    IDENTIFIER_DENSITY = "identifier_density"
    TERM_COUNT = "term_count"
    HAS_NUMBERS = "has_numbers"
    HAS_QUOTED_STRINGS = "has_quoted_strings"
    IS_STACKTRACE_DRIVEN = "is_stacktrace_driven"
    IS_TEST_DRIVEN = "is_test_driven"
    OBJECT_COUNT = "object_count"
    FILE_COUNT = "file_count"

    # -- One-hot encoded keys (produced by _one_hot, consumed by file ranker) --
    GRAPH_IS_CALLEE = "graph_is_callee"
    GRAPH_IS_CALLER = "graph_is_caller"
    GRAPH_IS_SIBLING = "graph_is_sibling"
    GRAPH_IS_DOC_XREF = "graph_is_doc_xref"
    GRAPH_IS_IMPLEMENTOR = "graph_is_implementor"
    SYM_AGENT_SEED = "sym_agent_seed"
    SYM_AUTO_SEED = "sym_auto_seed"
    SYM_TASK_EXTRACTED = "sym_task_extracted"
    SYM_PATH_MENTION = "sym_path_mention"
    IMPORT_FORWARD = "import_forward"
    IMPORT_REVERSE = "import_reverse"
    IMPORT_BARREL = "import_barrel"
    IMPORT_TEST_PAIR = "import_test_pair"

    # -- Ranker-only output keys --
    LANG_DATA = "lang_data"
    GRAPH_CALLER_TIER = "graph_caller_tier"
    HAS_SIGNATURE = "has_signature"
    TERM_COVERAGE = "term_coverage"

    # -- Score distribution output keys (cutoff / gate) --
    TOTAL_CANDIDATES = "total_candidates"
    TOP_SCORE = "top_score"
    SCORE_P10 = "score_p10"
    SCORE_P25 = "score_p25"
    SCORE_P50 = "score_p50"
    SCORE_P75 = "score_p75"
    SCORE_P90 = "score_p90"
    MAX_GAP = "max_gap"
    MAX_GAP_POS = "max_gap_pos"
    SCORE_VAR = "score_var"
    SCORE_ENTROPY = "score_entropy"
    CUMULATIVE_MASS_TOP10 = "cumulative_mass_top10"
    AGREEMENT_MEAN = "agreement_mean"
    AGREEMENT_MAX = "agreement_max"
    AGREEMENT_STD = "agreement_std"
    FRACTION_FROM_TERM = "fraction_from_term"
    FRACTION_FROM_GRAPH = "fraction_from_graph"
    FRACTION_FROM_EXPLICIT = "fraction_from_explicit"
    FRACTION_FROM_SPLADE = "fraction_from_splade"

    # -- Gate-only output keys --
    HAS_AGENT_SEEDS = "has_agent_seeds"
    AGENT_SEED_COUNT = "agent_seed_count"
    PATH_ENTROPY = "path_entropy"
    CLUSTER_COUNT = "cluster_count"
    MAX_SPLADE_SCORE = "max_splade_score"
    MAX_BM25_SCORE = "max_bm25_score"
    HAS_GRAPH_CANDIDATES = "has_graph_candidates"
    HAS_EXPLICIT_CANDIDATES = "has_explicit_candidates"

    # -- File ranker aggregation output keys --
    FILE_PATH = "_path"
    ANY_CALLEE = "any_callee"
    ANY_CALLER = "any_caller"
    ANY_SIBLING = "any_sibling"
    ANY_DOC_XREF = "any_doc_xref"
    ANY_IMPLEMENTOR = "any_implementor"
    ANY_AGENT_SEED = "any_agent_seed"
    ANY_AUTO_SEED = "any_auto_seed"
    ANY_TASK_EXTRACTED = "any_task_extracted"
    ANY_PATH_MENTION = "any_path_mention"
    ANY_IMPORT_FORWARD = "any_import_forward"
    ANY_IMPORT_REVERSE = "any_import_reverse"
    ANY_IMPORT_BARREL = "any_import_barrel"
    ANY_IMPORT_TEST_PAIR = "any_import_test_pair"
    ANY_FROM_COVERAGE = "any_from_coverage"
    ANY_SHARES_FILE_WITH_SEED = "any_shares_file_with_seed"
    ANY_CALLEE_OF_TOP = "any_callee_of_top"
    ANY_IMPORTED_BY_TOP = "any_imported_by_top"
    ANY_DOCSTRING = "any_docstring"
    ANY_DECORATORS = "any_decorators"
    ANY_RETURN_TYPE = "any_return_type"
    MAX_TERM_MATCH = "max_term_match"
    SUM_TERM_MATCHES = "sum_term_matches"
    MAX_LEX_HITS = "max_lex_hits"
    MAX_BM25_FILE_SCORE = "max_bm25_file_score"
    BEST_GRAPH_SEED_RANK = "best_graph_seed_rank"
    BEST_CALLER_TIER = "best_caller_tier"
    MAX_RETRIEVER_HITS = "max_retriever_hits"
    SUM_RETRIEVER_HITS = "sum_retriever_hits"
    MAX_RRF_SCORE = "max_rrf_score"
    MAX_HUB_SCORE = "max_hub_score"
    MAX_CE_TINY = "max_ce_tiny"
    MEAN_CE_TINY = "mean_ce_tiny"
    ANY_ARTIFACT_TEST = "any_artifact_test"
    ANY_ARTIFACT_CONFIG = "any_artifact_config"
    ANY_ARTIFACT_DOC = "any_artifact_doc"
    NUM_DEFS_IN_FILE = "num_defs_in_file"
    MEAN_HUB_SCORE = "mean_hub_score"
    MIN_SEED_PATH_DISTANCE = "min_seed_path_distance"


# ---------------------------------------------------------------------------
# Category constants — single source of truth for one-hot encodings.
# Key names produced: ``{prefix}_{category}`` (must match trained models).
# ---------------------------------------------------------------------------

_LANG_FAMILIES: tuple[str, ...] = (
    "python", "javascript", "go", "rust", "java", "csharp",
    "c_cpp", "ruby", "php", "swift", "kotlin",
)
_LANG_DATA_FAMILIES: frozenset[str] = frozenset(("json", "yaml", "toml", "xml"))

_GRAPH_EDGE_TYPES: tuple[str, ...] = (
    "callee", "caller", "sibling", "implementor", "doc_xref",
)
_SYMBOL_SOURCES: tuple[str, ...] = (
    "agent_seed", "auto_seed", "task_extracted", "path_mention", "pin",
)
_IMPORT_DIRECTIONS: tuple[str, ...] = ("forward", "reverse", "barrel", "test_pair")
_DEF_KINDS: tuple[str, ...] = ("function", "class", "method", "variable", "interface")
_ARTIFACT_KINDS: tuple[str, ...] = ("code", "test", "config", "doc", "build")
_INTENT_TYPES: tuple[str, ...] = ("debug", "implement", "refactor", "understand", "test")

_CALLER_TIER_ORDINALS: dict[str, int] = {
    "proven": 3, "strong": 2, "anchored": 1, "unknown": 0,
}


# ---------------------------------------------------------------------------
# Helpers and field spec types
# ---------------------------------------------------------------------------

# (key, default) field specs — passthrough copy from source dict.
_FieldSpec = tuple[tuple[str, Any], ...]
# (output_key, input_key) — aggregation mapping for file ranker.
_AggPair = tuple[tuple[str, str], ...]


def _one_hot(
    prefix: str, value: str | None, categories: tuple[str, ...],
) -> dict[str, bool]:
    """Encode a categorical as ``{prefix}_{cat}: value == cat`` booleans."""
    return {f"{prefix}_{cat}": value == cat for cat in categories}


def _copy_fields(source: dict[str, Any], spec: _FieldSpec) -> dict[str, Any]:
    """Copy fields from *source* using ``(key, default)`` pairs."""
    return {key: source.get(key, default) for key, default in spec}


def _copy_or(source: dict[str, Any], spec: _FieldSpec) -> dict[str, Any]:
    """Like :func:`_copy_fields` but uses ``or`` to coerce falsy values."""
    return {key: source.get(key) or default for key, default in spec}


def _any_of(defs: list[dict[str, Any]], spec: _AggPair) -> dict[str, bool]:
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

_RANKER_SCORE_FIELDS: _FieldSpec = (
    (_K.TERM_MATCH_COUNT, 0), (_K.TERM_TOTAL_MATCHES, 0), (_K.LEX_HIT_COUNT, 0),
    (_K.BM25_FILE_SCORE, 0.0), (_K.SPLADE_SCORE, 0.0),
    (_K.CE_SCORE, 0.0), (_K.CE_SCORE_TINY, 0.0),
)

_RANKER_METADATA_FIELDS: _FieldSpec = (
    (_K.OBJECT_SIZE_LINES, 0), (_K.PATH_DEPTH, 0), (_K.NESTING_DEPTH, 0),
    (_K.HUB_SCORE, 0), (_K.IS_TEST, False), (_K.IS_BARREL, False),
    (_K.IS_ENDPOINT, False), (_K.TEST_COVERAGE_COUNT, 0),
    (_K.HAS_DOCSTRING, False), (_K.HAS_DECORATORS, False),
    (_K.HAS_RETURN_TYPE, False), (_K.HAS_PARENT_SCOPE, False),
)

_RANKER_STRUCTURAL_FIELDS: _FieldSpec = (
    (_K.SHARES_FILE_WITH_SEED, False), (_K.IS_CALLEE_OF_TOP, False),
    (_K.IS_IMPORTED_BY_TOP, False), (_K.FROM_COVERAGE, False),
    (_K.FROM_TERM_MATCH, False), (_K.FROM_EXPLICIT, False),
    (_K.FROM_GRAPH, False),
)

_RANKER_LOCALITY_FIELDS: _FieldSpec = (
    (_K.SEED_PATH_DISTANCE, 999), (_K.SAME_PACKAGE, False),
    (_K.PACKAGE_DISTANCE, 999),
)

_RANKER_QUERY_FIELDS: _FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.HAS_IDENTIFIER, False), (_K.HAS_PATH, False),
    (_K.IDENTIFIER_DENSITY, 0.0), (_K.TERM_COUNT, 0),
    (_K.HAS_NUMBERS, False), (_K.HAS_QUOTED_STRINGS, False),
    (_K.IS_STACKTRACE_DRIVEN, False), (_K.IS_TEST_DRIVEN, False),
)

_CUTOFF_QUERY_FIELDS: _FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.HAS_IDENTIFIER, False), (_K.HAS_PATH, False),
    (_K.HAS_NUMBERS, False), (_K.HAS_QUOTED_STRINGS, False),
    (_K.IS_STACKTRACE_DRIVEN, False), (_K.IS_TEST_DRIVEN, False),
)

_GATE_QUERY_FIELDS: _FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.IDENTIFIER_DENSITY, 0.0), (_K.HAS_PATH, False),
    (_K.HAS_NUMBERS, False), (_K.HAS_QUOTED_STRINGS, False),
    (_K.IS_STACKTRACE_DRIVEN, False), (_K.IS_TEST_DRIVEN, False),
)

_FILE_RANKER_QUERY_FIELDS: _FieldSpec = (
    (_K.QUERY_LEN, 0), (_K.HAS_IDENTIFIER, False), (_K.HAS_PATH, False),
    (_K.TERM_COUNT, 0),
)

_REPO_FIELDS: _FieldSpec = (
    (_K.OBJECT_COUNT, 0), (_K.FILE_COUNT, 0),
)

# ---------------------------------------------------------------------------
# File ranker aggregation specs — (output_key, input_key[, default]).
# ---------------------------------------------------------------------------

_FILE_ANY_FIELDS: _AggPair = (
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
# TypedDict contracts — one per LightGBM model.
# Key names are the model's ``feature_name()`` list; renaming breaks inference.
# ---------------------------------------------------------------------------

class RankerFeatures(TypedDict):
    """Per-candidate feature vector for the LambdaMART def ranker."""

    # Term match
    term_match_count: int
    term_total_matches: int
    lex_hit_count: int
    bm25_file_score: float
    splade_score: float
    ce_score: float
    ce_score_tiny: float
    # Language one-hot
    lang_python: bool
    lang_javascript: bool
    lang_go: bool
    lang_rust: bool
    lang_java: bool
    lang_csharp: bool
    lang_c_cpp: bool
    lang_ruby: bool
    lang_php: bool
    lang_swift: bool
    lang_kotlin: bool
    lang_data: bool
    # Graph
    graph_is_callee: bool
    graph_is_caller: bool
    graph_is_sibling: bool
    graph_is_implementor: bool
    graph_is_doc_xref: bool
    graph_seed_rank: int
    graph_caller_tier: int
    # Symbol source
    sym_agent_seed: bool
    sym_auto_seed: bool
    sym_task_extracted: bool
    sym_path_mention: bool
    sym_pin: bool
    # Import direction
    import_forward: bool
    import_reverse: bool
    import_barrel: bool
    import_test_pair: bool
    retriever_hits: int
    # Def kind
    kind_function: bool
    kind_class: bool
    kind_method: bool
    kind_variable: bool
    kind_interface: bool
    # Object metadata
    object_size_lines: int
    path_depth: int
    nesting_depth: int
    hub_score: int
    is_test: bool
    is_barrel: bool
    is_endpoint: bool
    test_coverage_count: int
    has_docstring: bool
    has_decorators: bool
    has_return_type: bool
    has_parent_scope: bool
    has_signature: bool
    # Structural links
    shares_file_with_seed: bool
    is_callee_of_top: bool
    is_imported_by_top: bool
    from_coverage: bool
    from_term_match: bool
    from_explicit: bool
    from_graph: bool
    # Derived
    term_coverage: float
    rrf_score: float
    # Artifact kind
    artifact_code: bool
    artifact_test: bool
    artifact_config: bool
    artifact_doc: bool
    artifact_build: bool
    # Locality
    seed_path_distance: int
    same_package: bool
    package_distance: int
    # Query
    query_len: int
    has_identifier: bool
    has_path: bool
    identifier_density: float
    term_count: int
    has_numbers: bool
    has_quoted_strings: bool
    # Intent
    intent_debug: bool
    intent_implement: bool
    intent_refactor: bool
    intent_understand: bool
    intent_test: bool
    is_stacktrace_driven: bool
    is_test_driven: bool


class CutoffFeatures(TypedDict):
    """Query-level feature vector for the cutoff regressor."""

    # Query
    query_len: int
    has_identifier: bool
    has_path: bool
    has_numbers: bool
    has_quoted_strings: bool
    is_stacktrace_driven: bool
    is_test_driven: bool
    # Repo
    object_count: int
    file_count: int
    # Score distribution
    total_candidates: int
    top_score: float
    score_p10: float
    score_p25: float
    score_p50: float
    score_p75: float
    score_p90: float
    max_gap: float
    max_gap_pos: float
    score_var: float
    score_entropy: float
    cumulative_mass_top10: float
    # Retriever agreement
    agreement_mean: float
    agreement_max: int
    fraction_from_term: float
    fraction_from_graph: float
    fraction_from_explicit: float
    fraction_from_splade: float


class GateFeatures(TypedDict):
    """Query-level feature vector for the gate classifier."""

    # Query
    query_len: int
    identifier_density: float
    has_path: bool
    has_numbers: bool
    has_quoted_strings: bool
    is_stacktrace_driven: bool
    is_test_driven: bool
    has_agent_seeds: bool
    agent_seed_count: int
    # Repo
    object_count: int
    file_count: int
    # Pool
    total_candidates: int
    top_score: float
    score_p25: float
    score_p50: float
    score_p75: float
    max_splade_score: float
    max_bm25_score: float
    has_graph_candidates: bool
    has_explicit_candidates: bool
    path_entropy: float
    cluster_count: int
    # Agreement
    agreement_mean: float
    agreement_std: float


class FileRankerFeatures(TypedDict):
    """Per-file aggregated feature vector for the file ranker."""

    _path: str
    # Term match aggregates
    max_term_match: int
    sum_term_matches: int
    max_lex_hits: int
    max_bm25_file_score: float
    # Graph
    any_callee: bool
    any_caller: bool
    any_sibling: bool
    any_doc_xref: bool
    any_implementor: bool
    best_graph_seed_rank: int
    best_caller_tier: int
    # Symbol
    any_agent_seed: bool
    any_auto_seed: bool
    any_task_extracted: bool
    any_path_mention: bool
    # Import
    any_import_forward: bool
    any_import_reverse: bool
    any_import_barrel: bool
    any_import_test_pair: bool
    # Retriever agreement
    max_retriever_hits: int
    sum_retriever_hits: int
    # Coverage / structural
    any_from_coverage: bool
    any_shares_file_with_seed: bool
    any_callee_of_top: bool
    any_imported_by_top: bool
    # Scores
    max_rrf_score: float
    max_splade_score: float
    max_ce_tiny: float
    mean_ce_tiny: float
    # Artifact kind
    any_artifact_test: bool
    any_artifact_config: bool
    any_artifact_doc: bool
    # File metadata
    num_defs_in_file: int
    mean_hub_score: float
    max_hub_score: int
    is_test: bool
    is_barrel: bool
    path_depth: int
    any_docstring: bool
    any_decorators: bool
    any_return_type: bool
    # Locality
    min_seed_path_distance: int
    same_package: bool
    # Query
    query_len: int
    has_identifier: bool
    has_path: bool
    term_count: int


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
        features.update(_one_hot(_K.PFX_LANG, lang, _LANG_FAMILIES))
        features[_K.LANG_DATA] = lang in _LANG_DATA_FAMILIES

        # Graph signal one-hot
        edge_type = cand.get(_K.GRAPH_EDGE_TYPE)
        features.update(_one_hot(_K.PFX_GRAPH_IS, edge_type, _GRAPH_EDGE_TYPES))
        features[_K.GRAPH_SEED_RANK] = cand.get(_K.GRAPH_SEED_RANK) or 0
        features[_K.GRAPH_CALLER_TIER] = _CALLER_TIER_ORDINALS.get(
            cand.get(_K.GRAPH_CALLER_MAX_TIER) or "", 0,
        )

        # Symbol source one-hot
        features.update(_one_hot(_K.PFX_SYM, cand.get(_K.SYMBOL_SOURCE), _SYMBOL_SOURCES))

        # Import direction one-hot
        features.update(_one_hot(_K.PFX_IMPORT, cand.get(_K.IMPORT_DIRECTION), _IMPORT_DIRECTIONS))
        features[_K.RETRIEVER_HITS] = cand.get(_K.RETRIEVER_HITS, 0)

        # Def kind one-hot
        features.update(_one_hot(_K.PFX_KIND, cand.get(_K.KIND, ""), _DEF_KINDS))

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
            _K.PFX_ARTIFACT, cand.get(_K.ARTIFACT_KIND, _K.VAL_CODE), _ARTIFACT_KINDS,
        ))

        # Locality
        features.update(_copy_fields(cand, _RANKER_LOCALITY_FIELDS))

        # Query features (same for all candidates in a group)
        features.update(_copy_fields(query_features, _RANKER_QUERY_FIELDS))

        # Task intent one-hot
        features.update(_one_hot(
            _K.PFX_INTENT, query_features.get(_K.INTENT, _K.VAL_UNKNOWN), _INTENT_TYPES,
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
