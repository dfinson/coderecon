"""Tests for global daemon app and routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.catalog.db import CatalogDB
from coderecon.catalog.registry import CatalogRegistry
from coderecon.daemon.global_app import GlobalDaemon, _DynamicMcpRouter


@pytest.fixture
def daemon(tmp_path: Path) -> GlobalDaemon:
    catalog = CatalogDB(home=tmp_path / ".coderecon")
    registry = CatalogRegistry(catalog)
    return GlobalDaemon(registry)


class TestGlobalDaemon:
    def test_initial_state(self, daemon: GlobalDaemon) -> None:
        assert daemon.slot_names == []
        assert daemon.get_slot("nope") is None

    def test_build_app_empty(self, daemon: GlobalDaemon) -> None:
        app = daemon.build_app()
        assert app is not None
        # Static routes for health + catalog must exist
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/health" in route_paths
        assert "/catalog" in route_paths

    def test_build_app_includes_repos_path_param_routes(self, daemon: GlobalDaemon) -> None:
        app = daemon.build_app()
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/repos/{name}/health" in route_paths
        assert "/repos/{name}/status" in route_paths
        assert "/repos/{name}/reindex" in route_paths
        assert "/repos/{name}/refresh-worktrees" in route_paths

    def test_build_app_includes_dynamic_mcp_router(self, daemon: GlobalDaemon) -> None:
        app = daemon.build_app()
        assert any(isinstance(r, _DynamicMcpRouter) for r in app.routes)


class TestGlobalAppRoutes:
    """Test the Starlette routes without actually starting a server."""

    @pytest.fixture
    def app(self, daemon: GlobalDaemon):
        return daemon.build_app()

    @pytest.mark.anyio
    async def test_health_endpoint(self, app) -> None:
        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "active_repos" in data
        assert isinstance(data["active_repos"], list)

    @pytest.mark.anyio
    async def test_catalog_endpoint_empty(self, app) -> None:
        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["repositories"] == []

    @pytest.mark.anyio
    async def test_repo_health_404_for_unknown_repo(self, app) -> None:
        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/repos/nonexistent/health")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_mcp_route_404_for_unknown_repo(self, app) -> None:
        from starlette.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/repos/nonexistent/worktrees/main/mcp")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_catalog_register_rejects_invalid_path(self, app) -> None:
        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/catalog/register", json={"path": "/nonexistent/path"})
        assert resp.status_code == 400
