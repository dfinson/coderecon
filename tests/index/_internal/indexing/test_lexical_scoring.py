"""Tests for index/_internal/indexing/lexical_scoring.py."""

from __future__ import annotations

from unittest.mock import MagicMock


from coderecon.index._internal.indexing.lexical_scoring import score_files_bm25


def _make_mock_index(
    hits: list[tuple[float, object]] | None = None,
    parse_error: bool = False,
) -> MagicMock:
    """Build a mock LexicalIndex with controllable searcher/parse_query."""
    index = MagicMock()
    inner = MagicMock()
    index._index = inner

    searcher = MagicMock()
    inner.searcher.return_value = searcher

    if parse_error:
        inner.parse_query.side_effect = ValueError("bad query")
    else:
        inner.parse_query.return_value = MagicMock()

    # Build doc lookup: each hit needs a doc with path and worktree
    docs: dict[int, dict[str, str]] = {}
    address_list: list[tuple[float, int]] = []
    for i, (score, doc_data) in enumerate(hits or []):
        docs[i] = doc_data  # type: ignore[assignment]
        address_list.append((score, i))

    search_result = MagicMock()
    search_result.hits = address_list
    searcher.search.return_value = search_result

    def doc_fn(addr: int) -> MagicMock:
        d = MagicMock()
        data = docs.get(addr, {})
        d.get_first.side_effect = lambda field: data.get(field, "")
        return d

    searcher.doc.side_effect = doc_fn

    return index


class TestScoreFilesBm25:
    """Tests for score_files_bm25 — BM25 scoring via Tantivy."""

    def test_empty_query_returns_empty(self) -> None:
        index = _make_mock_index()
        result = score_files_bm25(index, "")
        assert result == {}

    def test_whitespace_only_query_returns_empty(self) -> None:
        index = _make_mock_index()
        result = score_files_bm25(index, "   ")
        assert result == {}

    def test_single_char_tokens_stripped(self) -> None:
        """Tokens shorter than 2 chars are dropped; if all stripped, empty result."""
        index = _make_mock_index()
        result = score_files_bm25(index, "a b c")
        assert result == {}

    def test_boolean_operators_stripped(self) -> None:
        """AND, OR, NOT keywords from task text are removed."""
        index = _make_mock_index(hits=[
            (1.5, {"path": "foo.py", "worktree": "main"}),
        ])
        score_files_bm25(index, "AND OR NOT hello world")
        # parse_query should be called — the remaining tokens "hello", "world" form a query
        index._index.parse_query.assert_called_once()
        query_str = index._index.parse_query.call_args[0][0]
        assert "AND" not in query_str.split(" OR ")  # no literal "AND" as a search term
        assert "hello" in query_str
        assert "world" in query_str

    def test_syntax_chars_stripped_from_tokens(self) -> None:
        """Tantivy syntax characters are stripped, not escaped."""
        index = _make_mock_index(hits=[])
        score_files_bm25(index, "foo:bar baz()")
        query_str = index._index.parse_query.call_args[0][0]
        # Colons and parens should be stripped
        assert ":" not in query_str.replace("context_id:", "").replace('worktree:', '')
        assert "foobar" in query_str
        assert "baz" in query_str

    def test_quoted_phrase_preserved(self) -> None:
        """Quoted phrases should remain quoted in the output query."""
        index = _make_mock_index(hits=[])
        score_files_bm25(index, '"async def" handler')
        query_str = index._index.parse_query.call_args[0][0]
        assert '"async def"' in query_str

    def test_results_aggregated_by_path(self) -> None:
        """Multiple hits for the same path should keep the max score."""
        index = _make_mock_index(hits=[
            (2.0, {"path": "a.py", "worktree": "main"}),
            (3.0, {"path": "a.py", "worktree": "main"}),
            (1.0, {"path": "b.py", "worktree": "main"}),
        ])
        result = score_files_bm25(index, "some query text")
        assert result["a.py"] == 3.0
        assert result["b.py"] == 1.0

    def test_worktree_priority(self) -> None:
        """Earlier worktree in the list should win for the same path."""
        index = _make_mock_index(hits=[
            (1.0, {"path": "a.py", "worktree": "feature"}),
            (5.0, {"path": "a.py", "worktree": "main"}),
        ])
        result = score_files_bm25(
            index, "some query text", worktrees=["feature", "main"],
        )
        # feature is priority 0, main is priority 1 — feature wins
        assert result["a.py"] == 1.0

    def test_context_id_filter_added(self) -> None:
        """When context_id is set, the query should include it."""
        index = _make_mock_index(hits=[])
        score_files_bm25(index, "hello world", context_id=42)
        query_str = index._index.parse_query.call_args[0][0]
        assert "context_id:42" in query_str

    def test_context_id_none_omitted(self) -> None:
        """When context_id is None, it should not appear in the query."""
        index = _make_mock_index(hits=[])
        score_files_bm25(index, "hello world", context_id=None)
        query_str = index._index.parse_query.call_args[0][0]
        assert "context_id" not in query_str

    def test_parse_error_falls_back_to_escaped(self) -> None:
        """When parse_query raises ValueError, the fallback escape path is tried."""
        index = MagicMock()
        inner = MagicMock()
        index._index = inner

        searcher = MagicMock()
        inner.searcher.return_value = searcher

        call_count = 0

        def parse_side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("bad syntax")
            return MagicMock()

        inner.parse_query.side_effect = parse_side_effect

        search_result = MagicMock()
        search_result.hits = []
        searcher.search.return_value = search_result

        result = score_files_bm25(index, "hello world")
        assert result == {}
        assert call_count == 2  # first call fails, second (escaped) succeeds

    def test_double_parse_error_returns_empty(self) -> None:
        """When both parse attempts fail, return empty dict."""
        index = _make_mock_index(parse_error=True)
        result = score_files_bm25(index, "hello world")
        assert result == {}

    def test_empty_path_doc_skipped(self) -> None:
        """Documents with empty path should be excluded from results."""
        index = _make_mock_index(hits=[
            (2.0, {"path": "", "worktree": "main"}),
            (1.0, {"path": "real.py", "worktree": "main"}),
        ])
        result = score_files_bm25(index, "some query text")
        assert "" not in result
        assert result["real.py"] == 1.0

    def test_custom_worktrees_in_filter(self) -> None:
        """Custom worktrees list should appear in the query filter."""
        index = _make_mock_index(hits=[])
        score_files_bm25(index, "hello world", worktrees=["feat-a", "feat-b"])
        query_str = index._index.parse_query.call_args[0][0]
        assert 'worktree:"feat-a"' in query_str
        assert 'worktree:"feat-b"' in query_str

    def test_ensure_initialized_called(self) -> None:
        """_ensure_initialized must be called before any indexing."""
        index = _make_mock_index()
        score_files_bm25(index, "")
        index._ensure_initialized.assert_called_once()
