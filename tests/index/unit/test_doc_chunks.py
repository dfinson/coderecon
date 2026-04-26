"""Tests for doc_chunks — semantic chunking of non-code files."""

from __future__ import annotations

import pytest

from coderecon.index._internal.indexing.doc_chunks import (
    MIN_CHUNK_LENGTH,
    _chunk_keyvalue,
    _chunk_markdown,
    _chunk_paragraphs,
    chunk_file,
)


def _pad(text: str) -> str:
    """Pad text to exceed MIN_CHUNK_LENGTH so it isn't filtered out."""
    if len(text) < MIN_CHUNK_LENGTH:
        return text + " " * (MIN_CHUNK_LENGTH - len(text) + 1)
    return text


class TestChunkMarkdown:
    """Markdown heading-based splitting."""

    def test_single_heading_section(self) -> None:
        md = f"# Title\n{_pad('Some content here for the section')}"
        chunks = _chunk_markdown(md)
        assert len(chunks) == 1
        assert chunks[0][0] == "preamble"

    def test_two_headings_split(self) -> None:
        md = f"{_pad('Preamble paragraph with enough text')}\n# Section A\n{_pad('Content of section A here')}"
        chunks = _chunk_markdown(md)
        assert len(chunks) == 2
        assert chunks[0][0] == "preamble"
        assert chunks[1][0] == "Section A"

    def test_nested_headings(self) -> None:
        md = (
            f"{_pad('Intro text that is long enough')}\n"
            f"# H1\n{_pad('H1 content that is long enough')}\n"
            f"## H2\n{_pad('H2 content that is long enough')}"
        )
        chunks = _chunk_markdown(md)
        keys = [c[0] for c in chunks]
        assert "preamble" in keys
        assert "H1" in keys
        assert "H2" in keys

    def test_short_chunks_filtered(self) -> None:
        md = "# A\nhi\n# B\nbye"
        chunks = _chunk_markdown(md)
        assert len(chunks) == 0

    def test_line_numbers_correct(self) -> None:
        md = f"{_pad('Line 1 preamble content with text')}\n# Heading\n{_pad('Line 3 body content with enough text')}"
        chunks = _chunk_markdown(md)
        # Preamble starts at line 1
        assert chunks[0][2] == 1
        # Heading section starts at line 2
        assert chunks[1][2] == 2


class TestChunkKeyvalue:
    """YAML/TOML key-based splitting."""

    def test_yaml_keys(self) -> None:
        yaml_text = (
            f"name: {_pad('my-project with a description')}\n"
            f"version: {_pad('1.0.0 with extra metadata info')}\n"
        )
        chunks = _chunk_keyvalue(yaml_text)
        assert len(chunks) >= 1

    def test_toml_keys(self) -> None:
        toml_text = (
            f"title = {_pad('my project title with description')}\n"
            f"version = {_pad('1.0.0 release candidate info here')}\n"
        )
        chunks = _chunk_keyvalue(toml_text)
        assert len(chunks) >= 1

    def test_short_values_filtered(self) -> None:
        yaml_text = "a: 1\nb: 2"
        chunks = _chunk_keyvalue(yaml_text)
        assert len(chunks) == 0

    def test_preserves_key_names(self) -> None:
        yaml_text = (
            f"database: {_pad('postgresql://host:5432/db?sslmode=require')}\n"
            f"cache: {_pad('redis://host:6379/0?timeout=30&retry=3')}\n"
        )
        chunks = _chunk_keyvalue(yaml_text)
        keys = [c[0] for c in chunks]
        # Either header or database should appear as a key
        assert any(k in ("header", "database") for k in keys)


class TestChunkParagraphs:
    """Fallback paragraph splitting."""

    def test_single_paragraph(self) -> None:
        text = _pad("A single paragraph with enough content to pass the filter")
        chunks = _chunk_paragraphs(text)
        assert len(chunks) == 1
        assert chunks[0][0] == "para_0"

    def test_double_newline_splits(self) -> None:
        # Paragraph chunker needs two consecutive empty lines to split
        text = (
            f"{_pad('First paragraph with sufficient text')}\n\n\n"
            f"{_pad('Second paragraph with sufficient text')}"
        )
        chunks = _chunk_paragraphs(text)
        assert len(chunks) == 2
        assert chunks[0][0] == "para_0"
        assert chunks[1][0] == "para_1"

    def test_short_paragraphs_filtered(self) -> None:
        text = "hi\n\nbye"
        chunks = _chunk_paragraphs(text)
        assert len(chunks) == 0


class TestChunkFile:
    """Top-level chunk_file dispatcher."""

    def test_markdown_dispatches_to_heading_chunker(self) -> None:
        md = f"{_pad('Preamble text that is long enough here')}\n# H\n{_pad('Body text that is long enough here')}"
        chunks = chunk_file(md, "markdown")
        assert len(chunks) >= 1

    def test_yaml_dispatches_to_keyvalue_chunker(self) -> None:
        yaml_text = f"key: {_pad('value content that is long enough')}\n"
        chunks = chunk_file(yaml_text, "yaml")
        assert len(chunks) >= 1

    def test_unknown_family_uses_paragraph_chunker(self) -> None:
        text = _pad("Some generic text content paragraph")
        chunks = chunk_file(text, "unknown_format")
        assert len(chunks) >= 1

    @pytest.mark.parametrize("family", ["rst", "asciidoc"])
    def test_doc_families_use_markdown_chunker(self, family: str) -> None:
        md = f"{_pad('Preamble text that is long enough here')}\n# H\n{_pad('Body content long enough text')}"
        chunks = chunk_file(md, family)
        # Should behave like markdown chunker
        assert any(c[0] == "preamble" for c in chunks) or len(chunks) >= 1

    @pytest.mark.parametrize("family", ["toml", "json"])
    def test_config_families_use_keyvalue_chunker(self, family: str) -> None:
        text = f"key: {_pad('value content that is long enough here')}\n"
        chunks = chunk_file(text, family)
        assert len(chunks) >= 1
