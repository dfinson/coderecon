"""Tests for coderecon.daemon.resolve."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.daemon.resolve import resolve_worktree


class TestResolveWorktree:
    """Tests for the resolve_worktree function."""

    @pytest.fixture()
    def daemon(self) -> MagicMock:
        d = MagicMock()
        d.get_slot = MagicMock(return_value=None)
        d.lazy_activate_repo = AsyncMock(return_value=None)
        d.lazy_activate_worktree = AsyncMock(return_value=None)
        return d

    @pytest.mark.asyncio()
    async def test_returns_none_when_repo_not_found(self, daemon: MagicMock) -> None:
        daemon.get_slot.return_value = None
        daemon.lazy_activate_repo.return_value = None
        result = await resolve_worktree(daemon, "nonexistent")
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_when_worktree_not_found(self, daemon: MagicMock) -> None:
        wt_slot = MagicMock()
        slot = MagicMock()
        slot.worktrees = {}  # No worktrees
        daemon.get_slot.return_value = slot
        daemon.lazy_activate_worktree.return_value = None
        result = await resolve_worktree(daemon, "myrepo", "nonexistent-wt")
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_existing_slot(self, daemon: MagicMock) -> None:
        wt_slot = MagicMock()
        wt_slot.last_request_at = 0.0
        slot = MagicMock()
        slot.worktrees = {"main": wt_slot}
        daemon.get_slot.return_value = slot
        result = await resolve_worktree(daemon, "myrepo")
        assert result is wt_slot

    @pytest.mark.asyncio()
    async def test_default_worktree_is_main(self, daemon: MagicMock) -> None:
        wt_slot = MagicMock()
        wt_slot.last_request_at = 0.0
        slot = MagicMock()
        slot.worktrees = {"main": wt_slot}
        daemon.get_slot.return_value = slot
        result = await resolve_worktree(daemon, "myrepo", None)
        assert result is wt_slot

    @pytest.mark.asyncio()
    async def test_lazy_activates_repo(self, daemon: MagicMock) -> None:
        wt_slot = MagicMock()
        wt_slot.last_request_at = 0.0
        slot = MagicMock()
        slot.worktrees = {"main": wt_slot}
        daemon.get_slot.return_value = None
        daemon.lazy_activate_repo.return_value = slot
        result = await resolve_worktree(daemon, "myrepo")
        daemon.lazy_activate_repo.assert_awaited_once_with("myrepo")
        assert result is wt_slot

    @pytest.mark.asyncio()
    async def test_lazy_activates_worktree(self, daemon: MagicMock) -> None:
        wt_slot = MagicMock()
        wt_slot.last_request_at = 0.0
        slot = MagicMock()
        slot.worktrees = {}
        daemon.get_slot.return_value = slot
        daemon.lazy_activate_worktree.return_value = wt_slot
        result = await resolve_worktree(daemon, "myrepo", "feature-branch")
        daemon.lazy_activate_worktree.assert_awaited_once_with("myrepo", "feature-branch")
        assert result is wt_slot

    @pytest.mark.asyncio()
    async def test_updates_last_request_at(self, daemon: MagicMock) -> None:
        wt_slot = MagicMock()
        wt_slot.last_request_at = 0.0
        slot = MagicMock()
        slot.worktrees = {"main": wt_slot}
        daemon.get_slot.return_value = slot
        before = time.time()
        result = await resolve_worktree(daemon, "myrepo")
        after = time.time()
        assert before <= wt_slot.last_request_at <= after

    @pytest.mark.asyncio()
    async def test_explicit_worktree_name(self, daemon: MagicMock) -> None:
        wt_slot = MagicMock()
        wt_slot.last_request_at = 0.0
        slot = MagicMock()
        slot.worktrees = {"dev": wt_slot}
        daemon.get_slot.return_value = slot
        result = await resolve_worktree(daemon, "myrepo", "dev")
        assert result is wt_slot
