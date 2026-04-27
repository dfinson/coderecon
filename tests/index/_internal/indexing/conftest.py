"""Shared fixtures for indexing tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_db() -> MagicMock:
    """Create mock database."""
    db = MagicMock()
    session = MagicMock()
    db.session.return_value.__enter__ = MagicMock(return_value=session)
    db.session.return_value.__exit__ = MagicMock(return_value=False)
    return db
