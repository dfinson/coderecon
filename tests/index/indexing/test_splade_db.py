"""Smoke test for splade_db module."""


def test_splade_db_importable():
    # Import splade first to avoid circular import (splade <-> splade_db)
    import coderecon.index.search.splade  # noqa: F401
    from coderecon.index.search.splade_db import (
        load_all_vectors_fast,
        retrieve_splade,
    )
    assert callable(load_all_vectors_fast)
    assert callable(retrieve_splade)
