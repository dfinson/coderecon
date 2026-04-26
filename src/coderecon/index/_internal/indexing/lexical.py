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
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tantivy

if TYPE_CHECKING:
    pass

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
        version_file.write_text(json.dumps({"version": SCHEMA_VERSION}))

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

    # =========================================================================
    # Staged Operations (for epoch atomicity)
    # =========================================================================

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
        except (OSError, ValueError):
            # OSError: filesystem errors during commit
            # ValueError: tantivy index corruption or schema mismatch
            # On failure, changes are discarded (Tantivy writer rollback)
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
        r"""Escape special Tantivy query syntax characters for literal search.

        Escapes: + - && || ! ( ) { } [ ] ^ " ~ * ? : \ /
        """
        special_chars = r'+-&|!(){}[]^"~*?:\/ '
        escaped = []
        for char in query:
            if char in special_chars:
                escaped.append(f"\\{char}")
            else:
                escaped.append(char)
        return "".join(escaped)

    def _build_tantivy_query(self, query: str) -> str:
        """Build Tantivy query with AND semantics and phrase support.

        - Quoted strings (e.g., ``"async def"``) become Tantivy phrase queries.
        - Unquoted terms are joined with AND so all must appear.
        - Field-prefixed terms (e.g., ``symbols:foo``) are passed through.
        - Boolean operators (AND, OR, NOT) are preserved as-is.
        - Tantivy syntax characters in plain tokens are escaped.
        """
        tokens = re.findall(r'"[^"]+"|\S+', query)
        if not tokens:
            return query

        has_explicit_ops = any(
            t.upper() in ("AND", "OR", "NOT") for t in tokens if not t.startswith('"')
        )

        # Characters that are Tantivy query syntax operators
        _syntax_chars = set(r'+-&|!(){}[]^~*?\\/"')

        def _escape_token(token: str) -> str:
            """Escape Tantivy syntax chars in a plain token."""
            if not any(c in _syntax_chars for c in token):
                return token
            escaped: list[str] = []
            for ch in token:
                if ch in _syntax_chars:
                    escaped.append(f"\\{ch}")
                else:
                    escaped.append(ch)
            return "".join(escaped)

        # Known field prefixes that Tantivy should interpret
        _known_fields = frozenset(("content", "symbols", "path", "context_id"))

        parts: list[str] = []
        for token in tokens:
            if token.startswith('"') and token.endswith('"'):
                parts.append(token)
            elif token.upper() in ("AND", "OR", "NOT"):
                parts.append(token.upper())
            elif ":" in token and token.partition(":")[0] in _known_fields:
                parts.append(token)
            else:
                parts.append(_escape_token(token))

        if has_explicit_ops:
            # User provided explicit operators — preserve their structure
            return " ".join(parts)
        # No explicit operators — join with AND so all terms must match
        return " AND ".join(parts)

    def search(
        self,
        query: str,
        limit: int = 20,  # noqa: ARG002 - kept for API compat; callers handle limiting
        context_id: int | None = None,
        context_lines: int = 1,
        *,
        content_query: str | None = None,
        worktrees: list[str] | None = None,
    ) -> SearchResults:
        """
        Search the index.

        Args:
            query: Search query (supports Tantivy query syntax).
                Quoted strings are treated as exact phrases.
                Unquoted multi-term queries use AND semantics (all terms must appear).
            limit: Unused — all matches are returned; callers apply limits.
            context_id: Optional context to filter by
            context_lines: Lines of context before/after each match (default 1)
            content_query: Optional override for line-level content matching.
                When set, _extract_all_snippets uses this instead of `query`.
                Used by search_symbols to pass the original unprefixed terms.
            worktrees: Ordered list of worktrees to search.  Results from the
                first worktree take priority; subsequent entries act as fallback
                overlays (e.g. ``["feature-x", "main"]`` uses feature-x's
                version of a file when present, otherwise falls back to main).
                ``None`` / ``["main"]`` both default to main-only search.

        Returns:
            SearchResults with matching lines (one result per line occurrence),
            ordered by (path, line_number) for deterministic results.
            If query syntax is invalid, falls back to literal search
            and sets fallback_reason.
        """
        self._ensure_initialized()
        start = time.monotonic()

        results = SearchResults()
        fallback_reason: str | None = None
        literal_fallback = False

        effective_worktrees: list[str] = worktrees if worktrees else ["main"]
        # Worktree priority map: lower index = higher priority (used for dedup).
        wt_priority: dict[str, int] = {wt: i for i, wt in enumerate(effective_worktrees)}

        # Build the base query with AND semantics.
        tantivy_query = self._build_tantivy_query(query)

        # Prepend worktree filter: (worktree:A OR worktree:B OR ...).
        # Use phrase syntax (worktree:"value") so the raw tokenizer matches
        # the stored value verbatim for any character including / . -
        # (escaped-term syntax fails for / because the stored token is `feat/slash`
        # but the escaped query term becomes `feat\/slash` — no match).
        wt_filter = " OR ".join(
            'worktree:"{}"'.format(wt.replace("\\", "\\\\").replace('"', '\\"'))
            for wt in effective_worktrees
        )
        base_with_wt = f"({wt_filter}) AND ({tantivy_query})"

        full_query = (
            f"({base_with_wt}) AND context_id:{context_id}"
            if context_id is not None
            else base_with_wt
        )

        searcher = self._index.searcher()

        # Try to parse query; on syntax error, fall back to escaped literal search
        try:
            parsed = self._index.parse_query(full_query, ["content", "symbols", "path"])
        except ValueError as e:
            error_msg = str(e)
            fallback_reason = f"query syntax error: {error_msg[:50]}"

            # Escape the original query and retry
            escaped_query = self._escape_query(query)
            base_escaped = f"({wt_filter}) AND ({escaped_query})"
            escaped_full = (
                f"({base_escaped}) AND context_id:{context_id}"
                if context_id is not None
                else base_escaped
            )
            try:
                parsed = self._index.parse_query(escaped_full, ["content", "symbols", "path"])
            except ValueError:
                results.query_time_ms = int((time.monotonic() - start) * 1000)
                results.fallback_reason = "query could not be parsed even after escaping"
                return results

            if content_query is None:
                content_query = query
            literal_fallback = True

        # Fetch ALL matching documents — no BM25 doc limit.
        # Tantivy's value is the inverted index for fast token→file lookup;
        # we ignore BM25 scores and use deterministic (path, line) ordering.
        doc_limit = max(searcher.num_docs, 1)
        top_docs = searcher.search(parsed, limit=doc_limit).hits
        results.total_hits = len(top_docs)

        # Collect raw (worktree_priority, file_path, line_num, snippet, ctx_id).
        # We deduplicate by (path, line) keeping the highest-priority worktree
        # so that feature-branch versions shadow main-branch versions.
        raw: list[tuple[int, str, int, str, int | None]] = []
        for _score, doc_addr in top_docs:
            doc = searcher.doc(doc_addr)
            file_path = doc.get_first("path") or ""
            content = doc.get_first("content") or ""
            ctx_id = doc.get_first("context_id")
            doc_wt = doc.get_first("worktree") or "main"
            prio = wt_priority.get(doc_wt, len(effective_worktrees))

            snippet_query = content_query if content_query is not None else query
            for snippet, line_num in self._extract_all_snippets(
                content, snippet_query, context_lines, literal=literal_fallback
            ):
                raw.append((prio, file_path, line_num, snippet, ctx_id))

        # Dedup: for each (path, line) keep the entry from the highest-priority
        # worktree (lowest priority index = first in the overlay list).
        # Use a dict keyed by (path, line) storing the best seen entry.
        best: dict[tuple[str, int], tuple[int, str, int, str, int | None]] = {}
        for entry in raw:
            prio, fp, ln, snippet, ctx_id = entry
            key = (fp, ln)
            if key not in best or prio < best[key][0]:
                best[key] = entry
        # Sort by (path, line) for deterministic ordering
        kept = sorted(best.values(), key=lambda e: (e[1], e[2]))

        results.results = [
            SearchResult(
                file_path=fp,
                line=ln,
                column=0,
                snippet=snippet,
                score=1.0,
                context_id=ctx_id,
            )
            for _prio, fp, ln, snippet, ctx_id in kept
        ]

        results.query_time_ms = int((time.monotonic() - start) * 1000)
        results.fallback_reason = fallback_reason
        return results

    def search_symbols(
        self,
        query: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
        worktrees: list[str] | None = None,
    ) -> SearchResults:
        """Search only in symbol names."""
        self._ensure_initialized()

        # Prefix each non-operator, non-phrase token with symbols: so
        # _build_tantivy_query AND-joins them correctly as field queries.
        tokens = re.findall(r'"[^"]+"|\S+', query)
        prefixed = []
        for t in tokens:
            if t.startswith('"') or t.upper() in ("AND", "OR", "NOT") or ":" in t:
                prefixed.append(t)
            else:
                prefixed.append(f"symbols:{t}")
        symbol_query = " ".join(prefixed)
        return self.search(
            symbol_query, limit, context_id, context_lines,
            content_query=query, worktrees=worktrees,
        )

    def search_path(
        self,
        pattern: str,
        limit: int = 20,
        context_id: int | None = None,
        context_lines: int = 1,
        worktrees: list[str] | None = None,
    ) -> SearchResults:
        """Search in file paths."""
        self._ensure_initialized()

        path_query = f"path:{pattern}"
        # Path searches match by file path, not content. Pass empty content_query
        # so _extract_all_snippets returns a document-level match at line 1
        # instead of trying (and failing) to match path terms in file content.
        return self.search(
            path_query, limit, context_id, context_lines,
            content_query="", worktrees=worktrees,
        )

    def _extract_search_terms(
        self, query: str, *, literal: bool = False
    ) -> tuple[list[tuple[list[str], list[str]]], list[str], list[str]]:
        """Extract search terms from query, preserving boolean structure.

        Parses OR-groups, AND semantics within groups, NOT exclusions,
        and quoted phrases.  Field-prefixed terms for non-content fields
        (``path:``, ``symbols:``, ``context_id:``) are excluded;
        ``content:`` values are extracted as content terms.

        Args:
            query: The search query string.
            literal: When True, treat every whitespace-separated token as a
                plain content term (AND'd together).  No operator, field, or
                phrase interpretation.  Used for fallback/escaped queries.

        Returns:
            Tuple of ``(or_groups, negative_terms, negative_phrases)`` where:

            - **or_groups**: list of ``(phrases, terms)`` tuples connected
              by OR.  A line matches if ANY group matches.  Within a group
              ALL phrases and ALL terms must appear (AND semantics).
            - **negative_terms**: individual words that must NOT appear.
            - **negative_phrases**: quoted phrases that must NOT appear.
        """
        query_lower = query.lower()

        # Literal mode: treat every token as a plain content term
        if literal:
            terms = query_lower.split()
            if terms:
                return [([], terms)], [], []
            return [], [], []

        # Tokenise: preserve quoted phrases as single tokens
        tokens = re.findall(r'"[^"]+"|\S+', query_lower)

        # Split tokens into OR-separated groups, tracking NOT
        or_groups: list[tuple[list[str], list[str]]] = []
        negative_terms: list[str] = []
        negative_phrases: list[str] = []

        current_phrases: list[str] = []
        current_terms: list[str] = []
        negate_next = False

        # Content-field prefixes whose values should be treated as content terms
        _content_fields = frozenset(("content",))
        # Non-content field prefixes to skip entirely
        _skip_fields = frozenset(("path", "symbols", "context_id"))

        for token in tokens:
            upper = token.upper()

            if upper == "OR":
                # Flush current group
                if current_phrases or current_terms:
                    or_groups.append((current_phrases, current_terms))
                    current_phrases = []
                    current_terms = []
                negate_next = False
                continue

            if upper == "AND":
                # Implicit anyway — just skip
                continue

            if upper == "NOT":
                negate_next = True
                continue

            # Quoted phrase
            if token.startswith('"') and token.endswith('"') and len(token) > 2:
                phrase = token[1:-1]
                if negate_next:
                    negative_phrases.append(phrase)
                    negate_next = False
                else:
                    current_phrases.append(phrase)
                continue

            # Field-prefixed token
            if ":" in token:
                field, _, value = token.partition(":")
                if field in _content_fields and value:
                    # content:X — the value matches file content
                    if negate_next:
                        negative_terms.append(value)
                    else:
                        current_terms.append(value)
                # Skip non-content fields (path:, symbols:, context_id:)
                elif field in _skip_fields:
                    pass
                else:
                    # Unknown field prefix — treat whole token as literal
                    if negate_next:
                        negative_terms.append(token)
                    else:
                        current_terms.append(token)
                negate_next = False
                continue

            # Plain term
            if negate_next:
                negative_terms.append(token)
                negate_next = False
            else:
                current_terms.append(token)

        # Flush last group
        if current_phrases or current_terms:
            or_groups.append((current_phrases, current_terms))

        return or_groups, negative_terms, negative_phrases

    def _extract_all_snippets(
        self,
        content: str,
        query: str,
        context_lines: int = 1,
        *,
        literal: bool = False,
    ) -> list[tuple[str, int]]:
        """Extract snippets for ALL lines matching the query.

        Evaluates boolean structure: OR-groups are alternatives, NOT terms
        are excluded, and terms within a group are AND'd.

        Args:
            content: File content
            query: Search query
            context_lines: Lines of context before and after match (default 1)
            literal: Treat all tokens as plain literal terms (no operators)

        Returns:
            List of (snippet_text, line_number) tuples where line_number is 1-indexed.
            Returns empty list when no lines match (caller should skip the document).
        """
        lines = content.split("\n")
        or_groups, negative_terms, negative_phrases = self._extract_search_terms(
            query, literal=literal
        )

        if not or_groups and not negative_terms and not negative_phrases:
            # No content-level search terms (e.g., field-only query like path:foo).
            # Tantivy matched this document by a non-content field, so return
            # a document-level match at line 1.
            snippet_size = 1 + 2 * context_lines
            return [("\n".join(lines[:snippet_size]), 1)]

        # Find ALL lines matching the boolean structure
        matches: list[tuple[str, int]] = []
        for i, line in enumerate(lines):
            line_lower = line.lower()

            # Negative terms/phrases: skip line if any are present
            if any(nt in line_lower for nt in negative_terms):
                continue
            if any(np in line_lower for np in negative_phrases):
                continue

            # OR-groups: line matches if ANY group matches.
            # Within a group ALL phrases AND ALL terms must appear.
            matched = False
            if not or_groups:
                # Only negative constraints and no positive terms —
                # every line that survives the negative filter matches.
                matched = True
            else:
                for phrases, terms in or_groups:
                    if all(p in line_lower for p in phrases) and all(
                        t in line_lower for t in terms
                    ):
                        matched = True
                        break

            if not matched:
                continue

            # Build context snippet
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            snippet = "\n".join(lines[start:end])
            matches.append((snippet, i + 1))  # 1-indexed

        return matches

    def _extract_snippet(
        self,
        content: str,
        query: str,
        context_lines: int = 1,
        *,
        literal: bool = False,
    ) -> tuple[str, int]:
        """Extract first snippet matching the query.

        Returns:
            Tuple of (snippet_text, line_number) where line_number is 1-indexed.
            Returns empty snippet at line 1 when no content lines match.
        """
        matches = self._extract_all_snippets(content, query, context_lines, literal=literal)
        if matches:
            return matches[0]
        return ("", 1)

    def score_files_bm25(
        self,
        query: str,
        context_id: int | None = None,
        limit: int = 500,
        worktrees: list[str] | None = None,
    ) -> dict[str, float]:
        """Score files by BM25 relevance to *query* using Tantivy.

        This is **parallel plumbing** — it does NOT touch the existing
        ``search()`` flow (which ignores BM25 scores and returns per-line
        matches).  Instead it returns a ``{path: max_bm25_score}`` map
        suitable for gating/ranking in downstream consumers like recon.

        Differences from ``search()``:
        - Uses **OR** semantics (any term matches) so partial overlap still
          scores.
        - Returns the **max BM25 score per file** (when Tantivy finds a
          document, the score reflects how well its content matches the
          query).
        - Does NOT extract snippets — purely a scoring pass.
        - A file absent from the returned dict has zero relevance.

        Args:
            query: Natural-language query (task description).
            context_id: Optional context filter.
            limit: Max documents to score (default 500, covers most repos).

        Returns:
            Dict mapping repo-relative file path → BM25 score (> 0).
        """
        self._ensure_initialized()

        # Build an OR query from the terms so partial overlap still yields
        # a positive score.  Quoted phrases are kept intact.
        # Tokenize on whitespace, strip punctuation that isn't part of
        # meaningful identifiers, and escape Tantivy syntax characters.
        raw_tokens = re.findall(r'"[^"]+"|\S+', query)
        if not raw_tokens:
            return {}

        # Tantivy query syntax characters (including : for field prefix,
        # . and , which commonly appear in natural language task text).
        _syntax_chars = set(r'+-&|!(){}[]^~*?:\\/".@,;')

        def _clean_token(tok: str) -> str:
            """Strip Tantivy syntax chars from a token entirely.

            For BM25 scoring we want plain words, not escaped operators.
            Stripping is safer than escaping because some characters
            (notably ``:`` for field prefixes) cause parse errors even
            when escaped in certain positions.
            """
            cleaned = "".join(ch for ch in tok if ch not in _syntax_chars)
            return cleaned

        parts: list[str] = []
        for token in raw_tokens:
            upper = token.upper()
            if upper in ("AND", "OR", "NOT"):
                continue  # strip boolean operators from the task text
            if token.startswith('"') and token.endswith('"'):
                # Strip quotes and clean the inner text
                inner = token[1:-1]
                cleaned = _clean_token(inner)
                if cleaned:
                    parts.append(f'"{cleaned}"')
            else:
                cleaned = _clean_token(token)
                if cleaned and len(cleaned) >= 2:  # skip single-char noise
                    parts.append(cleaned)

        if not parts:
            return {}

        # OR-join so any token contributes score (unlike search() which AND-joins)
        or_query = " OR ".join(parts)

        # Prepend worktree filter so BM25 scores only cover the relevant worktrees.
        effective_wt = worktrees if worktrees else ["main"]
        # Use phrase syntax so the raw tokenizer matches verbatim (see search() for rationale).
        wt_filter = " OR ".join(
            'worktree:"{}"'.format(wt.replace("\\", "\\\\").replace('"', '\\"'))
            for wt in effective_wt
        )
        content_expr = f"({wt_filter}) AND ({or_query})"

        full_query = (
            f"({content_expr}) AND context_id:{context_id}"
            if context_id is not None
            else content_expr
        )

        searcher = self._index.searcher()

        try:
            parsed = self._index.parse_query(full_query, ["content", "symbols", "path"])
        except ValueError:
            # Bad syntax — try escaping the whole thing
            escaped = self._escape_query(query)
            content_esc = f"({wt_filter}) AND ({escaped})"
            full_esc = (
                f"({content_esc}) AND context_id:{context_id}"
                if context_id is not None
                else content_esc
            )
            try:
                parsed = self._index.parse_query(full_esc, ["content", "symbols", "path"])
            except ValueError:
                return {}

        top_docs = searcher.search(parsed, limit=limit).hits

        # For the overlay case, prefer the first (higher-priority) worktree's
        # score when a path appears under multiple worktrees.
        wt_priority: dict[str, int] = {wt: i for i, wt in enumerate(effective_wt)}
        scores: dict[str, float] = {}
        path_wt: dict[str, int] = {}  # path -> best worktree priority so far
        for bm25_score, doc_addr in top_docs:
            doc = searcher.doc(doc_addr)
            file_path = doc.get_first("path") or ""
            if not file_path:
                continue
            doc_wt = doc.get_first("worktree") or "main"
            prio = wt_priority.get(doc_wt, len(effective_wt))
            prev_prio = path_wt.get(file_path, len(effective_wt) + 1)
            if prio < prev_prio or (prio == prev_prio and float(bm25_score) > scores.get(file_path, 0.0)):
                scores[file_path] = float(bm25_score)
                path_wt[file_path] = prio

        return scores

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
