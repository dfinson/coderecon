"""Tests for MCP AppContext."""

from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock

from coderecon.mcp.context import AppContext

class TestAppContext:
    """Tests for AppContext dataclass."""

    def test_is_dataclass(self) -> None:
        """AppContext is a dataclass."""
        from dataclasses import is_dataclass

        assert is_dataclass(AppContext)

    def test_has_required_fields(self) -> None:
        """AppContext has all required fields."""
        field_names = {f.name for f in fields(AppContext)}
        expected = {
            "worktree_name",
            "repo_root",
            "git_ops",
            "coordinator",
            "gate",
            "router",
            "file_ops",
            "mutation_ops",
            "refactor_ops",
            "test_ops",
            "lint_ops",
            "session_manager",
        }
        assert expected <= field_names

    def test_manual_construction(self, tmp_path: Path) -> None:
        """Can construct AppContext manually."""
        ctx = AppContext(
            worktree_name="main",
            repo_root=tmp_path,
            git_ops=MagicMock(),
            coordinator=MagicMock(),
            gate=MagicMock(),
            router=MagicMock(),
            file_ops=MagicMock(),
            mutation_ops=MagicMock(),
            refactor_ops=MagicMock(),
            test_ops=MagicMock(),
            lint_ops=MagicMock(),
            session_manager=MagicMock(),
        )
        assert ctx.repo_root == tmp_path
        assert ctx.worktree_name == "main"
