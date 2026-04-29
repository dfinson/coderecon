"""Lexical index for full-text search via Tantivy.

This module provides full-text search capabilities using Tantivy,
a fast Rust-based search engine. It supports:
- File content indexing
- Symbol name search
- Code snippet retrieval
- Fuzzy matching
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tantivy

from coderecon.adapters.files.ops import atomic_write_text

# Bump when the Tantivy schema changes — triggers automatic index rebuild.
# 1 → 2: added ``worktree`` field and made ``path_exact`` a compound
#         ``{worktree}:{path}`` key for per-worktree deletion.
SCHEMA_VERSION = 2

@dataclass
class SearchResult:
    """A single search result."""
    file_path: str
    line: int
    column: int
    snippet: str
    score: float
    context_id: int | None = None
@dataclass
class SearchResults:
    """Collection of search results."""
    results: list[SearchResult] = field(default_factory=list)
    total_hits: int = 0
    query_time_ms: int = 0
    fallback_reason: str | None = None  # Set if query syntax error triggered literal fallback
class LexicalIndex:
    """
    Full-text search index using Tantivy.
    Provides fuzzy search over:
    - File contents (for grep-like search)
    - Symbol names (for quick navigation)
    - Documentation strings
    Supports staged writes for epoch atomicity:
    - stage_file() / stage_remove() buffer changes in memory
    - commit_staged() commits all staged changes atomically
    - discard_staged() discards uncommitted changes
    Usage::
        index = LexicalIndex(index_path)
        # Staged writes (for epoch atomicity)
        index.stage_file("src/foo.py", content, context_id=1)
        index.stage_file("src/bar.py", content, context_id=1)
        index.commit_staged()  # Single atomic commit
        # Or direct writes (backward compatible)
        index.add_file("src/foo.py", content, context_id=1)
        # Search
        results = index.search("class MyClass", limit=10)
    """
    def __init__(self, index_path: Path | str) -> None:
        """
        Initialize the lexical index.
        Args:
            index_path: Directory to store Tantivy index files
        """
        self.index_path = Path(index_path)
        self._index: tantivy.Index | None = None
        self._writer: tantivy.IndexWriter | None = None
        self._schema: tantivy.Schema | None = None
        self._initialized = False
        # Staging buffer for atomic epoch commits
        self._staged_adds: list[dict[str, Any]] = []
        # Each entry is (worktree, path) — both needed for compound-key deletion.
        self._staged_removes: list[tuple[str, str]] = []
    def _ensure_initialized(self) -> None:
        """Lazily initialize the Tantivy index."""
        if self._initialized:
            return
        # Schema-version migration: if the on-disk version differs, wipe and
        # rebuild so we don't silently return stale or mis-keyed results.
        version_file = self.index_path / "schema_version.json"
        if self.index_path.exists() and version_file.exists():
            try:
                stored = json.loads(version_file.read_text())["version"]
            except (OSError, KeyError, ValueError):
                stored = 0
            if stored != SCHEMA_VERSION:
                shutil.rmtree(self.index_path, ignore_errors=True)
        elif self.index_path.exists() and any(self.index_path.iterdir()):
            # Non-empty directory without a version file is a v1 index.
            shutil.rmtree(self.index_path, ignore_errors=True)
        # Build schema
        schema_builder = tantivy.SchemaBuilder()
        # Use default tokenizer for path to allow partial matching (e.g., "utils" matches "src/utils.py")
        schema_builder.add_text_field("path", stored=True, tokenizer_name="default")
        # Compound deletion key: "{worktree}:{path}" — raw so we match exactly.
        schema_builder.add_text_field("path_exact", stored=False, tokenizer_name="raw")
        schema_builder.add_text_field("content", stored=True, tokenizer_name="default")
        schema_builder.add_text_field("symbols", stored=True, tokenizer_name="default")
        schema_builder.add_integer_field("context_id", stored=True, indexed=True)
        schema_builder.add_integer_field("file_id", stored=True, indexed=True)
        # Per-worktree discriminator — raw tokenizer for exact filter queries.
        schema_builder.add_text_field("worktree", stored=True, tokenizer_name="raw")
        self._schema = schema_builder.build()
        # Create or open index
        self.index_path.mkdir(parents=True, exist_ok=True)
        self._index = tantivy.Index(self._schema, path=str(self.index_path))
        self._initialized = True
        # Persist the schema version so future startups detect upgrades.
        atomic_write_text(version_file, json.dumps({"version": SCHEMA_VERSION}))
    def add_file(
        self,
        file_path: str,
        content: str,
        context_id: int,
        file_id: int = 0,
        symbols: list[str] | None = None,
        worktree: str = "main",
    ) -> None:
        """
        Add or update a file in the index.
        Args:
            file_path: Relative file path
            content: File content as string
            context_id: Context this file belongs to
            file_id: Database file ID
            symbols: List of symbol names in this file
            worktree: Worktree this file belongs to (default "main")
        """
        self._ensure_initialized()
        writer = self._index.writer()
        try:
            # Delete the existing document for this (worktree, path) pair.
            _key = f"{worktree}:{file_path}"
            writer.delete_documents("path_exact", _key)
            # Add new document
            doc = tantivy.Document()
            doc.add_text("path", file_path)
            doc.add_text("path_exact", _key)        # compound key for deletion
            doc.add_text("content", content)
            doc.add_text("symbols", " ".join(symbols) if symbols else "")
            doc.add_integer("context_id", context_id)
            doc.add_integer("file_id", file_id)
            doc.add_text("worktree", worktree)
            writer.add_document(doc)
            writer.commit()
        finally:
            pass  # Writer is cleaned up automatically
    def add_files_batch(
        self,
        files: list[dict[str, Any]],
    ) -> int:
        """
        Add multiple files in a batch.
        Args:
            files: List of dicts with keys: path, content, context_id, file_id, symbols,
                   and optionally ``worktree`` (default ``"main"``)
        Returns:
            Number of files indexed.
        """
        self._ensure_initialized()
        writer = self._index.writer()
        count = 0
        try:
            for f in files:
                wt = f.get("worktree", "main")
                _key = f"{wt}:{f['path']}"
                # Delete existing entry for this (worktree, path).
                writer.delete_documents("path_exact", _key)
                # Add new
                doc = tantivy.Document()
                doc.add_text("path", f["path"])
                doc.add_text("path_exact", _key)        # compound deletion key
                doc.add_text("content", f.get("content", ""))
                doc.add_text("symbols", " ".join(f.get("symbols", [])))
                doc.add_integer("context_id", f.get("context_id", 0))
                doc.add_integer("file_id", f.get("file_id", 0))
                doc.add_text("worktree", wt)
                writer.add_document(doc)
                count += 1
            writer.commit()
        finally:
            pass
        return count
    def remove_file(self, file_path: str, worktree: str = "main") -> bool:
        """Remove a file from the index (immediate commit)."""
        self._ensure_initialized()
        writer = self._index.writer()
        try:
            deleted = writer.delete_documents("path_exact", f"{worktree}:{file_path}")
            writer.commit()
            return bool(deleted > 0)
        finally:
            pass
    # Staged Operations (for epoch atomicity)
    def stage_file(
        self,
        file_path: str,
        content: str,
        context_id: int,
        file_id: int = 0,
        symbols: list[str] | None = None,
        worktree: str = "main",
    ) -> None:
        """
        Stage a file for later atomic commit.
        Changes are buffered in memory until commit_staged() is called.
        This enables atomic epoch publishing where SQLite and Tantivy
        commits happen together.
        Args:
            file_path: Relative file path
            content: File content as string
            context_id: Context this file belongs to
            file_id: Database file ID
            symbols: List of symbol names in this file
            worktree: Worktree this file belongs to (default "main")
        """
        self._staged_adds.append(
            {
                "path": file_path,
                "content": content,
                "context_id": context_id,
                "file_id": file_id,
                "symbols": symbols or [],
                "worktree": worktree,
            }
        )
    def stage_remove(self, file_path: str, worktree: str = "main") -> None:
        """
        Stage a file removal for later atomic commit.
        Only removes the entry for the given *worktree*; entries for other
        worktrees are unaffected.
        Args:
            file_path: Relative file path to remove
            worktree: Worktree whose entry to remove (default "main")
        """
        self._staged_removes.append((worktree, file_path))
    def has_staged_changes(self) -> bool:
        """Return True if there are uncommitted staged changes."""
        return bool(self._staged_adds or self._staged_removes)
    def staged_count(self) -> tuple[int, int]:
        """Return (additions, removals) count of staged changes."""
        return len(self._staged_adds), len(self._staged_removes)
    def commit_staged(self) -> int:
        """
        Commit all staged changes atomically.
        This is the Tantivy-side of epoch publishing. Call this
        immediately before committing the SQLite epoch record.
        Returns:
            Number of documents affected (adds + removes)
        """
        if not self.has_staged_changes():
            return 0
        self._ensure_initialized()
        writer = self._index.writer()
        count = 0
        try:
            # Process removals first — each entry is (worktree, path).
            for wt_rm, file_path in self._staged_removes:
                writer.delete_documents("path_exact", f"{wt_rm}:{file_path}")
                count += 1
            # Process additions (which also delete the existing entry for this
            # worktree+path pair before inserting the new one).
            for f in self._staged_adds:
                wt = f.get("worktree", "main")
                _key = f"{wt}:{f['path']}"
                writer.delete_documents("path_exact", _key)
                # Add new document
                doc = tantivy.Document()
                doc.add_text("path", f["path"])
                doc.add_text("path_exact", _key)        # compound deletion key
                doc.add_text("content", f.get("content", ""))
                doc.add_text("symbols", " ".join(f.get("symbols", [])))
                doc.add_integer("context_id", f.get("context_id", 0))
                doc.add_integer("file_id", f.get("file_id", 0))
                doc.add_text("worktree", wt)
                writer.add_document(doc)
                count += 1
            # Single atomic commit
            writer.commit()
        except BaseException:
            # On failure, discard staging buffers.  The Tantivy writer
            # automatically rolls back uncommitted changes.
            self._staged_adds.clear()
            self._staged_removes.clear()
            raise
        # Clear staging buffers on success
        self._staged_adds.clear()
        self._staged_removes.clear()
        return count
    def discard_staged(self) -> int:
        """
        Discard all staged changes without committing.
        Returns:
            Number of staged changes discarded
        """
        count = len(self._staged_adds) + len(self._staged_removes)
        self._staged_adds.clear()
        self._staged_removes.clear()
        return count
    def _escape_query(self, query: str) -> str:
        from coderecon.index.search.lexical_search import _escape_query
        return _escape_query(query)

    def _build_tantivy_query(self, query: str) -> str:
        from coderecon.index.search.lexical_search import _build_tantivy_query
        return _build_tantivy_query(query)

    def search(
        self,
        query: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
        *,
        content_query: str | None = None,
        worktrees: list[str] | None = None,
    ) -> SearchResults:
        from coderecon.index.search.lexical_search import search
        return search(self, query, limit, context_id, context_lines, content_query=content_query, worktrees=worktrees)

    def search_symbols(
        self,
        query: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
        worktrees: list[str] | None = None,
    ) -> SearchResults:
        from coderecon.index.search.lexical_search import search_symbols
        return search_symbols(self, query, limit, context_id, context_lines, worktrees=worktrees)

    def search_path(
        self,
        pattern: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
        worktrees: list[str] | None = None,
    ) -> SearchResults:
        from coderecon.index.search.lexical_search import search_path
        return search_path(self, pattern, limit, context_id, context_lines, worktrees=worktrees)

    def _extract_search_terms(self, query: str, *, literal: bool = False) -> tuple:
        from coderecon.index.search.lexical_search import _extract_search_terms
        return _extract_search_terms(query, literal=literal)

    def _extract_all_snippets(self, content: str, query: str, context_lines: int = 1, *, literal: bool = False) -> list[tuple[str, int]]:
        from coderecon.index.search.lexical_search import _extract_all_snippets
        return _extract_all_snippets(content, query, context_lines, literal=literal)

    def _extract_snippet(self, content: str, query: str, context_lines: int = 1, *, literal: bool = False) -> tuple[str, int]:
        from coderecon.index.search.lexical_search import _extract_snippet
        return _extract_snippet(content, query, context_lines, literal=literal)

    def score_files_bm25(self, query: str, context_id: int | None = None, limit: int = 500, worktrees: list[str] | None = None) -> dict[str, float]:
        from coderecon.index.search.lexical_scoring import score_files_bm25
        return score_files_bm25(self, query, context_id, limit, worktrees=worktrees)

    def clear(self) -> None:
        """Clear all documents from the index."""
        self._ensure_initialized()
        writer = self._index.writer()
        try:
            writer.delete_all_documents()
            writer.commit()
        finally:
            pass
    def reload(self) -> None:
        """Reload the index to see latest changes."""
        if self._index:
            self._index.reload()
    def doc_count(self) -> int:
        """Return number of documents in the index."""
        self._ensure_initialized()
        searcher = self._index.searcher()
        return int(searcher.num_docs)
def create_index(index_path: Path | str) -> LexicalIndex:
    """Create a new lexical index."""
    return LexicalIndex(index_path)
