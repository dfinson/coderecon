"""Tests for index/_internal/indexing/doc_chunks.py — chunk_file and chunking logic."""

from coderecon.index.structural.doc_chunks import (
    chunk_file,
)
from coderecon.index.models import LanguageFamily

def test_chunk_markdown_single_heading():
    """Markdown with one heading produces one chunk."""
    text = "# Overview\n\nThis is a long enough section with content to meet the minimum length threshold easily."
    chunks = chunk_file(text, LanguageFamily.MARKDOWN)
    assert len(chunks) >= 1
    assert chunks[0][0] == "preamble" or chunks[0][0] == "Overview"

def test_chunk_markdown_multiple_headings():
    """Markdown with multiple headings splits at heading boundaries."""
    text = (
        "# Introduction\n\n"
        "This is the introduction section with enough text to pass the minimum length check.\n\n"
        "# Details\n\n"
        "This is the details section with enough text to also pass the minimum length check.\n"
    )
    chunks = chunk_file(text, LanguageFamily.MARKDOWN)
    assert len(chunks) >= 2
    keys = [c[0] for c in chunks]
    assert "Introduction" in keys or "preamble" in keys

def test_chunk_markdown_short_section_skipped():
    """Markdown sections shorter than MIN_CHUNK_LENGTH are skipped."""
    text = "# H1\n\nhi\n\n# H2\n\nThis section has enough text to meet the minimum chunk length threshold requirement."
    chunks = chunk_file(text, LanguageFamily.MARKDOWN)
    # "hi" section should be skipped
    for chunk in chunks:
        assert len(chunk[1]) >= 30

def test_chunk_keyvalue_yaml():
    """YAML text is chunked by top-level keys."""
    text = (
        "name: my-project\n"
        "version: 1.0.0\n"
        "description: A project with a description that is long enough\n\n"
        "dependencies:\n"
        "  click: '>=8.0'\n"
        "  structlog: '>=21.0'\n"
        "  sqlalchemy: '>=2.0'\n"
    )
    chunks = chunk_file(text, LanguageFamily.YAML)
    assert len(chunks) >= 1

def test_chunk_keyvalue_toml():
    """TOML text is chunked by top-level keys."""
    text = (
        "[project]\n"
        "name = 'my-project'\n"
        "version = '1.0.0'\n"
        "description = 'A project with a long enough description'\n\n"
        "[dependencies]\n"
        "click = '>=8.0'\n"
        "structlog = '>=21.0'\n"
    )
    chunks = chunk_file(text, LanguageFamily.TOML)
    assert len(chunks) >= 1

def test_chunk_paragraphs_fallback():
    """Non-markdown/yaml text uses paragraph splitting."""
    text = (
        "First paragraph with enough text content to pass the minimum threshold.\n\n\n"
        "Second paragraph also has plenty of text to exceed thirty characters.\n"
    )
    chunks = chunk_file(text, LanguageFamily.MAKE)
    assert len(chunks) >= 1

def test_chunk_file_returns_tuples():
    """chunk_file returns list of (key, text, start_line, end_line) tuples."""
    text = "# Title\n\nBody of the document with sufficient content to meet length requirements.\n"
    chunks = chunk_file(text, LanguageFamily.MARKDOWN)
    for chunk in chunks:
        assert len(chunk) == 4
        key, body, start, end = chunk
        assert isinstance(key, str)
        assert isinstance(body, str)
        assert isinstance(start, int)
        assert isinstance(end, int)
        assert start <= end

def test_chunk_file_empty_text():
    """chunk_file returns empty list for empty text."""
    chunks = chunk_file("", LanguageFamily.MARKDOWN)
    assert chunks == []
