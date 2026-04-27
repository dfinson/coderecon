"""Root conftest.py for test configuration.

Ensures local src/ directory takes priority over any installed packages.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Insert local src directory at the beginning of sys.path
# This ensures that the local coderecon package is used, not any installed one
_src_dir = Path(__file__).parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Force reimport of coderecon modules if already imported
for module_name in list(sys.modules.keys()):
    if module_name.startswith("coderecon"):
        del sys.modules[module_name]


@pytest.fixture
def registry(tmp_path: Path) -> "CatalogRegistry":
    """Create a CatalogRegistry backed by a temporary database."""
    from coderecon.catalog.db import CatalogDB
    from coderecon.catalog.registry import CatalogRegistry

    catalog = CatalogDB(home=tmp_path / ".coderecon")
    return CatalogRegistry(catalog)
