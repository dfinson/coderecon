"""SQLModel definitions for the Tier 0 + Tier 1 stacked index.

Single source of truth for all table schemas. See SPEC.md §7 for architecture.
"""

import json

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from coderecon.index.models_enums import (  # noqa: F401 — re-exports
    BindReasonCode,
    BindTargetKind,
    Certainty,
    DynamicAccessPattern,
    ExportThunkMode,
    Freshness,
    ImportKind,
    LanguageFamily,
    MarkerTier,
    ProbeStatus,
    RefTier,
    ResolutionMethod,
    Role,
    ScopeKind,
)
from coderecon.index.models_tables import (  # noqa: F401 — re-exports
    CandidateContext,
    DefSnapshotRecord,
    DocCodeEdgeFact,
    DocCrossRef,
    EndpointFact,
    Epoch,
    FileChunkVec,
    FileState,
    InterfaceImplFact,
    LexicalHit,
    LintStatusFact,
    MemberAccessFact,
    ReceiverShapeFact,
    RepoState,
    SemanticNeighborFact,
    SpladeVec,
    TestCoverageFact,
    TypeAnnotationFact,
    TypeMemberFact,
)


class Worktree(SQLModel, table=True):
    """A worktree (or main checkout) tracked in this repo's index."""
    __tablename__ = "worktrees"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    root_path: str = Field(unique=True, index=True)
    branch: str | None = None
    is_main: bool = Field(default=False)
class File(SQLModel, table=True):
    """Tracked file in the repository."""
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("worktree_id", "path", name="uq_files_wt_path"),
    )
    id: int | None = Field(default=None, primary_key=True)
    worktree_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("worktrees.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    path: str = Field(index=True)
    language_family: str | None = None
    content_hash: str | None = None
    line_count: int | None = None
    indexed_at: float | None = None
    last_indexed_epoch: int | None = Field(default=None, index=True)
    declared_module: str | None = Field(
        default=None,
        index=True,
        description="Language-level module/package identity extracted from source "
        "(e.g. 'cats.effect' from Scala `package cats.effect`, "
        "'Newtonsoft.Json' from C# `namespace Newtonsoft.Json`). "
        "NULL for languages without declarations (JS/TS, C/C++) "
        "or files missing declarations.",
    )
    parse_status: str | None = None
    # Relationships
    defs: list["DefFact"] = Relationship(back_populates="file")
    refs: list["RefFact"] = Relationship(back_populates="file")
    scopes: list["ScopeFact"] = Relationship(back_populates="file")
    binds: list["LocalBindFact"] = Relationship(back_populates="file")
    imports: list["ImportFact"] = Relationship(back_populates="file")
    dynamic_sites: list["DynamicAccessSite"] = Relationship(back_populates="file")
class TestTarget(SQLModel, table=True):
    """Test target discovered during indexing."""
    __tablename__ = "test_targets"
    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    target_id: str = Field(unique=True, index=True)  # Stable ID for run()
    selector: str  # Path or pattern
    kind: str = Field(index=True)  # "file", "directory", "module"
    language: str = Field(index=True)
    runner_pack_id: str = Field(index=True)  # "python.pytest", "js.vitest", etc.
    workspace_root: str
    estimated_cost: str = "medium"  # "low", "medium", "high"
    test_count: int | None = None
    path: str | None = None  # File path if kind is "file"
    discovered_at: float | None = None
class IndexedLintTool(SQLModel, table=True):
    """Lint tool discovered during indexing."""
    __tablename__ = "indexed_lint_tools"
    id: int | None = Field(default=None, primary_key=True)
    tool_id: str = Field(unique=True, index=True)  # "python.ruff", "js.eslint", etc.
    name: str
    category: str = Field(index=True)  # "lint", "format", "type_check", "security"
    languages: str  # JSON array of language strings
    executable: str
    workspace_root: str
    config_file: str | None = None  # Which config file triggered detection
    discovered_at: float | None = None
class IndexedCoverageCapability(SQLModel, table=True):
    """Coverage capability discovered during indexing.
    Stores whether coverage tools are available for each (workspace, runner_pack) pair.
    Queried at execution time instead of re-detecting tools per invocation.
    """
    __tablename__ = "indexed_coverage_capabilities"
    id: int | None = Field(default=None, primary_key=True)
    workspace_root: str = Field(index=True)
    runner_pack_id: str = Field(index=True)  # "python.pytest", "js.jest", etc.
    tools_json: str  # JSON dict of tool_name -> is_available
    discovered_at: float | None = None
    def get_tools(self) -> dict[str, bool]:
        """Parse tools_json to dict."""
        if self.tools_json is None:
            return {}
        result: dict[str, bool] = json.loads(self.tools_json)
        return result
    def set_tools(self, tools: dict[str, bool]) -> None:
        """Set tools_json from dict."""
        self.tools_json = json.dumps(tools) if tools else "{}"
class Context(SQLModel, table=True):
    """Indexing context (package, workspace, etc) - represents a build unit."""
    __tablename__ = "contexts"
    id: int | None = Field(default=None, primary_key=True)
    name: str | None = None
    language_family: str = Field(index=True)
    root_path: str = Field(index=True)
    tier: int | None = None
    probe_status: str = Field(default=ProbeStatus.PENDING.value, index=True)
    include_spec: str | None = None
    exclude_spec: str | None = None
    config_hash: str | None = None
    refreshed_at: float | None = None
    # Relationships
    markers: list["ContextMarker"] = Relationship(back_populates="context")
    def get_include_globs(self) -> list[str]:
        """Parse include_spec JSON to list."""
        if self.include_spec is None:
            return []
        result: list[str] = json.loads(self.include_spec)
        return result
    def get_exclude_globs(self) -> list[str]:
        """Parse exclude_spec JSON to list."""
        if self.exclude_spec is None:
            return []
        result: list[str] = json.loads(self.exclude_spec)
        return result
class ContextMarker(SQLModel, table=True):
    """Marker file that triggered context discovery."""
    __tablename__ = "context_markers"
    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    marker_path: str
    marker_tier: str
    detected_at: float | None = None
    # Relationships
    context: Context | None = Relationship(back_populates="markers")
class DefFact(SQLModel, table=True):
    """Definition fact (function, class, method, variable). See SPEC.md §7.3.1."""
    __tablename__ = "def_facts"
    def_uid: str = Field(primary_key=True)  # Stable identity (see §7.4)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    kind: str = Field(index=True)  # function, class, method, variable, etc.
    name: str = Field(index=True)  # Simple name
    qualified_name: str | None = None  # Full nesting path (e.g., Class.method) — populated from lexical_path
    lexical_path: str = Field(index=True)  # Syntactic nesting path for identity
    namespace: str | None = Field(
        default=None, index=True
    )  # Declaring namespace/package (C#, Java, Go, etc.)
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    signature_hash: str | None = None  # Hash of syntactic signature
    display_name: str | None = None  # Human-readable form
    signature_text: str | None = None  # Raw signature text (e.g. '(self, x: int, y: int)')
    decorators_json: str | None = None  # JSON array of decorator/annotation strings
    docstring: str | None = None  # First paragraph of docstring text
    return_type: str | None = None  # Return type annotation text
    # Relationships
    file: File | None = Relationship(back_populates="defs")
    # Note: refs relationship removed - use FactQueries.list_refs_by_def_uid() instead
class RefFact(SQLModel, table=True):
    """Reference fact (identifier occurrence). See SPEC.md §7.3.2."""
    __tablename__ = "ref_facts"
    ref_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    scope_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("scope_facts.scope_id", ondelete="CASCADE"), nullable=True, index=True)
    )
    token_text: str = Field(index=True)  # Exact text slice from source
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    role: str = Field(index=True)  # DEFINITION, REFERENCE, IMPORT, EXPORT
    ref_tier: str = Field(
        default=RefTier.UNKNOWN.value, index=True
    )  # PROVEN, STRONG, ANCHORED, UNKNOWN
    certainty: str = Field(default=Certainty.CERTAIN.value)
    target_def_uid: str | None = Field(
        default=None, index=True
    )  # Target def_uid (not FK, join manually)
    # Relationships
    file: File | None = Relationship(back_populates="refs")
    scope: "ScopeFact" = Relationship(back_populates="refs")
    # Note: target_def relationship removed - use FactQueries.get_def() instead
class ScopeFact(SQLModel, table=True):
    """Lexical scope fact. See SPEC.md §7.3.3."""
    __tablename__ = "scope_facts"
    scope_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    parent_scope_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("scope_facts.scope_id", ondelete="CASCADE"), nullable=True, index=True)
    )  # NULL for file scope
    kind: str = Field(index=True)  # file, class, function, block, etc.
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    # Relationships
    file: File | None = Relationship(back_populates="scopes")
    refs: list[RefFact] = Relationship(back_populates="scope")
    binds: list["LocalBindFact"] = Relationship(back_populates="scope")
class LocalBindFact(SQLModel, table=True):
    """Same-file binding fact (index-time only, NO query-time inference). See SPEC.md §7.3.4."""
    __tablename__ = "local_bind_facts"
    bind_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    scope_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("scope_facts.scope_id", ondelete="CASCADE"), nullable=True, index=True)
    )
    name: str = Field(index=True)  # Bound identifier name
    target_kind: str  # DEF, IMPORT, UNKNOWN
    target_uid: str | None = None  # def_uid or import_uid or NULL
    certainty: str = Field(default=Certainty.CERTAIN.value)
    reason_code: str  # PARAM, LOCAL_ASSIGN, DEF_IN_SCOPE, IMPORT_ALIAS
    # Relationships (scope_id nullable, so relationship is optional)
    file: File | None = Relationship(back_populates="binds")
    scope: "ScopeFact" = Relationship(
        back_populates="binds", sa_relationship_kwargs={"foreign_keys": "[LocalBindFact.scope_id]"}
    )
class ImportFact(SQLModel, table=True):
    """Import statement fact (syntactic only). See SPEC.md §7.3.5."""
    __tablename__ = "import_facts"
    import_uid: str = Field(primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    scope_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("scope_facts.scope_id", ondelete="CASCADE"), nullable=True, index=True)
    )
    imported_name: str = Field(index=True)  # Name being imported
    alias: str | None = None  # Local alias (NULL if none)
    source_literal: str | None = None  # Import source string literal (if extractable)
    resolved_path: str | None = Field(
        default=None,
        index=True,
        description="Repo-relative file path this import resolves to. "
        "Populated at index time by resolving source_literal against "
        "declared_module values (declaration-based languages) or file "
        "paths (path-based languages like JS/TS, C/C++, Python).",
    )
    import_kind: str  # python_import, python_from, js_import, etc.
    certainty: str = Field(default=Certainty.CERTAIN.value)
    start_line: int | None = None  # 1-indexed start line of import statement
    start_col: int | None = None  # 0-indexed start column
    end_line: int | None = None  # 1-indexed end line of import statement
    end_col: int | None = None  # 0-indexed end column
    # Relationships
    file: File | None = Relationship(back_populates="imports")
class ExportSurface(SQLModel, table=True):
    """Materialized export surface per build unit. See SPEC.md §7.3.6."""
    __tablename__ = "export_surfaces"
    surface_id: int | None = Field(default=None, primary_key=True)
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), unique=True, index=True)
    )
    surface_hash: str | None = None  # Hash of all entries for invalidation
    epoch_id: int | None = None  # Epoch when surface was computed
    # Relationships
    entries: list["ExportEntry"] = Relationship(back_populates="surface")
class ExportEntry(SQLModel, table=True):
    """Individual exported name within an ExportSurface. See SPEC.md §7.3.7."""
    __tablename__ = "export_entries"
    entry_id: int | None = Field(default=None, primary_key=True)
    surface_id: int = Field(
        sa_column=Column(Integer, ForeignKey("export_surfaces.surface_id", ondelete="CASCADE"), index=True)
    )
    exported_name: str = Field(index=True)  # Public name
    def_uid: str | None = None  # Target definition (NULL if unresolved)
    certainty: str = Field(default=Certainty.CERTAIN.value)
    evidence_kind: str | None = None  # explicit_export, default_module, __all__literal, etc.
    # Relationships
    surface: ExportSurface | None = Relationship(back_populates="entries")
class ExportThunk(SQLModel, table=True):
    """Re-export declaration (strictly constrained forms only). See SPEC.md §7.3.8."""
    __tablename__ = "export_thunks"
    thunk_id: int | None = Field(default=None, primary_key=True)
    source_unit: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )  # Unit doing the re-export
    target_unit: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )  # Unit being re-exported from
    mode: str  # REEXPORT_ALL, EXPLICIT_NAMES, ALIAS_MAP
    explicit_names: str | None = None  # JSON array of names (if EXPLICIT_NAMES)
    alias_map: str | None = None  # JSON object of name→alias (if ALIAS_MAP)
    evidence_kind: str | None = None  # Syntax node type that produced this
    def get_explicit_names(self) -> list[str]:
        """Parse explicit_names JSON to list."""
        if self.explicit_names is None:
            return []
        result: list[str] = json.loads(self.explicit_names)
        return result
    def get_alias_map(self) -> dict[str, str]:
        """Parse alias_map JSON to dict."""
        if self.alias_map is None:
            return {}
        result: dict[str, str] = json.loads(self.alias_map)
        return result
class AnchorGroup(SQLModel, table=True):
    """Bounded ambiguity bucket for refs. See SPEC.md §7.3.9."""
    __tablename__ = "anchor_groups"
    group_id: int | None = Field(default=None, primary_key=True)
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    member_token: str = Field(index=True)  # The identifier text (e.g., 'foo')
    receiver_shape: str | None = None  # Receiver pattern (e.g., 'self.', 'obj.', 'None')
    total_count: int = Field(default=0)  # Total refs in this group
    exemplar_ids: str | None = None  # JSON array of ref_ids (hard-capped)
    def get_exemplar_ids(self) -> list[int]:
        """Parse exemplar_ids JSON to list."""
        if self.exemplar_ids is None:
            return []
        result: list[int] = json.loads(self.exemplar_ids)
        return result
class DynamicAccessSite(SQLModel, table=True):
    """Telemetry for dynamic access patterns (reporting only). See SPEC.md §7.3.10."""
    __tablename__ = "dynamic_access_sites"
    site_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(
        sa_column=Column(Integer, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    )
    start_line: int
    start_col: int
    pattern_type: str  # bracket_access, getattr, reflect, eval, etc.
    extracted_literals: str | None = None  # JSON array of literal strings (if any)
    has_non_literal_key: bool = Field(default=False)  # True if key is computed/dynamic
    # Relationships
    file: File | None = Relationship(back_populates="dynamic_sites")
    def get_extracted_literals(self) -> list[str]:
        """Parse extracted_literals JSON to list."""
        if self.extracted_literals is None:
            return []
        result: list[str] = json.loads(self.extracted_literals)
        return result

# TIER 2 TYPE-AWARE FACT TABLES (Type-traced refactoring support)

