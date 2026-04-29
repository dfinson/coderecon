"""Search functions extracted from LexicalIndex.

Standalone functions that operate on a ``LexicalIndex`` instance
passed as the first parameter.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from coderecon.config.constants import MS_PER_SEC
from coderecon.index.search.lexical import SearchResult, SearchResults

if TYPE_CHECKING:
    from coderecon.index.search.lexical import LexicalIndex


def _escape_query(query: str) -> str:
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


def _build_tantivy_query(query: str) -> str:
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


def _extract_search_terms(
    query: str, *, literal: bool = False
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
    or_groups, negative_terms, negative_phrases = _extract_search_terms(
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
    matches = _extract_all_snippets(content, query, context_lines, literal=literal)
    if matches:
        return matches[0]
    return ("", 1)


def search(
    index: LexicalIndex,
    query: str,
    limit: int = 20,  # noqa: ARG001 - kept for API compat; callers handle limiting
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
    index._ensure_initialized()
    start = time.monotonic()

    results = SearchResults()
    fallback_reason: str | None = None
    literal_fallback = False
    effective_worktrees: list[str] = worktrees if worktrees else ["main"]

    # Worktree priority map: lower index = higher priority (used for dedup).
    wt_priority: dict[str, int] = {wt: i for i, wt in enumerate(effective_worktrees)}

    # Build the base query with AND semantics.
    tantivy_query = _build_tantivy_query(query)

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

    searcher = index._index.searcher()

    # Try to parse query; on syntax error, fall back to escaped literal search
    try:
        parsed = index._index.parse_query(full_query, ["content", "symbols", "path"])
    except ValueError as e:
        error_msg = str(e)
        fallback_reason = f"query syntax error: {error_msg[:50]}"

        # Escape the original query and retry
        escaped_query = _escape_query(query)
        base_escaped = f"({wt_filter}) AND ({escaped_query})"
        escaped_full = (
            f"({base_escaped}) AND context_id:{context_id}"
            if context_id is not None
            else base_escaped
        )
        try:
            parsed = index._index.parse_query(escaped_full, ["content", "symbols", "path"])
        except ValueError:
            results.query_time_ms = int((time.monotonic() - start) * MS_PER_SEC)
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
        for snippet, line_num in _extract_all_snippets(
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

    results.query_time_ms = int((time.monotonic() - start) * MS_PER_SEC)
    results.fallback_reason = fallback_reason
    return results


def search_symbols(
    index: LexicalIndex,
    query: str,
    limit: int = 20,
    context_id: int | None = None,
    context_lines: int = 1,
    worktrees: list[str] | None = None,
) -> SearchResults:
    """Search only in symbol names."""
    index._ensure_initialized()

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

    return search(
        index, symbol_query, limit, context_id, context_lines,
        content_query=query, worktrees=worktrees,
    )


def search_path(
    index: LexicalIndex,
    pattern: str,
    limit: int = 20,
    context_id: int | None = None,
    context_lines: int = 1,
    worktrees: list[str] | None = None,
) -> SearchResults:
    """Search in file paths."""
    index._ensure_initialized()

    path_query = f"path:{pattern}"
    # Path searches match by file path, not content. Pass empty content_query
    # so _extract_all_snippets returns a document-level match at line 1
    # instead of trying (and failing) to match path terms in file content.
    return search(
        index, path_query, limit, context_id, context_lines,
        content_query="", worktrees=worktrees,
    )
