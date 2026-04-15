"""Tests for git managed hooks."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a fake git repo with hooks dir."""
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    return tmp_path


class TestInstallHooks:
    def test_installs_hooks(self, git_repo: Path) -> None:
        from coderecon.git._internal.managed_hooks import install_hooks

        installed = install_hooks(git_repo)
        assert "post-checkout" in installed
        assert "post-merge" in installed
        assert "post-rewrite" in installed

        # Verify file exists and is executable
        hook = git_repo / ".git" / "hooks" / "post-checkout"
        assert hook.exists()
        assert hook.stat().st_mode & stat.S_IEXEC

    def test_skips_existing_non_managed(self, git_repo: Path) -> None:
        """Existing non-managed hooks should not be overwritten."""
        from coderecon.git._internal.managed_hooks import install_hooks

        # Create a custom hook
        hook = git_repo / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\necho custom\n")

        installed = install_hooks(git_repo)
        assert "post-checkout" not in installed
        # Original content preserved
        assert "custom" in hook.read_text()

    def test_overwrites_managed_hooks(self, git_repo: Path) -> None:
        """Existing managed hooks should be updated."""
        from coderecon.git._internal.managed_hooks import install_hooks

        # Install once
        install_hooks(git_repo)
        # Install again — should succeed
        installed = install_hooks(git_repo)
        assert "post-checkout" in installed

    def test_no_git_dir(self, tmp_path: Path) -> None:
        from coderecon.git._internal.managed_hooks import install_hooks

        installed = install_hooks(tmp_path)
        assert installed == []


class TestUninstallHooks:
    def test_removes_managed_hooks(self, git_repo: Path) -> None:
        from coderecon.git._internal.managed_hooks import install_hooks, uninstall_hooks

        install_hooks(git_repo)
        removed = uninstall_hooks(git_repo)
        assert "post-checkout" in removed

        hook = git_repo / ".git" / "hooks" / "post-checkout"
        assert not hook.exists()

    def test_keeps_non_managed(self, git_repo: Path) -> None:
        from coderecon.git._internal.managed_hooks import uninstall_hooks

        hook = git_repo / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\necho custom\n")

        removed = uninstall_hooks(git_repo)
        assert "post-checkout" not in removed
        assert hook.exists()


class TestHooksInstalled:
    def test_detects_installed(self, git_repo: Path) -> None:
        from coderecon.git._internal.managed_hooks import hooks_installed, install_hooks

        install_hooks(git_repo)
        found = hooks_installed(git_repo)
        assert "post-checkout" in found

    def test_empty_when_none(self, git_repo: Path) -> None:
        from coderecon.git._internal.managed_hooks import hooks_installed

        found = hooks_installed(git_repo)
        assert found == []
