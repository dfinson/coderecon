"""Smoke test for ops_indexing module."""

from coderecon.index.ops_indexing import batch_get_defs


def test_batch_get_defs_is_callable():
    assert callable(batch_get_defs)
