"""Tests for daemon dev_tools — index introspection helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from coderecon.daemon.dev_tools import (
    dev_index_facts,
    dev_index_status,
    dev_lookup_defs,
)
from coderecon.index.models import Context, DefFact, File, ImportFact, Worktree

pytestmark = pytest.mark.integration


# ── fixtures ──────────────────────────────────────────────


@pytest.fixture()
def db_session(tmp_path):
    """In-memory SQLite session with full schema."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)

    # Base worktree
    wt = Worktree(name="main", root_path=str(tmp_path), is_main=True)
    session.add(wt)
    session.commit()
    session.refresh(wt)

    # Context (needed for DefFact FK)
    ctx = Context(language_family="python", root_path=str(tmp_path))
    session.add(ctx)
    session.commit()
    session.refresh(ctx)

    # Files
    f1 = File(worktree_id=wt.id, path="src/app.py", language_family="python", line_count=100)
    f2 = File(worktree_id=wt.id, path="src/utils/helpers.py", language_family="python", line_count=50)
    f3 = File(worktree_id=wt.id, path="lib/index.ts", language_family="typescript", line_count=30)
    session.add_all([f1, f2, f3])
    session.commit()
    for f in [f1, f2, f3]:
        session.refresh(f)

    # Defs
    session.add_all([
        DefFact(def_uid="d1", file_id=f1.id, unit_id=ctx.id, kind="class", name="AppServer",
                lexical_path="AppServer", start_line=10, start_col=0, end_line=80, end_col=0),
        DefFact(def_uid="d2", file_id=f1.id, unit_id=ctx.id, kind="function", name="main",
                lexical_path="main", start_line=90, start_col=0, end_line=100, end_col=0),
        DefFact(def_uid="d3", file_id=f2.id, unit_id=ctx.id, kind="function", name="format_date",
                lexical_path="format_date", start_line=5, start_col=0, end_line=20, end_col=0),
        DefFact(def_uid="d4", file_id=f3.id, unit_id=ctx.id, kind="class", name="IndexBuilder",
                lexical_path="IndexBuilder", start_line=1, start_col=0, end_line=30, end_col=0),
    ])
    session.commit()

    # Imports (one resolved, one external)
    session.add_all([
        ImportFact(import_uid="i1", file_id=f1.id, unit_id=ctx.id,
                   imported_name="flask", source_literal="flask", import_kind="python_import",
                   resolved_path="", start_line=1),
        ImportFact(import_uid="i2", file_id=f1.id, unit_id=ctx.id,
                   imported_name="Flask", source_literal="flask", import_kind="python_from",
                   resolved_path="", start_line=2),
        ImportFact(import_uid="i3", file_id=f2.id, unit_id=ctx.id,
                   imported_name="datetime", source_literal="datetime", import_kind="python_import",
                   resolved_path="", start_line=1),
        ImportFact(import_uid="i4", file_id=f1.id, unit_id=ctx.id,
                   imported_name="helpers", source_literal="src.utils.helpers",
                   import_kind="python_from", resolved_path="src/utils/helpers.py", start_line=3),
    ])
    session.commit()

    yield session, wt, ctx
    session.close()


@pytest.fixture()
def app_ctx(db_session):
    """Mock AppContext backed by a real in-memory database."""
    session, wt, _ctx = db_session

    # Build a mock coordinator with a real db.session() that returns our session
    coordinator = MagicMock()
    coordinator._initialized = True
    coordinator._get_or_create_worktree_id = MagicMock(return_value=wt.id)

    # db.session() must be a context manager returning our real Session
    db = MagicMock()
    db.session.return_value.__enter__ = MagicMock(return_value=session)
    db.session.return_value.__exit__ = MagicMock(return_value=False)
    coordinator.db = db

    ctx = SimpleNamespace(coordinator=coordinator)
    return ctx


# ── dev_index_facts ───────────────────────────────────────


class TestDevIndexFacts:
    @pytest.mark.asyncio
    async def test_returns_top_dirs(self, app_ctx) -> None:
        result = await dev_index_facts(app_ctx)
        assert "src" in result["top_dirs"]
        assert "lib" in result["top_dirs"]

    @pytest.mark.asyncio
    async def test_returns_languages(self, app_ctx) -> None:
        result = await dev_index_facts(app_ctx)
        langs = {l["language"] for l in result["languages"]}
        assert "python" in langs
        assert "typescript" in langs

    @pytest.mark.asyncio
    async def test_returns_classes(self, app_ctx) -> None:
        result = await dev_index_facts(app_ctx)
        assert "AppServer" in result["classes"]
        assert "IndexBuilder" in result["classes"]

    @pytest.mark.asyncio
    async def test_returns_functions(self, app_ctx) -> None:
        result = await dev_index_facts(app_ctx)
        assert "main" in result["functions"]
        assert "format_date" in result["functions"]

    @pytest.mark.asyncio
    async def test_returns_external_deps(self, app_ctx) -> None:
        result = await dev_index_facts(app_ctx)
        assert "flask" in result["external_deps"]
        assert "datetime" in result["external_deps"]
        # resolved imports should not appear
        assert "src.utils.helpers" not in result["external_deps"]

    @pytest.mark.asyncio
    async def test_returns_counts(self, app_ctx) -> None:
        result = await dev_index_facts(app_ctx)
        assert result["file_count"] == 3
        assert result["def_count"] == 4

    @pytest.mark.asyncio
    async def test_with_worktree_filter(self, app_ctx) -> None:
        result = await dev_index_facts(app_ctx, worktree="main")
        assert result["file_count"] == 3


# ── dev_lookup_defs ───────────────────────────────────────


class TestDevLookupDefs:
    @pytest.mark.asyncio
    async def test_all_defs(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx)
        assert len(result["defs"]) == 4

    @pytest.mark.asyncio
    async def test_filter_by_kind(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx, kind="class")
        names = [d["name"] for d in result["defs"]]
        assert "AppServer" in names
        assert "main" not in names

    @pytest.mark.asyncio
    async def test_filter_by_name(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx, name="main")
        assert len(result["defs"]) == 1
        assert result["defs"][0]["name"] == "main"

    @pytest.mark.asyncio
    async def test_filter_by_path(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx, path="src/app.py")
        names = {d["name"] for d in result["defs"]}
        assert "AppServer" in names
        assert "format_date" not in names

    @pytest.mark.asyncio
    async def test_filter_by_start_line(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx, start_line=10)
        # Should match AppServer (start_line=10) within ±5
        names = {d["name"] for d in result["defs"]}
        assert "AppServer" in names

    @pytest.mark.asyncio
    async def test_filter_by_end_line(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx, start_line=10, end_line=80)
        names = {d["name"] for d in result["defs"]}
        assert "AppServer" in names

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx, path="nonexistent.py")
        assert result["defs"] == []

    @pytest.mark.asyncio
    async def test_def_entry_fields(self, app_ctx) -> None:
        result = await dev_lookup_defs(app_ctx, name="AppServer")
        entry = result["defs"][0]
        assert entry["path"] == "src/app.py"
        assert entry["kind"] == "class"
        assert entry["start_line"] == 10
        assert entry["end_line"] == 80
        assert "has_docstring" in entry
        assert "object_size_lines" in entry


# ── dev_index_status ──────────────────────────────────────


class TestDevIndexStatus:
    @pytest.mark.asyncio
    async def test_returns_counts(self, app_ctx) -> None:
        result = await dev_index_status(app_ctx)
        assert result["file_count"] == 3
        assert result["def_count"] == 4
        assert result["initialized"] is True
        assert result["worktree"] == "main"

    @pytest.mark.asyncio
    async def test_with_worktree(self, app_ctx) -> None:
        result = await dev_index_status(app_ctx, worktree="main")
        assert result["worktree"] == "main"
        assert result["file_count"] == 3
