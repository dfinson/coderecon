"""Tests for docstring cross-reference resolution."""

from __future__ import annotations

import pytest

from coderecon.index._internal.analysis.docstring_xref import (
    RawCrossRef,
    extract_cross_refs,
)


class TestExtractCrossRefs:
    def test_sphinx_role(self) -> None:
        text = 'See :func:`module.func` for details.'
        refs = extract_cross_refs(text)
        assert len(refs) >= 1
        assert any(r.target_name == "module.func" for r in refs)
        assert any(r.confidence == "high" for r in refs)

    def test_sphinx_class_role(self) -> None:
        text = "Returns a :class:`MyClass` instance."
        refs = extract_cross_refs(text)
        assert any(r.target_name == "MyClass" for r in refs)

    def test_see_also(self) -> None:
        text = "See also FooBarClass for more."
        refs = extract_cross_refs(text)
        assert any(r.target_name == "FooBarClass" for r in refs)

    def test_markdown_link(self) -> None:
        text = "Check [MyHelper](path/to/file) for usage."
        refs = extract_cross_refs(text)
        assert any(r.target_name == "MyHelper" for r in refs)

    def test_qualified_name(self) -> None:
        text = "Uses package.module.ClassName internally."
        refs = extract_cross_refs(text)
        # Should catch as medium-confidence qualified name
        assert any("ClassName" in r.target_name for r in refs)

    def test_camelcase(self) -> None:
        text = "The HttpClient is responsible for connections."
        refs = extract_cross_refs(text)
        assert any(r.target_name == "HttpClient" for r in refs)
        assert any(r.confidence == "low" for r in refs)

    def test_returns_raises(self) -> None:
        text = "Returns: ResponseObject"
        refs = extract_cross_refs(text)
        assert any(r.target_name == "ResponseObject" for r in refs)

    def test_line_numbers(self) -> None:
        text = "line one\nSee :func:`bar`\nline three"
        refs = extract_cross_refs(text, start_line=10)
        func_ref = [r for r in refs if r.target_name == "bar"]
        assert len(func_ref) >= 1
        assert func_ref[0].source_line == 11

    def test_no_refs_in_code(self) -> None:
        text = "def my_function():\n    pass"
        refs = extract_cross_refs(text)
        # Should not pick up 'my_function' as a CamelCase ref
        assert not any(r.target_name == "my_function" for r in refs)

    def test_deduplication(self) -> None:
        text = "See :func:`foo` and also :func:`foo`"
        refs = extract_cross_refs(text)
        foo_refs = [r for r in refs if r.target_name == "foo"]
        # Same line, same target → deduped
        assert len(foo_refs) == 1

    def test_empty_input(self) -> None:
        refs = extract_cross_refs("")
        assert refs == []
