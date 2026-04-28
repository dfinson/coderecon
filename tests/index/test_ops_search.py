"""Tests for coderecon.index.ops_search."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.index.ops_search import score_files_bm25, search, search_symbols
from coderecon.index.ops_types import SearchMode, SearchResponse, SearchResult


def _mock_engine(
    *,
    lexical: MagicMock | None = MagicMock(),
    session: MagicMock | None = None,
) -> MagicMock:
    """Build a mock IndexCoordinatorEngine."""
    engine = MagicMock()
    engine._lexical = lexical
    engine._search_worktrees = ["main"]
    engine.wait_for_freshness = AsyncMock()
    sess = session or MagicMock()
    engine.db.session.return_value.__enter__ = MagicMock(return_value=sess)
    engine.db.session.return_value.__exit__ = MagicMock(return_value=False)
    return engine


class TestScoreFilesBm25:
    """score_files_bm25 delegates to the lexical index."""

    def test_returns_scores_from_lexical(self) -> None:
        lexical = MagicMock()
        lexical.score_files_bm25.return_value = {"a.py": 1.5, "b.py": 0.8}
        engine = _mock_engine(lexical=lexical)

        result = score_files_bm25(engine, "query")
        assert result == {"a.py": 1.5, "b.py": 0.8}
        lexical.score_files_bm25.assert_called_once_with(
            "query", limit=500, worktrees=["main"]
        )

    def test_returns_empty_when_no_lexical(self) -> None:
        engine = _mock_engine(lexical=None)
        assert score_files_bm25(engine, "query") == {}


class TestSearch:
    """search() dispatches by mode and applies filters."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_lexical(self) -> None:
        engine = _mock_engine(lexical=None)
        resp = await search(engine, "query")
        assert resp.results == []

    @pytest.mark.asyncio
    async def test_text_mode_returns_results(self) -> None:
        hit = MagicMock()
        hit.file_path = "src/foo.py"
        hit.line = 10
        hit.column = 5
        hit.snippet = "def foo():"
        hit.score = 2.0

        lexical = MagicMock()
        search_resp = MagicMock()
        search_resp.results = [hit]
        search_resp.fallback_reason = None
        lexical.search.return_value = search_resp

        engine = _mock_engine(lexical=lexical)
        resp = await search(engine, "foo", mode=SearchMode.TEXT)

        assert len(resp.results) == 1
        assert resp.results[0].path == "src/foo.py"
        assert resp.results[0].line == 10

    @pytest.mark.asyncio
    async def test_path_mode_delegates_to_search_path(self) -> None:
        lexical = MagicMock()
        search_resp = MagicMock()
        search_resp.results = []
        search_resp.fallback_reason = None
        lexical.search_path.return_value = search_resp

        engine = _mock_engine(lexical=lexical)
        await search(engine, "foo", mode=SearchMode.PATH)

        lexical.search_path.assert_called_once()

    @pytest.mark.asyncio
    async def test_symbol_mode_delegates_to_search_symbols(self) -> None:
        engine = _mock_engine()
        with patch(
            "coderecon.index.ops_search.search_symbols",
            new_callable=AsyncMock,
            return_value=SearchResponse(results=[]),
        ) as mock_ss:
            resp = await search(engine, "MyClass", mode=SearchMode.SYMBOL)
            mock_ss.assert_called_once()
            assert resp.results == []

    @pytest.mark.asyncio
    async def test_language_filter_returns_empty_when_no_matches(self) -> None:
        session = MagicMock()
        session.exec.return_value.all.return_value = []  # no files match
        engine = _mock_engine(session=session)

        resp = await search(engine, "query", filter_languages=["haskell"])
        assert resp.results == []

    @pytest.mark.asyncio
    async def test_filter_paths_applied(self) -> None:
        hit1 = MagicMock(file_path="src/a.py", line=1, column=0, snippet="x", score=1.0)
        hit2 = MagicMock(file_path="tests/b.py", line=1, column=0, snippet="y", score=0.5)

        lexical = MagicMock()
        search_resp = MagicMock()
        search_resp.results = [hit1, hit2]
        search_resp.fallback_reason = None
        lexical.search.return_value = search_resp

        engine = _mock_engine(lexical=lexical)
        with patch(
            "coderecon.index.ops_search._matches_filter_paths",
            side_effect=lambda p, _: p.startswith("src/"),
        ):
            resp = await search(engine, "x", filter_paths=["src/**"])

        assert len(resp.results) == 1
        assert resp.results[0].path == "src/a.py"

    @pytest.mark.asyncio
    async def test_offset_and_limit(self) -> None:
        hits = [
            MagicMock(file_path=f"f{i}.py", line=i, column=0, snippet=f"s{i}", score=float(i))
            for i in range(5)
        ]
        lexical = MagicMock()
        search_resp = MagicMock()
        search_resp.results = hits
        search_resp.fallback_reason = None
        lexical.search.return_value = search_resp

        engine = _mock_engine(lexical=lexical)
        resp = await search(engine, "q", limit=2, offset=1)

        assert len(resp.results) == 2
        assert resp.results[0].path == "f1.py"
        assert resp.results[1].path == "f2.py"


class TestSearchSymbols:
    """search_symbols combines SQLite + Tantivy fallback."""

    @pytest.mark.asyncio
    async def test_returns_sqlite_results(self) -> None:
        def_fact = MagicMock()
        def_fact.start_line = 10
        def_fact.start_col = 4
        def_fact.display_name = "MyClass"
        def_fact.name = "MyClass"

        session = MagicMock()
        session.exec.return_value.all.return_value = [
            (def_fact, "src/foo.py", 1.0),
        ]
        engine = _mock_engine(session=session)

        resp = await search_symbols(engine, "MyClass")
        assert len(resp.results) == 1
        assert resp.results[0].path == "src/foo.py"
        assert resp.results[0].score == 1.0

    @pytest.mark.asyncio
    async def test_deduplication(self) -> None:
        """Same (path, line, col) should not produce duplicates."""
        def_fact = MagicMock()
        def_fact.start_line = 10
        def_fact.start_col = 0
        def_fact.display_name = "foo"
        def_fact.name = "foo"

        session = MagicMock()
        session.exec.return_value.all.return_value = [
            (def_fact, "a.py", 0.8),
            (def_fact, "a.py", 0.6),  # duplicate
        ]
        engine = _mock_engine(session=session)

        resp = await search_symbols(engine, "foo")
        assert len(resp.results) == 1

    @pytest.mark.asyncio
    async def test_tantivy_fallback_when_sqlite_underfills(self) -> None:
        """When SQLite returns fewer than limit, Tantivy is consulted."""
        session = MagicMock()
        session.exec.return_value.all.return_value = []  # no SQLite results
        lexical = MagicMock()
        tantivy_hit = MagicMock(file_path="fallback.py", line=5, column=0, snippet="fallback", score=0.3)
        tantivy_resp = MagicMock()
        tantivy_resp.results = [tantivy_hit]
        lexical.search_symbols.return_value = tantivy_resp

        engine = _mock_engine(lexical=lexical, session=session)
        resp = await search_symbols(engine, "rare_symbol")

        assert len(resp.results) == 1
        assert resp.results[0].path == "fallback.py"

    @pytest.mark.asyncio
    async def test_tantivy_fallback_skipped_when_filter_kinds(self) -> None:
        """Tantivy fallback is skipped when filter_kinds is set."""
        session = MagicMock()
        session.exec.return_value.all.return_value = []
        lexical = MagicMock()
        engine = _mock_engine(lexical=lexical, session=session)

        resp = await search_symbols(engine, "x", filter_kinds=["class"])
        lexical.search_symbols.assert_not_called()
        assert resp.results == []

    @pytest.mark.asyncio
    async def test_offset_skips_results(self) -> None:
        defs = []
        for i in range(3):
            d = MagicMock()
            d.start_line = i
            d.start_col = 0
            d.display_name = f"sym{i}"
            d.name = f"sym{i}"
            defs.append((d, f"f{i}.py", float(3 - i)))

        session = MagicMock()
        session.exec.return_value.all.return_value = defs
        engine = _mock_engine(session=session)

        resp = await search_symbols(engine, "sym", offset=1, limit=1)
        assert len(resp.results) == 1
        assert resp.results[0].path == "f1.py"

    @pytest.mark.asyncio
    async def test_path_filter_applied(self) -> None:
        def_fact = MagicMock()
        def_fact.start_line = 1
        def_fact.start_col = 0
        def_fact.display_name = "x"
        def_fact.name = "x"

        session = MagicMock()
        session.exec.return_value.all.return_value = [
            (def_fact, "tests/foo.py", 0.8),
        ]
        engine = _mock_engine(session=session)

        with patch(
            "coderecon.index.ops_search._matches_filter_paths",
            return_value=False,
        ):
            resp = await search_symbols(engine, "x", filter_paths=["src/**"])

        assert resp.results == []

    @pytest.mark.asyncio
    async def test_tantivy_fallback_deduplicates_with_sqlite(self) -> None:
        """Tantivy results that overlap with SQLite are skipped."""
        def_fact = MagicMock()
        def_fact.start_line = 10
        def_fact.start_col = 0
        def_fact.display_name = "foo"
        def_fact.name = "foo"

        session = MagicMock()
        session.exec.return_value.all.return_value = [(def_fact, "a.py", 1.0)]

        tantivy_hit = MagicMock(file_path="a.py", line=10, column=0, snippet="foo", score=0.3)
        tantivy_resp = MagicMock()
        tantivy_resp.results = [tantivy_hit]
        lexical = MagicMock()
        lexical.search_symbols.return_value = tantivy_resp

        engine = _mock_engine(lexical=lexical, session=session)
        resp = await search_symbols(engine, "foo", limit=10)

        # Only 1 result — deduped
        assert len(resp.results) == 1

    @pytest.mark.asyncio
    async def test_results_sorted_by_score_descending(self) -> None:
        defs = []
        for i, score in enumerate([0.6, 1.0, 0.8]):
            d = MagicMock()
            d.start_line = i
            d.start_col = 0
            d.display_name = f"s{i}"
            d.name = f"s{i}"
            defs.append((d, f"f{i}.py", score))

        session = MagicMock()
        session.exec.return_value.all.return_value = defs
        engine = _mock_engine(session=session)

        resp = await search_symbols(engine, "s")
        scores = [r.score for r in resp.results]
        assert scores == sorted(scores, reverse=True)
