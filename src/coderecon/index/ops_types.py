"""Data classes and enums for index operations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InitResult:
    """Result of coordinator initialization."""

    contexts_discovered: int
    contexts_valid: int
    contexts_failed: int
    contexts_detached: int
    files_indexed: int
    errors: list[str]
    files_by_ext: dict[str, int] = field(default_factory=dict)  # extension -> file count


@dataclass
class IndexStats:
    """Statistics from an indexing operation."""

    files_processed: int
    files_added: int
    files_updated: int
    files_removed: int
    symbols_indexed: int
    duration_seconds: float


@dataclass
class SearchResult:
    """Result from a search operation."""

    path: str
    line: int
    column: int | None
    snippet: str
    score: float


@dataclass
class SearchResponse:
    """Response from a search operation including metadata."""

    results: list[SearchResult]
    fallback_reason: str | None = None  # Set if query syntax error triggered literal fallback


class SearchMode:
    """Search mode enum."""

    TEXT = "text"
    SYMBOL = "symbol"
    PATH = "path"
