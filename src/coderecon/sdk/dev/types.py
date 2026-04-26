"""Dev SDK result types — training-specific data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RawSignalsResult:
    """Full retrieval signal payload for training data collection."""

    query_features: dict[str, Any] = field(default_factory=dict)
    repo_features: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class DefEntry:
    """A single definition from the index."""

    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    uid: str = ""
    language_family: str = ""
    qualified_name: str = ""
    lexical_path: str = ""
    has_docstring: bool = False
    has_decorators: bool = False
    has_return_type: bool = False
    object_size_lines: int = 0


@dataclass(frozen=True)
class IndexFactsResult:
    """Structured metadata extracted from the index for LLM grounding."""

    top_dirs: list[str] = field(default_factory=list)
    languages: list[dict[str, Any]] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    external_deps: list[str] = field(default_factory=list)
    file_count: int = 0
    def_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class IndexStatusResult:
    """Per-worktree index status (file counts, def counts)."""

    worktree: str = ""
    file_count: int = 0
    def_count: int = 0
    initialized: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# Wire → typed conversion


def _to_raw_signals_result(d: dict[str, Any]) -> RawSignalsResult:
    return RawSignalsResult(
        query_features=d.get("query_features", {}),
        repo_features=d.get("repo_features", {}),
        candidates=d.get("candidates", []),
        diagnostics=d.get("diagnostics", {}),
        raw=d,
    )


def _to_index_facts_result(d: dict[str, Any]) -> IndexFactsResult:
    return IndexFactsResult(
        top_dirs=d.get("top_dirs", []),
        languages=d.get("languages", []),
        classes=d.get("classes", []),
        functions=d.get("functions", []),
        external_deps=d.get("external_deps", []),
        file_count=d.get("file_count", 0),
        def_count=d.get("def_count", 0),
        raw=d,
    )


def _to_def_entries(d: dict[str, Any]) -> list[DefEntry]:
    return [
        DefEntry(
            path=e.get("path", ""),
            name=e.get("name", ""),
            kind=e.get("kind", ""),
            start_line=e.get("start_line", 0),
            end_line=e.get("end_line", 0),
            uid=e.get("uid", ""),
            language_family=e.get("language_family", ""),
            qualified_name=e.get("qualified_name", ""),
            lexical_path=e.get("lexical_path", ""),
            has_docstring=e.get("has_docstring", False),
            has_decorators=e.get("has_decorators", False),
            has_return_type=e.get("has_return_type", False),
            object_size_lines=e.get("object_size_lines", 0),
        )
        for e in d.get("defs", [])
    ]


def _to_index_status_result(d: dict[str, Any]) -> IndexStatusResult:
    return IndexStatusResult(
        worktree=d.get("worktree", ""),
        file_count=d.get("file_count", 0),
        def_count=d.get("def_count", 0),
        initialized=d.get("initialized", False),
        raw=d,
    )
