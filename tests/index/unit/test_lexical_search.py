"""Tests for index/_internal/indexing/lexical_search.py — search helper functions."""
from __future__ import annotations

from coderecon.index._internal.indexing.lexical_search import (
    _build_tantivy_query,
    _escape_query,
    _extract_search_terms,
)


class TestEscapeQuery:
    """Tests for _escape_query."""

    def test_plain_text_unchanged(self) -> None:
        assert _escape_query("hello") == "hello"

    def test_escapes_plus(self) -> None:
        assert _escape_query("a+b") == r"a\+b"

    def test_escapes_minus(self) -> None:
        assert _escape_query("a-b") == r"a\-b"

    def test_escapes_exclamation(self) -> None:
        assert _escape_query("!x") == r"\!x"

    def test_escapes_parentheses(self) -> None:
        assert _escape_query("(a)") == r"\(a\)"

    def test_escapes_brackets(self) -> None:
        assert _escape_query("[1]") == r"\[1\]"

    def test_escapes_quotes(self) -> None:
        assert _escape_query('"hi"') == r'\"hi\"'

    def test_escapes_colon(self) -> None:
        assert _escape_query("field:val") == r"field\:val"

    def test_escapes_spaces(self) -> None:
        assert _escape_query("a b") == r"a\ b"

    def test_escapes_multiple_special_chars(self) -> None:
        result = _escape_query("a+b-c!d")
        assert result == r"a\+b\-c\!d"

    def test_empty_string(self) -> None:
        assert _escape_query("") == ""


class TestBuildTantivyQuery:
    """Tests for _build_tantivy_query."""

    def test_empty_query_passthrough(self) -> None:
        assert _build_tantivy_query("") == ""

    def test_single_term_no_join(self) -> None:
        result = _build_tantivy_query("hello")
        assert result == "hello"

    def test_multiple_terms_joined_with_and(self) -> None:
        result = _build_tantivy_query("foo bar")
        assert result == "foo AND bar"

    def test_quoted_phrase_preserved(self) -> None:
        result = _build_tantivy_query('"async def"')
        assert result == '"async def"'

    def test_mixed_phrase_and_term(self) -> None:
        result = _build_tantivy_query('"async def" handler')
        assert result == '"async def" AND handler'

    def test_field_prefixed_term_preserved(self) -> None:
        result = _build_tantivy_query("symbols:foo bar")
        assert result == "symbols:foo AND bar"

    def test_explicit_or_preserved(self) -> None:
        result = _build_tantivy_query("foo OR bar")
        assert "OR" in result

    def test_explicit_and_preserved(self) -> None:
        result = _build_tantivy_query("foo AND bar")
        assert "AND" in result

    def test_explicit_not_preserved(self) -> None:
        result = _build_tantivy_query("foo NOT bar")
        assert "NOT" in result

    def test_special_chars_in_token_escaped(self) -> None:
        result = _build_tantivy_query("a+b c")
        assert r"\+" in result


class TestExtractSearchTerms:
    """Tests for _extract_search_terms."""

    def test_empty_query(self) -> None:
        groups, neg_terms, neg_phrases = _extract_search_terms("")
        assert groups == []
        assert neg_terms == []
        assert neg_phrases == []

    def test_single_term(self) -> None:
        groups, neg_terms, neg_phrases = _extract_search_terms("hello")
        assert len(groups) == 1
        phrases, terms = groups[0]
        assert terms == ["hello"]
        assert phrases == []
        assert neg_terms == []

    def test_multiple_terms_same_group(self) -> None:
        groups, _, _ = _extract_search_terms("foo bar")
        assert len(groups) == 1
        _, terms = groups[0]
        assert terms == ["foo", "bar"]

    def test_or_splits_groups(self) -> None:
        groups, _, _ = _extract_search_terms("foo OR bar")
        assert len(groups) == 2
        assert groups[0][1] == ["foo"]
        assert groups[1][1] == ["bar"]

    def test_not_creates_negative_term(self) -> None:
        groups, neg_terms, _ = _extract_search_terms("foo NOT bar")
        assert len(groups) == 1
        assert groups[0][1] == ["foo"]
        assert neg_terms == ["bar"]

    def test_quoted_phrase(self) -> None:
        groups, _, _ = _extract_search_terms('"async def"')
        assert len(groups) == 1
        phrases, terms = groups[0]
        assert phrases == ["async def"]
        assert terms == []

    def test_not_quoted_phrase(self) -> None:
        _, _, neg_phrases = _extract_search_terms('NOT "bad pattern"')
        assert neg_phrases == ["bad pattern"]

    def test_content_field_extracted(self) -> None:
        groups, _, _ = _extract_search_terms("content:widget")
        assert len(groups) == 1
        assert groups[0][1] == ["widget"]

    def test_path_field_skipped(self) -> None:
        groups, _, _ = _extract_search_terms("path:src/foo.py")
        assert groups == []

    def test_symbols_field_skipped(self) -> None:
        groups, _, _ = _extract_search_terms("symbols:MyClass")
        assert groups == []

    def test_literal_mode(self) -> None:
        groups, neg_terms, neg_phrases = _extract_search_terms(
            "foo OR bar", literal=True
        )
        assert len(groups) == 1
        _, terms = groups[0]
        assert terms == ["foo", "or", "bar"]
        assert neg_terms == []
        assert neg_phrases == []

    def test_and_is_implicit_skip(self) -> None:
        groups, _, _ = _extract_search_terms("foo AND bar")
        assert len(groups) == 1
        assert groups[0][1] == ["foo", "bar"]
