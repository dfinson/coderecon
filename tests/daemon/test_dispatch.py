"""Tests for coderecon.daemon.dispatch — stdio method routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.daemon.dispatch import (
    MANAGEMENT_METHODS,
    _error_response,
    _success_response,
    dispatch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_mock() -> MagicMock:
    registry = MagicMock()
    registry.list_repos.return_value = []
    return registry


def _make_daemon_mock(*, slot_names: list[str] | None = None) -> MagicMock:
    daemon = MagicMock()
    daemon.slot_names = slot_names or []
    daemon.get_slot.return_value = None
    return daemon


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

class TestResponseHelpers:
    def test_success(self) -> None:
        resp = _success_response("r1", {"ok": True})
        assert resp == {"id": "r1", "result": {"ok": True}}

    def test_success_no_id(self) -> None:
        resp = _success_response(None, {"ok": True})
        assert "id" not in resp
        assert resp["result"] == {"ok": True}

    def test_error(self) -> None:
        resp = _error_response("r2", "FAIL", "broken")
        assert resp["id"] == "r2"
        assert resp["error"]["code"] == "FAIL"
        assert resp["error"]["message"] == "broken"


# ---------------------------------------------------------------------------
# Management dispatch
# ---------------------------------------------------------------------------

class TestManagementMethods:
    def test_management_methods_frozen(self) -> None:
        assert "register" in MANAGEMENT_METHODS
        assert "catalog" in MANAGEMENT_METHODS
        assert "status" in MANAGEMENT_METHODS
        assert "session_close" in MANAGEMENT_METHODS

    @pytest.mark.asyncio
    async def test_missing_method(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {"id": "r1"})
        assert resp["error"]["code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_catalog(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {"id": "r1", "method": "catalog", "params": {}})
        assert "result" in resp
        assert "repos" in resp["result"]

    @pytest.mark.asyncio
    async def test_status(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {"id": "r1", "method": "status", "params": {}})
        assert resp["result"]["daemon_healthy"] is True

    @pytest.mark.asyncio
    async def test_register_no_path(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {"id": "r1", "method": "register", "params": {}})
        assert resp["error"]["code"] == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_register_success(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        repo_mock = SimpleNamespace(name="myrepo")
        wt_mock = SimpleNamespace(name="main")
        registry.register.return_value = (repo_mock, wt_mock)
        resp = await dispatch(daemon, registry, {
            "id": "r1", "method": "register", "params": {"path": "/tmp/repo"},
        })
        assert resp["result"]["repo"] == "myrepo"
        assert resp["result"]["worktree"] == "main"


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_tool_missing_repo(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {
            "id": "r1", "method": "recon_map", "params": {},
        })
        assert resp["error"]["code"] == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_tool_repo_not_found(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        with patch("coderecon.daemon.dispatch.resolve_worktree", new_callable=AsyncMock, return_value=None):
            resp = await dispatch(daemon, registry, {
                "id": "r1", "method": "recon_map", "params": {"repo": "unknown"},
            })
        assert resp["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_tool_unknown_method(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        fake_slot = MagicMock()
        fake_slot.app_ctx = MagicMock()
        with patch("coderecon.daemon.dispatch.resolve_worktree", new_callable=AsyncMock, return_value=fake_slot):
            resp = await dispatch(daemon, registry, {
                "id": "r1", "method": "nonexistent", "params": {"repo": "r"},
            })
        assert resp["error"]["code"] == "METHOD_NOT_FOUND"


class TestDescribeDispatch:
    @pytest.mark.asyncio
    async def test_describe_no_repo_required(self) -> None:
        """describe is a management method — must not require repo."""
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {
            "id": "r1", "method": "describe",
            "params": {"action": "tool", "name": "recon"},
        })
        # Should get a result, not an INVALID_PARAMS error
        assert "error" not in resp or resp.get("error", {}).get("code") != "INVALID_PARAMS"


class TestUnregisterDispatch:
    @pytest.mark.asyncio
    async def test_unregister_no_path(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {
            "id": "r1", "method": "unregister", "params": {},
        })
        assert resp["error"]["code"] == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_unregister_success(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        registry.unregister.return_value = True
        resp = await dispatch(daemon, registry, {
            "id": "r1", "method": "unregister", "params": {"path": "/tmp/repo"},
        })
        assert resp["result"]["removed"] is True


class TestReindexDispatch:
    @pytest.mark.asyncio
    async def test_reindex_no_repo(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        resp = await dispatch(daemon, registry, {
            "id": "r1", "method": "reindex", "params": {},
        })
        assert resp["error"]["code"] == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_reindex_repo_not_found(self) -> None:
        daemon = _make_daemon_mock()
        registry = _make_registry_mock()
        with patch("coderecon.daemon.dispatch.resolve_worktree", new_callable=AsyncMock, return_value=None):
            resp = await dispatch(daemon, registry, {
                "id": "r1", "method": "reindex", "params": {"repo": "unknown"},
            })
        assert resp["error"]["code"] == "NOT_FOUND"
