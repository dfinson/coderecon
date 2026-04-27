"""Smoke test for splade_db module."""


def test_splade_db_importable():
    # Import splade first to avoid circular import (splade <-> splade_db)
    import coderecon.index._internal.indexing.splade  # noqa: F401
    from coderecon.index._internal.indexing.splade_db import (
        load_all_vectors_fast,
        retrieve_splade,
    )
    assert callable(load_all_vectors_fast)
    assert callable(retrieve_splade)
