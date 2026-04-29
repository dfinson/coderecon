"""Recon domain models — dataclasses for evidence, candidates, and parsed tasks.

No I/O, no database access, no async.  Pure data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from coderecon.mcp.tools.recon.recon_constants import (
    _STOP_WORDS,  # noqa: F401  # re-exported for parsing.py
    ArtifactKind,
    TaskIntent,
    _classify_artifact,  # noqa: F401  # re-exported for merge.py, tests
    _extract_intent,  # noqa: F401  # re-exported for parsing.py, tests
    _is_barrel_file,  # noqa: F401  # re-exported for merge.py, tests
    _is_test_file,  # noqa: F401  # re-exported for merge.py, tests
)

if TYPE_CHECKING:
    from coderecon.index.models import DefFact

@dataclass
class EvidenceRecord:
    """A single piece of evidence supporting a candidate's relevance."""
    category: str  # "term_match", "lexical", "explicit"
    detail: str  # Human-readable description
    score: float = 0.0  # Normalized [0, 1] contribution

# HarvestCandidate — unified representation from all harvesters

@dataclass
class HarvestCandidate:
    """A definition candidate produced by one or more harvesters.

    Accumulates evidence from multiple sources.  The filter pipeline
    and scoring operate on these objects.

    Separated scores:
      - ``relevance_score``: How relevant to the task (for response ranking).
      - ``seed_score``: How good as a graph-expansion entry point
        (considers hub score, centrality, not just relevance).
    """

    def_uid: str
    def_fact: DefFact | None = None
    artifact_kind: ArtifactKind = ArtifactKind.code

    # Which harvesters found this candidate
    from_term_match: bool = False
    from_explicit: bool = False
    from_graph: bool = False
    from_coverage: bool = False

    # Harvester-specific scores
    matched_terms: set[str] = field(default_factory=set)

    # Raw signal fields for ranking model training
    term_match_count: int = 0  # Raw count of query terms matching this def's name
    term_total_matches: int = 0  # How many defs matched each term (IDF denominator)
    lex_hit_count: int = 0  # Per-def Tantivy lexical index hits
    bm25_file_score: float = 0.0  # Best BM25 score of any file containing this def
    graph_edge_type: str | None = None  # callee/caller/sibling/override/implementor/doc_xref or None
    graph_seed_rank: int | None = None  # Position of the seed in merged pool
    graph_caller_max_tier: str | None = None  # Best ref_tier among caller refs (proven > strong > anchored > unknown)
    symbol_source: str | None = None  # agent_seed/auto_seed/task_extracted/path_mention or None
    import_direction: str | None = None  # forward/reverse/barrel/test_pair or None
    splade_score: float = 0.0  # SPLADE sparse dot-product score (Harvester S)

    # Structured evidence trail
    evidence: list[EvidenceRecord] = field(default_factory=list)

    # Separated scores (populated during scoring phase)
    relevance_score: float = 0.0
    seed_score: float = 0.0

    # Structural metadata (populated during enrichment)
    hub_score: int = 0
    file_path: str = ""
    language_family: str = ""
    is_test: bool = False
    is_barrel: bool = False
    is_endpoint: bool = False
    test_coverage_count: int = 0
    declared_module: str = ""
    shares_file_with_seed: bool = False
    is_callee_of_top: bool = False
    is_imported_by_top: bool = False
    @property
    def evidence_axes(self) -> int:
        """Count of independent harvester sources that found this candidate."""
        return sum(
            [
                self.from_term_match,
                self.from_explicit,
                self.from_graph,
                self.from_coverage,
            ]
        )
    @property
    def has_semantic_evidence(self) -> bool:
        """Semantic axis: matched >= 2 terms,
        OR single term with hub support, OR lexical hit, OR explicit mention,
        OR graph-discovered.
        """
        return (
            len(self.matched_terms) >= 2
            or (len(self.matched_terms) == 1 and self.hub_score >= 3)
            or self.from_explicit
            or self.from_graph
        )
    @property
    def has_structural_evidence(self) -> bool:
        """Structural axis: hub >= 1, OR shares file, OR callee-of,
        OR imported-by."""
        return (
            self.hub_score >= 1
            or self.shares_file_with_seed
            or self.is_callee_of_top
            or self.is_imported_by_top
        )
    def matches_negative(self, negative_mentions: list[str]) -> bool:
        """Return True if this candidate's name/path matches a negated term."""
        if not negative_mentions:
            return False
        name_lower = self.def_fact.name.lower() if self.def_fact else ""
        path_lower = self.file_path.lower()
        return any(neg in name_lower or neg in path_lower for neg in negative_mentions)
    @property
    def has_strong_single_axis(self) -> bool:
        """True if any one axis is strong enough to pass alone (OR gate).

        Used to let high-confidence candidates through even when they
        lack evidence on other axes.
        """
        return self.from_explicit or self.hub_score >= 8 or len(self.matched_terms) >= 3

# ParsedTask — structured extraction from free-text

@dataclass(frozen=True)
class ParsedTask:
    """Structured extraction from a free-text task description.

    All fields are derived server-side — no agent cooperation required.
    The agent just sends ``task: str`` and the server extracts everything.

    Attributes:
        raw:              Original task text.
        intent:           Classified intent (debug/implement/refactor/etc.).
        primary_terms:    High-signal search terms (longest first).
        secondary_terms:  Lower-signal terms (short, generic, or from camelCase splits).
        explicit_paths:   File paths mentioned in the task text.
        explicit_symbols: Symbol-like identifiers mentioned in the task.
        keywords:         Union of primary + secondary for broad matching.
        query_text:       Synthesized query text.
        negative_mentions: Terms the user explicitly excludes ("not X", "except Y").
        is_stacktrace_driven: True if task contains error/traceback patterns.
        is_test_driven:   True if the primary goal is writing/fixing tests.
    """

    raw: str
    intent: TaskIntent = TaskIntent.unknown
    primary_terms: list[str] = field(default_factory=list)
    secondary_terms: list[str] = field(default_factory=list)
    explicit_paths: list[str] = field(default_factory=list)
    explicit_symbols: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    query_text: str = ""
    negative_mentions: list[str] = field(default_factory=list)
    is_stacktrace_driven: bool = False
    is_test_driven: bool = False
