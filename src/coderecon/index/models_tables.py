"""Advanced index table models (type facts, coverage, endpoints, semantic)."""

import json

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlmodel import Field, SQLModel

from coderecon.index.models_enums import (
    Certainty,
    Freshness,
    LanguageFamily,
    ProbeStatus,
)


class TypeAnnotationFact(SQLModel, table=True):
    """Type annotation extracted from source code.
    Captures explicit type annotations from any language that supports them.
    Used by Pass 3 (type-traced resolution) to resolve member accesses.
    """
    __tablename__ = "type_annotation_facts"
    annotation_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    scope_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("scope_facts.scope_id", ondelete="CASCADE"), nullable=True, index=True)
    )
    # What's annotated
    target_kind: str  # AnnotationTargetKind value
    target_name: str = Field(index=True)  # Variable/param name
    # The annotation (as written and normalized)
    raw_annotation: str  # As written: "List[int]", "int[]", "[]int"
    canonical_type: str = Field(index=True)  # Normalized: "List<int>"
    base_type: str = Field(index=True)  # Just the base: "List", "int"
    # Type modifiers (language-agnostic flags)
    is_optional: bool = Field(default=False)  # None/null/nil allowed
    is_array: bool = Field(default=False)  # Collection type
    is_generic: bool = Field(default=False)  # Has type parameters
    is_reference: bool = Field(default=False)  # Pointer/ref type
    is_mutable: bool = Field(default=True)  # const/final/let vs var
    # Generic parameters (if applicable)
    type_args_json: str | None = None  # ["int"] for List<int>
    # Source info
    start_line: int
    start_col: int
    def get_type_args(self) -> list[str]:
        """Parse type_args_json to list."""
        if self.type_args_json is None:
            return []
        result: list[str] = json.loads(self.type_args_json)
        return result
class TypeMemberFact(SQLModel, table=True):
    """Member of a type definition (class field, struct field, interface method, etc.).
    Used to resolve attribute chains like `obj.field.method()`.
    """
    __tablename__ = "type_member_facts"
    member_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    # Parent type
    parent_def_uid: str = Field(index=True)  # DefFact.def_uid of class/struct/interface
    parent_type_name: str = Field(index=True)  # "AppContext", "MutationOps"
    parent_kind: str  # TypeParentKind value
    # Member info
    member_kind: str  # MemberKind value
    member_name: str = Field(index=True)
    member_def_uid: str | None = None  # DefFact.def_uid if method/property
    # Type info (for fields/properties)
    type_annotation: str | None = None  # "MutationOps", "str | None"
    canonical_type: str | None = Field(default=None, index=True)  # Normalized
    base_type: str | None = Field(default=None, index=True)  # Base type only
    # Visibility
    visibility: str | None = None  # "public", "private", "protected", "internal"
    is_static: bool = Field(default=False)
    is_abstract: bool = Field(default=False)
    start_line: int
    start_col: int
class MemberAccessFact(SQLModel, table=True):
    """Member access chain extracted from source code.
    Captures `obj.field.method()` patterns for type-traced resolution.
    """
    __tablename__ = "member_access_facts"
    access_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    scope_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("scope_facts.scope_id", ondelete="CASCADE"), nullable=True, index=True)
    )
    # Access pattern
    access_style: str  # AccessStyle value: dot, arrow, scope
    # The chain
    full_expression: str  # "ctx.mutation_ops.write_source"
    receiver_name: str = Field(index=True)  # Leftmost identifier: "ctx"
    member_chain: str  # Rest of chain: "mutation_ops.write_source"
    final_member: str = Field(index=True)  # Rightmost: "write_source"
    chain_depth: int  # Number of accesses: 2
    # Call info
    is_invocation: bool = Field(default=False)  # Ends with ()
    arg_count: int | None = None  # Number of arguments if call
    # Receiver type (from annotation in scope, if found)
    receiver_declared_type: str | None = Field(default=None, index=True)
    # Resolution results (filled by Pass 3)
    resolved_type_path: str | None = None  # "AppContext.MutationOps"
    final_target_def_uid: str | None = Field(default=None, index=True)
    resolution_method: str | None = None  # ResolutionMethod value
    resolution_confidence: float | None = None  # 0.0-1.0
    start_line: int
    start_col: int
    end_line: int
    end_col: int
class InterfaceImplFact(SQLModel, table=True):
    """Interface/trait implementation relationship.
    Used for Go, Rust, TypeScript interface matching.
    """
    __tablename__ = "interface_impl_facts"
    impl_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    # The implementing type
    implementor_def_uid: str = Field(index=True)
    implementor_name: str = Field(index=True)
    # The interface/trait
    interface_name: str = Field(index=True)
    interface_def_uid: str | None = None  # If found in same codebase
    # How declared
    impl_style: str  # ImplStyle value: explicit, structural, inferred
    start_line: int
    start_col: int
class ReceiverShapeFact(SQLModel, table=True):
    """Observed shape for duck-typing inference.
    Aggregates all member accesses on a receiver to infer its type.
    Used for dynamic languages without type annotations.
    """
    __tablename__ = "receiver_shape_facts"
    shape_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    scope_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("scope_facts.scope_id", ondelete="CASCADE"), nullable=True, index=True)
    )
    receiver_name: str = Field(index=True)  # "ctx", "self", "obj"
    declared_type: str | None = Field(default=None, index=True)  # If annotated
    # Observed shape (all members accessed on this receiver in this scope)
    shape_hash: str = Field(index=True)  # Hash for fast comparison
    observed_members_json: str  # {"fields": ["x"], "methods": ["run"]}
    # Inference results (filled by Pass 5)
    matched_types_json: str | None = None  # [{"type": "Foo", "confidence": 0.9}]
    best_match_type: str | None = Field(default=None, index=True)
    match_confidence: float | None = None  # 0.0-1.0
    def get_observed_members(self) -> dict[str, list[str]]:
        """Parse observed_members_json to dict."""
        result: dict[str, list[str]] = json.loads(self.observed_members_json)
        return result
    def get_matched_types(self) -> list[dict[str, float | str]]:
        """Parse matched_types_json to list."""
        if self.matched_types_json is None:
            return []
        result: list[dict[str, float | str]] = json.loads(self.matched_types_json)
        return result
class RepoState(SQLModel, table=True):
    """Repository state tracking (singleton row, id=1)."""
    __tablename__ = "repo_state"
    id: int = Field(default=1, primary_key=True)
    last_seen_head: str | None = None
    last_seen_index_mtime: float | None = None
    checked_at: float | None = None
    current_epoch_id: int | None = None  # Current epoch ID
    reconignore_hash: str | None = None  # Hash of .recon/.reconignore content
class Epoch(SQLModel, table=True):
    """Epoch record for incremental snapshot barriers. See SPEC.md §7.6."""
    __tablename__ = "epochs"
    epoch_id: int | None = Field(default=None, primary_key=True)
    published_at: float | None = None
    files_indexed: int = Field(default=0)
    commit_hash: str | None = None  # Git commit at epoch time (if available)
class DefSnapshotRecord(SQLModel, table=True):
    """Point-in-time snapshot of a DefFact, captured when an epoch is published.
    Used by semantic diff to compare symbol state across epochs without
    needing the full Index data to still exist.  No FK to File — the
    snapshot must survive file deletions.
    """
    __tablename__ = "def_snapshot_record"
    id: int | None = Field(default=None, primary_key=True)
    epoch_id: int = Field(index=True)
    file_path: str = Field(index=True)
    kind: str
    name: str
    lexical_path: str
    signature_hash: str | None = None
    display_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None

# TIER 3: BEHAVIORAL FACTS (coverage, lint — populated from tool execution)

class TestCoverageFact(SQLModel, table=True):
    """Coverage link between a test and a definition.
    Populated by joining coverage reports (line hit counts) against
    DefFact line ranges.  Each row records "test T covers definition D
    at line_rate R".
    Coverage facts are written at initial full-suite ingestion and
    updated incrementally by the background test pipeline on file save.
    """
    __tablename__ = "test_coverage_facts"
    id: int | None = Field(default=None, primary_key=True)
    test_id: str = Field(index=True)  # e.g. "tests/test_auth.py::test_login_valid"
    target_def_uid: str = Field(
        sa_column=Column(String, ForeignKey("def_facts.def_uid", ondelete="CASCADE"), index=True)
    )
    target_file_path: str = Field(index=True)  # Denormalized for fast queries
    covered_lines: int  # Lines within def range that were hit
    total_lines: int  # Total lines in def range
    line_rate: float  # covered_lines / total_lines
    branch_rate: float | None = None  # Branch coverage within def range if available
    epoch: int = Field(index=True)  # Epoch when this measurement was taken
    stale: bool = Field(default=False)  # True if def body changed since measurement
    test_passed: bool | None = Field(default=None)  # True=pass, False=fail, None=unknown
class LintStatusFact(SQLModel, table=True):
    """Persisted lint/type-check status for a file.
    Written by the background tier-1 pipeline on file save and at
    checkpoint time.  Diagnostic counts are aggregated per tool.
    """
    __tablename__ = "lint_status_facts"
    id: int | None = Field(default=None, primary_key=True)
    file_path: str = Field(index=True)
    tool_id: str = Field(index=True)  # "python.ruff", "js.eslint", etc.
    category: str = Field(index=True)  # "lint", "format", "type_check", "security"
    error_count: int = Field(default=0)
    warning_count: int = Field(default=0)
    info_count: int = Field(default=0)
    clean: bool = Field(default=True)  # True when all counts are zero
    epoch: int = Field(index=True)
class EndpointFact(SQLModel, table=True):
    """HTTP/RPC endpoint declaration or call site.
    Server-side: extracted from route decorators (@app.route, @Get, etc.)
    Client-side: extracted from HTTP client calls (fetch, requests.get, etc.)
    Matching server↔client by url_pattern creates cross-language edges.
    """
    __tablename__ = "endpoint_facts"
    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    kind: str = Field(index=True)  # "server" or "client"
    http_method: str | None = None  # GET, POST, PUT, DELETE, PATCH, *
    url_pattern: str = Field(index=True)  # "/api/users", "/api/users/:id"
    handler_def_uid: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("def_facts.def_uid", ondelete="SET NULL"), nullable=True, index=True),
    )
    start_line: int | None = None
    end_line: int | None = None
    framework: str | None = None  # "flask", "fastapi", "express", "gin", etc.
class DocCrossRef(SQLModel, table=True):
    """Cross-reference from a docstring/comment to a DefFact.
    Extracted by regex-parsing structured references like
    ``See also FooClass``, ``:func:`module.func```, or ``[BarClass](...)``.
    """
    __tablename__ = "doc_cross_refs"
    id: int | None = Field(default=None, primary_key=True)
    source_file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    source_def_uid: str | None = Field(default=None, index=True)  # Def containing the docstring
    source_line: int  # Line of the cross-reference
    raw_text: str  # Original reference text ("See also FooClass")
    target_def_uid: str = Field(
        sa_column=Column(String, ForeignKey("def_facts.def_uid", ondelete="CASCADE"), index=True)
    )
    confidence: str = Field(default="high")  # "high", "medium", "low"
class SpladeVec(SQLModel, table=True):
    """SPLADE sparse vector for a DefFact.
    Stores the sparse term-weight vector produced by splade-mini for
    each definition's anglicised scaffold.  Used at query time for
    sparse dot-product retrieval (Harvester S).
    """
    __tablename__ = "splade_vecs"
    def_uid: str = Field(
        sa_column=Column(String, ForeignKey("def_facts.def_uid", ondelete="CASCADE"), primary_key=True)
    )
    vector_json: str  # Compact JSON: {"term_idx": weight, ...}
    model_version: str = Field(index=True)  # e.g. "splade-mini-v1"
    scaffold_text: str | None = Field(default=None)  # Anglicised scaffold text
    vector_blob: bytes | None = Field(default=None)  # Packed (uint32,float32) pairs
class SemanticNeighborFact(SQLModel, table=True):
    """Semantic neighbor edge between two definitions.
    Pre-computed at index time via SPLADE dot product between def
    scaffold vectors.  Captures "similar code" relationships invisible
    to AST analysis — e.g. two parsers in different modules, duplicate
    implementations, analogous API handlers.
    """
    __tablename__ = "semantic_neighbor_facts"
    id: int | None = Field(default=None, primary_key=True)
    source_def_uid: str = Field(
        sa_column=Column(String, ForeignKey("def_facts.def_uid", ondelete="CASCADE"), index=True)
    )
    neighbor_def_uid: str = Field(
        sa_column=Column(String, ForeignKey("def_facts.def_uid", ondelete="CASCADE"), index=True)
    )
    score: float  # SPLADE dot-product similarity
    model_version: str = Field(index=True)
class FileChunkVec(SQLModel, table=True):
    """SPLADE vector for a non-code file chunk.
    Non-code files (markdown, YAML, TOML, etc.) don't produce DefFacts
    but still contain semantically meaningful content.  Each structural
    chunk (heading section, config key-path, etc.) gets a SPLADE vector
    for dot-product linking against code definitions.
    """
    __tablename__ = "file_chunk_vecs"
    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    chunk_key: str  # Structural key: heading path, YAML key-path, etc.
    chunk_text: str  # Raw text of the chunk
    start_line: int
    end_line: int
    vector_json: str  # Compact JSON sparse vector
    model_version: str = Field(index=True)
class DocCodeEdgeFact(SQLModel, table=True):
    """Semantic edge from a non-code file chunk to a code definition.
    Created at index time by dot-product between FileChunkVec and
    SpladeVec vectors.  Represents a semantic "relates to" connection
    between documentation/config content and code.
    """
    __tablename__ = "doc_code_edge_facts"
    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    chunk_key: str  # Which chunk in the file
    target_def_uid: str = Field(index=True)  # Code def this chunk relates to
    score: float  # SPLADE dot-product similarity
    model_version: str = Field(index=True)

# NON-TABLE MODELS (Pydantic only, for data transfer)

class FileState(SQLModel):
    """Computed file state (not persisted directly)."""
    freshness: Freshness
    certainty: Certainty
class CandidateContext(SQLModel):
    """Candidate context during discovery (not persisted directly)."""
    language_family: LanguageFamily
    root_path: str
    tier: int | None = None
    markers: list[str] = Field(default_factory=list)
    include_spec: list[str] | None = None
    exclude_spec: list[str] | None = None
    probe_status: ProbeStatus = ProbeStatus.PENDING
    is_root_fallback: bool = False  # True for tier-3 root fallback context
class LexicalHit(SQLModel):
    """Result from Tier 0 lexical search."""
    file_id: int
    unit_id: int
    path: str
    score: float
    snippet: str | None = None
