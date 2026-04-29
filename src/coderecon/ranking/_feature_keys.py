"""String constants and categorical definitions for feature extraction.

Every dict key used across the four LightGBM feature extractors is
defined here exactly once.  Renaming any value breaks trained models.
"""

from __future__ import annotations


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

LANG_FAMILIES: tuple[str, ...] = (
    "python", "javascript", "go", "rust", "java", "csharp",
    "c_cpp", "ruby", "php", "swift", "kotlin",
)
LANG_DATA_FAMILIES: frozenset[str] = frozenset(("json", "yaml", "toml", "xml"))

GRAPH_EDGE_TYPES: tuple[str, ...] = (
    "callee", "caller", "sibling", "implementor", "doc_xref",
)
SYMBOL_SOURCES: tuple[str, ...] = (
    "agent_seed", "auto_seed", "task_extracted", "path_mention", "pin",
)
IMPORT_DIRECTIONS: tuple[str, ...] = ("forward", "reverse", "barrel", "test_pair")
DEF_KINDS: tuple[str, ...] = ("function", "class", "method", "variable", "interface")
ARTIFACT_KINDS: tuple[str, ...] = ("code", "test", "config", "doc", "build")
INTENT_TYPES: tuple[str, ...] = ("debug", "implement", "refactor", "understand", "test")

CALLER_TIER_ORDINALS: dict[str, int] = {
    "proven": 3, "strong": 2, "anchored": 1, "unknown": 0,
}
