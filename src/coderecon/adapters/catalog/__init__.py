"""Global catalog for multi-repo management.

Provides a SQLite-backed registry at ~/.coderecon/catalog.db that tracks
all registered repositories and their worktrees.
"""

from __future__ import annotations

from coderecon.adapters.catalog.db import CatalogDB
from coderecon.adapters.catalog.models import RepoEntry, WorktreeEntry
from coderecon.adapters.catalog.registry import CatalogRegistry

__all__ = [
    "CatalogDB",
    "CatalogRegistry",
    "RepoEntry",
    "WorktreeEntry",
]
