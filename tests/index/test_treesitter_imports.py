"""Smoke test for treesitter_imports module."""

from coderecon.index.parsing.treesitter_imports import (
    _extract_imports_declarative,
    _process_python_import_node,
)


def test_functions_are_callable():
    assert callable(_extract_imports_declarative)
    assert callable(_process_python_import_node)
