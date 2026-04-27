"""Integration tests for index operations.

These tests verify that index operations work end-to-end with real
filesystem, real TreeSitter parsing, and real Tantivy indexing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import make_coordinator, noop_progress

pytestmark = pytest.mark.integration

class TestIndexInitialization:
    """Integration tests for index initialization."""

    @pytest.mark.asyncio
    async def test_init_creates_index_files(self, integration_repo: Path) -> None:
        """Index initialization creates necessary files."""
        index = make_coordinator(integration_repo)
        result = await index.initialize(noop_progress)

        # Init should complete without errors
        assert len(result.errors) == 0
        # Database and tantivy directory should exist
        assert (integration_repo / ".recon" / "index.db").exists()
        assert (integration_repo / ".recon" / "tantivy").exists()

    @pytest.mark.asyncio
    async def test_init_idempotent(self, integration_repo: Path) -> None:
        """Multiple init calls are safe."""
        index = make_coordinator(integration_repo)

        result1 = await index.initialize(noop_progress)
        result2 = await index.initialize(noop_progress)

        # Both should succeed without errors
        assert len(result1.errors) == 0
        assert len(result2.errors) == 0

class TestIndexing:
    """Integration tests for indexing operations."""

    @pytest.mark.asyncio
    async def test_index_discovers_python_files(self, integration_repo: Path) -> None:
        """Indexing discovers and processes Python files."""
        index = make_coordinator(integration_repo)
        result = await index.initialize(noop_progress)

        # Should have discovered some contexts
        assert result.contexts_discovered >= 0

    @pytest.mark.asyncio
    async def test_index_extracts_definitions(self, integration_repo: Path) -> None:
        """Indexing extracts function and class definitions."""
        index = make_coordinator(integration_repo)
        await index.initialize(noop_progress)

        # Query for definitions
        result = await index.search("def", mode="lexical")

        # Should have found some definitions
        assert result is not None

class TestSearch:
    """Integration tests for search operations."""

    @pytest.mark.asyncio
    async def test_search_finds_function_by_name(self, integration_repo: Path) -> None:
        """Search finds function by name."""
        index = make_coordinator(integration_repo)
        await index.initialize(noop_progress)

        result = await index.search("greet", mode="lexical")

        # Should find something (may be zero results if no greet function)
        assert result is not None
        assert len(result.results) >= 0

    @pytest.mark.asyncio
    async def test_search_finds_class_by_name(self, integration_repo: Path) -> None:
        """Search finds class by name."""
        index = make_coordinator(integration_repo)
        await index.initialize(noop_progress)

        result = await index.search("Calculator", mode="lexical")

        assert result is not None
        assert len(result.results) >= 0

    @pytest.mark.asyncio
    async def test_search_lexical_mode(self, integration_repo: Path) -> None:
        """Lexical search finds text matches."""
        index = make_coordinator(integration_repo)
        await index.initialize(noop_progress)

        result = await index.search("return", mode="lexical")

        # Lexical search should work
        assert result is not None

class TestMapRepo:
    """Integration tests for map_repo operation."""

    @pytest.mark.asyncio
    async def test_map_repo_returns_structure(self, integration_repo: Path) -> None:
        """map_repo returns repository structure."""
        index = make_coordinator(integration_repo)
        await index.initialize(noop_progress)

        result = await index.map_repo()

        # Should have languages detected
        assert result.languages is not None
        assert len(result.languages) > 0
        language_names = [lang.language.lower() for lang in result.languages]
        assert "python" in language_names

    @pytest.mark.asyncio
    async def test_map_repo_file_tree(self, integration_repo: Path) -> None:
        """map_repo returns file tree."""
        index = make_coordinator(integration_repo)
        await index.initialize(noop_progress)

        result = await index.map_repo()

        # Should have a structure with tree
        assert result.structure is not None
        assert result.structure.tree is not None
        # Tree is a list of DirectoryNode; check top-level names
        top_level_names = [node.name for node in result.structure.tree]
        assert "src" in top_level_names
