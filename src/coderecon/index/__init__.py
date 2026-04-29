"""Index module - Full Stacked Index (Tier 0 + Tier 1).

Architecture (see SPEC.md §7):
- Tier 0: Tantivy lexical index (always-on, candidate discovery)
- Tier 1: Tree-sitter/SQLite structural facts (defs, refs, scopes, binds, imports, exports)

This index provides syntactic facts only. No semantic resolution, no call graph,
no type information.
"""

from coderecon.index.models import (
    # Fact tables
    AnchorGroup,
    # Enums
    BindReasonCode,
    BindTargetKind,
    # Data transfer models
    CandidateContext,
    Certainty,
    Context,
    ContextMarker,
    DefFact,
    DynamicAccessPattern,
    DynamicAccessSite,
    Epoch,
    ExportEntry,
    ExportSurface,
    ExportThunk,
    ExportThunkMode,
    File,
    FileState,
    Freshness,
    ImportFact,
    ImportKind,
    LanguageFamily,
    LexicalHit,
    LocalBindFact,
    MarkerTier,
    ProbeStatus,
    RefFact,
    RefTier,
    RepoState,
    Role,
    ScopeFact,
    ScopeKind,
)

# Lazy imports for ops to avoid circular dependency:
# discovery/ → index.models → index.__init__ → index.ops → discovery/
def __getattr__(name: str):  # noqa: N807
    _OPS_NAMES = {
        "IndexCoordinatorEngine", "IndexStats", "InitResult",
        "SearchMode", "SearchResult",
    }
    if name in _OPS_NAMES:
        from coderecon.index import ops as _ops
        return getattr(_ops, name)
    if name == "ContextRuntime":
        from coderecon.testing.runtime import ContextRuntime as _CR
        return _CR
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Public API (lazy)
    "IndexCoordinatorEngine",
    "IndexStats",
    "InitResult",
    "SearchMode",
    "SearchResult",
    "ContextRuntime",
    # Enums
    "LanguageFamily",
    "Freshness",
    "Certainty",
    "RefTier",
    "Role",
    "ScopeKind",
    "BindTargetKind",
    "BindReasonCode",
    "ImportKind",
    "ExportThunkMode",
    "DynamicAccessPattern",
    "ProbeStatus",
    "MarkerTier",
    # Fact tables
    "File",
    "Context",
    "ContextMarker",
    "DefFact",
    "RefFact",
    "ScopeFact",
    "LocalBindFact",
    "ImportFact",
    "ExportSurface",
    "ExportEntry",
    "ExportThunk",
    "AnchorGroup",
    "DynamicAccessSite",
    "RepoState",
    "Epoch",
    # Data transfer models
    "FileState",
    "CandidateContext",
    "LexicalHit",
    # Testing runtime model (registered for schema)
    "ContextRuntime",
]
