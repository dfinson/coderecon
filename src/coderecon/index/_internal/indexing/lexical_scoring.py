"""BM25 scoring functions extracted from LexicalIndex."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from coderecon.index._internal.indexing.lexical_search import _escape_query

if TYPE_CHECKING:
    from coderecon.index._internal.indexing.lexical import LexicalIndex

# Tantivy query syntax characters (including : for field prefix,
# . and , which commonly appear in natural language task text).
_SYNTAX_CHARS = set(r'+-&|!(){}[]^~*?:\\/".@,;')


def _clean_token(tok: str) -> str:
    """Strip Tantivy syntax chars from a token entirely.

    For BM25 scoring we want plain words, not escaped operators.
    Stripping is safer than escaping because some characters
    (notably ``:`` for field prefixes) cause parse errors even
    when escaped in certain positions.
    """
    return "".join(ch for ch in tok if ch not in _SYNTAX_CHARS)


def _build_bm25_query(
    query: str,
    context_id: int | None,
    worktrees: list[str],
) -> str | None:
    """Build an OR-joined BM25 query string from natural language.

    Returns None if no usable tokens remain after cleaning.
    """
    raw_tokens = re.findall(r'"[^"]+"|\S+', query)
    if not raw_tokens:
        return None

    parts: list[str] = []
    for token in raw_tokens:
        upper = token.upper()
        if upper in ("AND", "OR", "NOT"):
            continue  # strip boolean operators from the task text
        if token.startswith('"') and token.endswith('"'):
            cleaned = _clean_token(token[1:-1])
            if cleaned:
                parts.append(f'"{cleaned}"')
        else:
            cleaned = _clean_token(token)
            if cleaned and len(cleaned) >= 2:  # skip single-char noise
                parts.append(cleaned)

    if not parts:
        return None

    or_query = " OR ".join(parts)
    wt_filter = " OR ".join(
        'worktree:"{}"'.format(wt.replace("\\", "\\\\").replace('"', '\\"'))
        for wt in worktrees
    )
    content_expr = f"({wt_filter}) AND ({or_query})"
    if context_id is not None:
        return f"({content_expr}) AND context_id:{context_id}"
    return content_expr


def score_files_bm25(
    index: LexicalIndex,
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
    index._ensure_initialized()

    effective_wt = worktrees if worktrees else ["main"]
    full_query = _build_bm25_query(query, context_id, effective_wt)
    if full_query is None:
        return {}

    searcher = index._index.searcher()
    try:
        parsed = index._index.parse_query(full_query, ["content", "symbols", "path"])
    except ValueError:
        # Bad syntax — try escaping the whole thing
        escaped = _escape_query(query)
        wt_filter = " OR ".join(
            'worktree:"{}"'.format(wt.replace("\\", "\\\\").replace('"', '\\"'))
            for wt in effective_wt
        )
        content_esc = f"({wt_filter}) AND ({escaped})"
        full_esc = (
            f"({content_esc}) AND context_id:{context_id}"
            if context_id is not None
            else content_esc
        )
        try:
            parsed = index._index.parse_query(full_esc, ["content", "symbols", "path"])
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
