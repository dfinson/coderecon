"""Integration tests for SDK handle — SessionHandle, RepoHandle delegation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.sdk.handle import RepoHandle, SessionHandle, _override_session

pytestmark = pytest.mark.integration


def _mock_sdk() -> MagicMock:
    """Create a mock CodeRecon SDK object."""
    sdk = MagicMock()
    sdk._explicit_session = None
    sdk.recon = AsyncMock(return_value=MagicMock())
    sdk.recon_map = AsyncMock(return_value=MagicMock())
    sdk.recon_impact = AsyncMock(return_value=MagicMock())
    sdk.recon_understand = AsyncMock(return_value=MagicMock())
    sdk.semantic_diff = AsyncMock(return_value=MagicMock())
    sdk.graph_cycles = AsyncMock(return_value=MagicMock())
    sdk.graph_communities = AsyncMock(return_value=MagicMock())
    sdk.graph_export = AsyncMock(return_value=MagicMock())
    sdk.refactor_rename = AsyncMock(return_value=MagicMock())
    sdk.refactor_move = AsyncMock(return_value=MagicMock())
    sdk.refactor_commit = AsyncMock(return_value=MagicMock())
    sdk.refactor_cancel = AsyncMock(return_value=MagicMock())
    sdk.checkpoint = AsyncMock(return_value=MagicMock())
    sdk.close_session = AsyncMock()
    sdk._call = AsyncMock(return_value={})
    sdk.as_openai_tools = MagicMock(return_value=[])
    return sdk


class TestOverrideSession:
    def test_sets_and_restores(self) -> None:
        sdk = MagicMock()
        sdk._explicit_session = None

        with _override_session(sdk, "sess_123"):
            assert sdk._explicit_session == "sess_123"

        assert sdk._explicit_session is None

    def test_none_session_noop(self) -> None:
        sdk = MagicMock()
        sdk._explicit_session = "original"

        with _override_session(sdk, None):
            assert sdk._explicit_session == "original"

        assert sdk._explicit_session == "original"

    def test_restores_on_exception(self) -> None:
        sdk = MagicMock()
        sdk._explicit_session = "original"

        with pytest.raises(ValueError):  # noqa: PT011
            with _override_session(sdk, "temp"):
                assert sdk._explicit_session == "temp"
                raise ValueError("boom")

        assert sdk._explicit_session == "original"


class TestSessionHandle:
    def test_repo_returns_repo_handle(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        repo = session.repo("myrepo")
        assert isinstance(repo, RepoHandle)

    def test_repo_with_worktree(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        repo = session.repo("myrepo", worktree="feature-x")
        assert isinstance(repo, RepoHandle)

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.close()
        # close_session should be called with the session's repo context
        # (close is a no-op on SessionHandle per current implementation)

    @pytest.mark.asyncio
    async def test_recon_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.recon("myrepo", "find bugs")
        sdk.recon.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_checkpoint_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.checkpoint("myrepo", ["a.py"])
        sdk.checkpoint.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_rename_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.refactor_rename("myrepo", "Foo", "Bar", "rename class")
        sdk.refactor_rename.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_move_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.refactor_move("myrepo", "a.py", "b.py", "move file")
        sdk.refactor_move.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_commit_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.refactor_commit("myrepo", "ref123")
        sdk.refactor_commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_cancel_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.refactor_cancel("myrepo", "ref123")
        sdk.refactor_cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recon_map_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.recon_map("myrepo")
        sdk.recon_map.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recon_impact_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.recon_impact("myrepo", "Calculator", "check impact")
        sdk.recon_impact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recon_understand_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.recon_understand("myrepo")
        sdk.recon_understand.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_semantic_diff_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.semantic_diff("myrepo")
        sdk.semantic_diff.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_cycles_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.graph_cycles("myrepo")
        sdk.graph_cycles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_communities_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.graph_communities("myrepo")
        sdk.graph_communities.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_export_delegates(self) -> None:
        sdk = _mock_sdk()
        session = SessionHandle(sdk, "sess_abc")
        await session.graph_export("myrepo")
        sdk.graph_export.assert_awaited_once()


class TestRepoHandle:
    def test_call_kwargs_includes_repo(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        kwargs = handle._call_kwargs()
        assert kwargs["repo"] == "myrepo"

    def test_call_kwargs_with_worktree(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo", worktree="feat")
        kwargs = handle._call_kwargs()
        assert kwargs["worktree"] == "feat"

    @pytest.mark.asyncio
    async def test_recon(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.recon("find bugs")
        sdk.recon.assert_awaited_once()
        call_args = sdk.recon.call_args
        assert call_args.args[0] == "myrepo"
        assert call_args.args[1] == "find bugs"

    @pytest.mark.asyncio
    async def test_recon_map(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.recon_map()
        sdk.recon_map.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_rename(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.refactor_rename("Foo", "Bar", "rename class")
        sdk.refactor_rename.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_move(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.refactor_move("a.py", "b.py", "move file")
        sdk.refactor_move.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_commit(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.refactor_commit("ref123")
        sdk.refactor_commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refactor_cancel(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.refactor_cancel("ref123")
        sdk.refactor_cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_checkpoint(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.checkpoint(["a.py"])
        sdk.checkpoint.assert_awaited_once()

    def test_as_openai_tools(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        with patch("coderecon.sdk.frameworks.as_openai_tools", return_value=[]) as mock_fn:
            result = handle.as_openai_tools()
            mock_fn.assert_called_once()
            assert result == []

    @pytest.mark.asyncio
    async def test_recon_impact(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.recon_impact("Calculator", "check impact")
        sdk.recon_impact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recon_understand(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.recon_understand()
        sdk.recon_understand.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_semantic_diff(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.semantic_diff()
        sdk.semantic_diff.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_cycles(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.graph_cycles()
        sdk.graph_cycles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_communities(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.graph_communities()
        sdk.graph_communities.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_export(self) -> None:
        sdk = _mock_sdk()
        handle = RepoHandle(sdk, "myrepo")
        await handle.graph_export()
        sdk.graph_export.assert_awaited_once()
