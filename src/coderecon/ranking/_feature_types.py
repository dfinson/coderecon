"""TypedDict contracts and type aliases for feature extraction.

One TypedDict per LightGBM model.  Key names are the model's
``feature_name()`` list; renaming any field breaks inference.
"""

from __future__ import annotations

from typing import Any, TypedDict

# (key, default) field specs — passthrough copy from source dict.
FieldSpec = tuple[tuple[str, Any], ...]
# (output_key, input_key) — aggregation mapping for file ranker.
AggPair = tuple[tuple[str, str], ...]


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
