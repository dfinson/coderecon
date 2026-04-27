"""Smoke test for structural_extract module."""

from coderecon.index._internal.indexing.structural_extract import _extract_file


def test_extract_file_is_callable():
    assert callable(_extract_file)
