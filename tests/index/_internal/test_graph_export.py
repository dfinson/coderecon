"""Tests for graph export HTML generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index._internal.db import Database, create_additional_indexes


@pytest.fixture
def export_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)

    from coderecon.index.models import Worktree
    with db.session() as session:
        session.add(Worktree(name="main", root_path=str(tmp_path), is_main=True))
        session.commit()

    from sqlmodel import Session

    from coderecon.index.models import Context, File, ImportFact

    with Session(db.engine) as session:
        ctx = Context(id=1, language_family="python", root_path=".")
        session.add(ctx)
        session.flush()

        session.add_all([
            File(id=1, path="a.py", content_hash="h1", worktree_id=1),
            File(id=2, path="b.py", content_hash="h2", worktree_id=1),
        ])
        session.flush()

        session.add(
            ImportFact(import_uid="i1", file_id=1, unit_id=1, source_literal="import b", resolved_path="b.py", imported_name="b", import_kind="python_import"),
        )
        session.commit()

    return db


class TestGraphExport:
    def test_generates_html(self, export_db: Database, tmp_path: Path) -> None:
        from coderecon.index._internal.analysis.graph_export import export_graph_html

        output = tmp_path / "graph.html"
        result = export_graph_html(export_db.engine, output)
        assert result.exists()
        content = result.read_text()
        assert "vis-network" in content
        assert "a.py" in content

    def test_empty_graph(self, tmp_path: Path) -> None:
        from coderecon.index._internal.analysis.graph_export import export_graph_html

        db = Database(tmp_path / "empty.db")
        db.create_all()

        output = tmp_path / "graph.html"
        result = export_graph_html(db.engine, output)
        assert result.exists()
        assert "No graph data" in result.read_text()

    def test_creates_parent_dirs(self, export_db: Database, tmp_path: Path) -> None:
        from coderecon.index._internal.analysis.graph_export import export_graph_html

        output = tmp_path / "nested" / "dir" / "graph.html"
        result = export_graph_html(export_db.engine, output)
        assert result.exists()
