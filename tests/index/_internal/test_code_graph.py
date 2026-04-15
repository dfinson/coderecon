"""Tests for code_graph module — graph construction and algorithms."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index._internal.db import Database, create_additional_indexes


@pytest.fixture
def graph_db(tmp_path: Path) -> Database:
    """Create a test DB with files, imports, defs, and refs for graph testing."""
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)

    from sqlmodel import Session

    from coderecon.index.models import Context, DefFact, File, ImportFact, RefFact

    with Session(db.engine) as session:
        ctx = Context(id=1, language_family="python", root_path=".")
        session.add(ctx)
        session.flush()

        fa = File(id=1, path="a.py", content_hash="h1")
        fb = File(id=2, path="b.py", content_hash="h2")
        fc = File(id=3, path="c.py", content_hash="h3")
        fd = File(id=4, path="d.py", content_hash="h4")
        session.add_all([fa, fb, fc, fd])
        session.flush()

        # Import edges: a→b, b→c, c→a (cycle), d→b
        session.add_all([
            ImportFact(import_uid="i1", file_id=1, unit_id=1, imported_name="b", import_kind="python_import", source_literal="import b", resolved_path="b.py"),
            ImportFact(import_uid="i2", file_id=2, unit_id=1, imported_name="c", import_kind="python_import", source_literal="import c", resolved_path="c.py"),
            ImportFact(import_uid="i3", file_id=3, unit_id=1, imported_name="a", import_kind="python_import", source_literal="import a", resolved_path="a.py"),
            ImportFact(import_uid="i4", file_id=4, unit_id=1, imported_name="b", import_kind="python_import", source_literal="import b", resolved_path="b.py"),
        ])

        # Defs
        session.add_all([
            DefFact(def_uid="a.foo", file_id=1, unit_id=1, kind="function", name="foo", qualified_name="a.foo", lexical_path="foo", start_line=1, start_col=0, end_line=10, end_col=0),
            DefFact(def_uid="b.bar", file_id=2, unit_id=1, kind="function", name="bar", qualified_name="b.bar", lexical_path="bar", start_line=1, start_col=0, end_line=10, end_col=0),
            DefFact(def_uid="c.baz", file_id=3, unit_id=1, kind="function", name="baz", qualified_name="c.baz", lexical_path="baz", start_line=1, start_col=0, end_line=10, end_col=0),
        ])

        # Refs: a.foo → b.bar, b.bar → c.baz
        session.add_all([
            RefFact(file_id=1, unit_id=1, target_def_uid="b.bar", ref_tier="proven", token_text="bar", role="REFERENCE", start_line=5, start_col=0, end_line=5, end_col=3),
            RefFact(file_id=2, unit_id=1, target_def_uid="c.baz", ref_tier="proven", token_text="baz", role="REFERENCE", start_line=5, start_col=0, end_line=5, end_col=3),
        ])

        session.commit()

    return db


class TestBuildFileGraph:
    def test_builds_graph_from_imports(self, graph_db: Database) -> None:
        from coderecon.index._internal.analysis.code_graph import build_file_graph

        g = build_file_graph(graph_db.engine)
        assert g.number_of_nodes() == 4
        assert g.number_of_edges() == 4
        assert g.has_edge("a.py", "b.py")
        assert g.has_edge("b.py", "c.py")
        assert g.has_edge("c.py", "a.py")
        assert g.has_edge("d.py", "b.py")

    def test_empty_db(self, tmp_path: Path) -> None:
        from coderecon.index._internal.analysis.code_graph import build_file_graph

        db = Database(tmp_path / "empty.db")
        db.create_all()
        g = build_file_graph(db.engine)
        assert g.number_of_nodes() == 0


class TestBuildDefGraph:
    def test_builds_def_edges(self, graph_db: Database) -> None:
        from coderecon.index._internal.analysis.code_graph import build_def_graph

        g = build_def_graph(graph_db.engine)
        assert g.number_of_nodes() >= 2


class TestPageRank:
    def test_compute_file_pagerank(self, graph_db: Database) -> None:
        from coderecon.index._internal.analysis.code_graph import (
            build_file_graph,
            compute_file_pagerank,
        )

        g = build_file_graph(graph_db.engine)
        ranked = compute_file_pagerank(g, top_k=4)
        assert len(ranked) == 4
        # All scores should be positive
        assert all(score > 0 for _, score in ranked)
        # Sorted descending
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_compute_def_pagerank(self, graph_db: Database) -> None:
        from coderecon.index._internal.analysis.code_graph import (
            build_def_graph,
            compute_pagerank,
        )

        g = build_def_graph(graph_db.engine)
        if g.number_of_nodes() > 0:
            ranked = compute_pagerank(g, top_k=3)
            assert all(s.pagerank > 0 for s in ranked)


class TestCycleDetection:
    def test_detects_cycle(self, graph_db: Database) -> None:
        from coderecon.index._internal.analysis.code_graph import (
            build_file_graph,
            detect_cycles,
        )

        g = build_file_graph(graph_db.engine)
        cycles = detect_cycles(g)
        # a→b→c→a forms a cycle
        assert len(cycles) >= 1
        cycle_nodes = cycles[0].nodes
        assert "a.py" in cycle_nodes
        assert "b.py" in cycle_nodes
        assert "c.py" in cycle_nodes

    def test_no_cycle_in_dag(self, tmp_path: Path) -> None:
        from coderecon.index._internal.analysis.code_graph import detect_cycles

        import networkx as nx

        g = nx.DiGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        cycles = detect_cycles(g)
        assert len(cycles) == 0


class TestCommunityDetection:
    def test_detects_communities(self, graph_db: Database) -> None:
        from coderecon.index._internal.analysis.code_graph import (
            build_file_graph,
            detect_communities,
        )

        g = build_file_graph(graph_db.engine)
        communities = detect_communities(g)
        assert len(communities) >= 1
        # All nodes should be covered
        all_members = {m for c in communities for m in c.members}
        assert all_members == set(g.nodes())


class TestAnalyzeConvenience:
    def test_analyze_file_graph(self, graph_db: Database) -> None:
        from coderecon.index._internal.analysis.code_graph import analyze_file_graph

        result = analyze_file_graph(graph_db.engine)
        assert result.node_count == 4
        assert result.edge_count == 4
        assert len(result.cycles) >= 1
        assert len(result.communities) >= 1
