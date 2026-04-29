"""Dataset table schemas for ranking model training.

Two groups of tables, collected separately:

**Ground truth** (stable, collected once per task):
- ``Run``             — one row per task run
- ``TouchedObject``   — ground-truth relevant DefFacts per run (binary)
- ``Query``           — authored queries (OK and non-OK) per run

**Retrieval signals** (re-collected when harvesters change):
- ``CandidateRank``   — per-candidate retrieval features per query

See §5 of recon-lab/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Ground truth (stable, collected once) ────────────────────────


@dataclass(frozen=True)
class Run:
    """§5.1 — one task execution against a repo."""

    run_id: str
    repo_id: str
    task_id: str
    task_text: str


@dataclass(frozen=True)
class TouchedObject:
    """§5.2 — a relevant DefFact for a task.

    Binary labelling: 1 = relevant (minimum_sufficient), 0 = irrelevant.
    """

    run_id: str
    path: str
    kind: str
    name: str
    start_line: int
    end_line: int
    tier: str = "minimum"


@dataclass(frozen=True)
class Query:
    """§5.3 — an authored query for a task run."""

    run_id: str
    query_id: str
    query_text: str
    query_type: str  # Q_SEMANTIC | Q_LEXICAL | Q_IDENTIFIER | Q_STRUCTURAL | Q_NAVIGATIONAL | Q_SEM_IDENT | Q_IDENT_NAV | Q_FULL | UNSAT | BROAD | AMBIG
    seeds: tuple[str, ...] = ()  # symbol names passed as seeds
    pins: tuple[str, ...] = ()  # file paths passed as pins
    label_gate: str = "OK"  # OK | UNSAT | BROAD | AMBIG


# ── Retrieval signals (re-collected when harvesters change) ──────


@dataclass(frozen=True)
class CandidateRank:
    """§5.4 — per-candidate retrieval features for ranker training.

    Fields match the output of ``recon_raw_signals()``.
    """

    run_id: str
    query_id: str
    # Identity
    path: str
    kind: str
    name: str
    lexical_path: str
    # Span
    start_line: int
    end_line: int
    object_size_lines: int
    # Path
    file_ext: str
    parent_dir: str
    path_depth: int
    # Structural metadata
    has_docstring: bool
    has_decorators: bool
    has_return_type: bool
    hub_score: int
    is_test: bool
    nesting_depth: int
    has_parent_scope: bool
    # Retriever signals
    term_match_count: int | None
    term_total_matches: int | None
    lex_hit_count: int | None
    bm25_file_score: float
    graph_edge_type: str | None
    graph_seed_rank: int | None
    symbol_source: str | None
    import_direction: str | None
    from_coverage: bool
    retriever_hits: int
    rrf_score: float
    # Query features
    query_len: int
    has_identifier: bool
    has_path: bool
    # Label (binary: 0=irrelevant, 1=relevant)
    label_relevant: int


# ── Constants ────────────────────────────────────────────────────

OK_QUERY_TYPES = frozenset({
    "Q_SEMANTIC", "Q_LEXICAL", "Q_IDENTIFIER", "Q_STRUCTURAL",
    "Q_NAVIGATIONAL", "Q_SEM_IDENT", "Q_IDENT_NAV", "Q_FULL",
})

NON_OK_QUERY_TYPES = frozenset({"UNSAT", "BROAD", "AMBIG"})

ALL_QUERY_TYPES = OK_QUERY_TYPES | NON_OK_QUERY_TYPES
