"""Integration tests for index robustness — error recovery, concurrent ops, edge cases."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from coderecon.index.ops import IndexCoordinatorEngine

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = pytest.mark.integration


def _noop(indexed: int, total: int, by_ext: dict[str, int], phase: str = "") -> None:
    pass


def _engine(repo: Path) -> IndexCoordinatorEngine:
    recon = repo / ".recon"
    return IndexCoordinatorEngine(repo, recon / "index.db", recon / "tantivy")


class TestIndexInitialization:
    def test_init_on_empty_repo(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        assert engine is not None

    @pytest.mark.asyncio
    async def test_full_init_and_index(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        result = await engine.initialize(_noop)
        assert result is not None

    @pytest.mark.asyncio
    async def test_init_idempotent(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)
        await engine.initialize(_noop)

    @pytest.mark.asyncio
    async def test_init_creates_db(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)
        assert (integration_repo / ".recon" / "index.db").exists()


class TestSearchAfterIndex:
    @pytest.mark.asyncio
    async def test_search_finds_function(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)
        result = await engine.search("greet", mode="lexical")
        assert result is not None
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_search_finds_class(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)
        result = await engine.search("Calculator", mode="lexical")
        assert result is not None
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_search_no_results(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)
        result = await engine.search("zzz_no_match_zzz", mode="lexical")
        assert result is not None
        assert len(result.results) == 0


class TestIncrementalReindex:
    @pytest.mark.asyncio
    async def test_reindex_after_file_change(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)

        main_py = integration_repo / "src" / "main.py"
        content = main_py.read_text()
        content += '\ndef new_function():\n    """A brand new function."""\n    return 42\n'
        main_py.write_text(content)

        await engine.reindex_incremental([main_py])

        result = await engine.search("new_function", mode="lexical")
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_reindex_after_file_deletion(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)

        deleted = integration_repo / "src" / "utils.py"
        deleted.unlink()

        await engine.reindex_incremental([deleted])


class TestFullReindex:
    @pytest.mark.asyncio
    async def test_full_reindex(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        await engine.initialize(_noop)
        await engine.reindex_full()

        result = await engine.search("greet", mode="lexical")
        assert len(result.results) > 0


class TestMapRepo:
    def test_repo_mapper_instantiation(self, integration_repo: Path) -> None:
        from unittest.mock import MagicMock

        from coderecon.tools.map_repo import RepoMapper

        session = MagicMock()
        mapper = RepoMapper(session, integration_repo)
        assert mapper is not None


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_search_before_init(self, integration_repo: Path) -> None:
        engine = _engine(integration_repo)
        with pytest.raises(Exception):
            await engine.search("greet", mode="lexical")
            await engine.search("greet", mode="lexical")

    def test_nonexistent_repo_path(self, tmp_path: Path) -> None:
        """Engine with nonexistent repo root can be constructed."""
        fake_root = tmp_path / "no_such_repo"
        db_path = tmp_path / "db.sqlite"
        tantivy_path = tmp_path / "tantivy"
        # Constructor shouldn't crash
        engine = IndexCoordinatorEngine(fake_root, db_path, tantivy_path)
        assert engine is not None
