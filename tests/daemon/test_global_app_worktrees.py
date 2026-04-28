"""Tests for daemon/global_app_worktrees.py — worktree management helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from coderecon.daemon.global_app_worktrees import (
    build_app,
    queue_startup_scans,
    refresh_worktrees,
)
from coderecon.git.errors import GitError

_GITOPS_PATH = "coderecon.git.ops.GitOps"
_BUILD_GLOBAL_APP_PATH = "coderecon.daemon.global_routes.build_global_app"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_wt_slot(repo_root: str = "/repo") -> MagicMock:
    slot = MagicMock()
    slot.repo_root = Path(repo_root)
    return slot


def _make_daemon(
    *,
    slots: dict | None = None,
    main_root: str = "/repo",
) -> MagicMock:
    daemon = MagicMock()
    if slots is None:
        main_wt = _make_wt_slot(main_root)
        repo_slot = MagicMock()
        repo_slot.worktrees = {"main": main_wt}
        slots = {"myrepo": repo_slot}
    daemon._slots = slots
    return daemon


# ===========================================================================
# queue_startup_scans tests
# ===========================================================================

class TestQueueStartupScans:
    """Tests for queue_startup_scans async function."""

    @pytest.mark.asyncio
    async def test_main_worktree_queues_changed_files(self) -> None:
        daemon = _make_daemon()
        slot = daemon._slots["myrepo"]
        slot.coordinator.changed_since_last_index = MagicMock(
            return_value=[Path("/repo/a.py")]
        )

        with patch(_GITOPS_PATH) as MockGitOps:
            MockGitOps.return_value.default_branch.return_value = "main"
            await queue_startup_scans(daemon)

        slot.indexer.queue_paths.assert_called_once_with(
            "main", [Path("/repo/a.py")]
        )

    @pytest.mark.asyncio
    async def test_main_worktree_no_changes_skips_queue(self) -> None:
        daemon = _make_daemon()
        slot = daemon._slots["myrepo"]
        slot.coordinator.changed_since_last_index = MagicMock(return_value=[])

        with patch(_GITOPS_PATH) as MockGitOps:
            MockGitOps.return_value.default_branch.return_value = "main"
            await queue_startup_scans(daemon)

        slot.indexer.queue_paths.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_main_worktree_queues_diff(self) -> None:
        main_wt = _make_wt_slot("/repo")
        feature_wt = _make_wt_slot("/repo-wt/feature")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt, "feature": feature_wt}
        slot.coordinator.changed_since_last_index = MagicMock(return_value=[])
        daemon = _make_daemon(slots={"myrepo": slot})

        with patch(_GITOPS_PATH) as MockGitOps:
            mock_main_git = MagicMock()
            mock_main_git.default_branch.return_value = "main"
            mock_feature_git = MagicMock()
            mock_feature_git.files_changed_vs.return_value = [
                Path("src/foo.py")
            ]
            MockGitOps.side_effect = [mock_main_git, mock_feature_git]
            await queue_startup_scans(daemon)

        slot.indexer.queue_paths.assert_called_once_with(
            "feature",
            [Path("/repo-wt/feature") / "src/foo.py"],
        )

    @pytest.mark.asyncio
    async def test_non_main_git_error_is_caught(self) -> None:
        main_wt = _make_wt_slot("/repo")
        feature_wt = _make_wt_slot("/repo-wt/feature")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt, "feature": feature_wt}
        slot.coordinator.changed_since_last_index = MagicMock(return_value=[])
        daemon = _make_daemon(slots={"myrepo": slot})

        with patch(_GITOPS_PATH) as MockGitOps:
            mock_main_git = MagicMock()
            mock_main_git.default_branch.return_value = "main"
            mock_feature_git = MagicMock()
            mock_feature_git.files_changed_vs.side_effect = GitError("boom")
            MockGitOps.side_effect = [mock_main_git, mock_feature_git]
            # Should not raise
            await queue_startup_scans(daemon)

        slot.indexer.queue_paths.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_branch_fallback_on_error(self) -> None:
        """When default_branch() raises, falls back to 'main'."""
        main_wt = _make_wt_slot("/repo")
        feature_wt = _make_wt_slot("/repo-wt/feat")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt, "feature": feature_wt}
        slot.coordinator.changed_since_last_index = MagicMock(return_value=[])
        daemon = _make_daemon(slots={"myrepo": slot})

        with patch(_GITOPS_PATH) as MockGitOps:
            mock_main_git = MagicMock()
            mock_main_git.default_branch.side_effect = GitError("no remote")
            mock_feature_git = MagicMock()
            mock_feature_git.files_changed_vs.return_value = []
            MockGitOps.side_effect = [mock_main_git, mock_feature_git]
            await queue_startup_scans(daemon)

        # Should have called files_changed_vs with fallback "main"
        mock_feature_git.files_changed_vs.assert_called_once_with("main")


# ===========================================================================
# refresh_worktrees tests
# ===========================================================================

class TestRefreshWorktrees:
    """Tests for refresh_worktrees async function."""

    @pytest.mark.asyncio
    async def test_unknown_slot_returns_empty(self) -> None:
        daemon = _make_daemon()
        result = await refresh_worktrees(daemon, "nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_worktrees_returns_empty(self) -> None:
        slot = MagicMock()
        slot.worktrees = {}
        daemon = _make_daemon(slots={"myrepo": slot})
        result = await refresh_worktrees(daemon, "myrepo")
        assert result == []

    @pytest.mark.asyncio
    async def test_already_known_worktree_skipped(self) -> None:
        main_wt = _make_wt_slot("/repo")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt}
        daemon = _make_daemon(slots={"myrepo": slot})

        wt_info = SimpleNamespace(name="main", path="/repo")
        with patch(_GITOPS_PATH) as MockGitOps:
            MockGitOps.return_value.worktrees.return_value = [wt_info]
            MockGitOps.return_value.default_branch.return_value = "main"
            result = await refresh_worktrees(daemon, "myrepo")

        assert result == []

    @pytest.mark.asyncio
    async def test_new_worktree_registered(self) -> None:
        main_wt = _make_wt_slot("/repo")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt}
        daemon = _make_daemon(slots={"myrepo": slot})
        daemon.registry.lookup_by_path.return_value = MagicMock()  # catalogued

        new_wt = SimpleNamespace(name="feature", path="/repo-wt/feature")
        with patch(_GITOPS_PATH) as MockGitOps:
            mock_git = MockGitOps.return_value
            mock_git.worktrees.return_value = [new_wt]
            mock_git.default_branch.return_value = "main"
            mock_git.files_changed_vs.return_value = [Path("x.py")]
            result = await refresh_worktrees(daemon, "myrepo")

        assert result == ["feature"]
        slot.coordinator.set_freshness_gate.assert_called_once()

    @pytest.mark.asyncio
    async def test_uncatalogued_worktree_skipped(self) -> None:
        main_wt = _make_wt_slot("/repo")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt}
        daemon = _make_daemon(slots={"myrepo": slot})
        daemon.registry.lookup_by_path.return_value = None  # not catalogued

        new_wt = SimpleNamespace(name="orphan", path="/repo-wt/orphan")
        with patch(_GITOPS_PATH) as MockGitOps:
            mock_git = MockGitOps.return_value
            mock_git.worktrees.return_value = [new_wt]
            mock_git.default_branch.return_value = "main"
            result = await refresh_worktrees(daemon, "myrepo")

        assert result == []
        slot.coordinator.set_freshness_gate.assert_not_called()

    @pytest.mark.asyncio
    async def test_git_error_on_worktrees_returns_empty(self) -> None:
        main_wt = _make_wt_slot("/repo")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt}
        daemon = _make_daemon(slots={"myrepo": slot})

        with patch(_GITOPS_PATH) as MockGitOps:
            MockGitOps.return_value.worktrees.side_effect = GitError("fail")
            result = await refresh_worktrees(daemon, "myrepo")

        assert result == []

    @pytest.mark.asyncio
    async def test_diff_scan_failure_still_registers(self) -> None:
        """If files_changed_vs fails, the worktree is still registered."""
        main_wt = _make_wt_slot("/repo")
        slot = MagicMock()
        slot.worktrees = {"main": main_wt}
        daemon = _make_daemon(slots={"myrepo": slot})
        daemon.registry.lookup_by_path.return_value = MagicMock()

        new_wt = SimpleNamespace(name="feat", path="/repo-wt/feat")
        with patch(_GITOPS_PATH) as MockGitOps:
            mock_main_git = MagicMock()
            mock_main_git.worktrees.return_value = [new_wt]
            mock_main_git.default_branch.return_value = "main"
            mock_wt_git = MagicMock()
            mock_wt_git.files_changed_vs.side_effect = OSError("disk")
            MockGitOps.side_effect = [mock_main_git, mock_wt_git]
            result = await refresh_worktrees(daemon, "myrepo")

        assert result == ["feat"]
        slot.coordinator.set_freshness_gate.assert_called_once()
        slot.indexer.queue_paths.assert_not_called()


# ===========================================================================
# build_app tests
# ===========================================================================

class TestBuildApp:
    """Test build_app delegates to build_global_app."""

    def test_delegates_to_build_global_app(self) -> None:
        daemon = MagicMock()
        with patch(_BUILD_GLOBAL_APP_PATH) as mock_build:
            mock_build.return_value = MagicMock()
            app = build_app(daemon, dev_mode=True)
        mock_build.assert_called_once_with(daemon, dev_mode=True)
        assert app is mock_build.return_value
