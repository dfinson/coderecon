"""Tests for refactor apply/cancel mixin."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.refactor.ops_models import (
    EditHunk,
    FileEdit,
    RefactorPreview,
    RefactorResult,
)
from coderecon.refactor.ops_apply import _ApplyMixin


def _make_mixin(
    *,
    repo_root: Path | None = None,
    pending: dict | None = None,
) -> _ApplyMixin:
    """Build an _ApplyMixin instance with mocked internals."""
    mixin = _ApplyMixin.__new__(_ApplyMixin)
    mixin._repo_root = repo_root or Path("/fake/repo")  # type: ignore[attr-defined]
    mixin._pending = pending if pending is not None else {}  # type: ignore[attr-defined]
    return mixin


def _make_preview(
    *,
    edits: list[FileEdit] | None = None,
    move_from: str | None = None,
    move_to: str | None = None,
) -> RefactorPreview:
    return RefactorPreview(
        files_affected=len(edits) if edits else 0,
        edits=edits or [],
        move_from=move_from,
        move_to=move_to,
    )


class TestApply:
    """Tests for _ApplyMixin.apply()."""

    @pytest.mark.asyncio
    async def test_raises_for_unknown_refactor_id(self) -> None:
        mixin = _make_mixin()
        with pytest.raises(ValueError, match="No pending refactor"):
            await mixin.apply("nonexistent", MagicMock())

    @pytest.mark.asyncio
    async def test_applies_simple_replacement(self, tmp_path: Path) -> None:
        # Set up a file with content to be refactored
        src = tmp_path / "mod.py"
        src.write_text("class OldName:\n    pass\n", encoding="utf-8")

        preview = _make_preview(edits=[
            FileEdit(path="mod.py", hunks=[
                EditHunk(old="OldName", new="NewName", line=1, certainty="high"),
            ]),
        ])
        mixin = _make_mixin(repo_root=tmp_path, pending={"ref1": preview})

        mock_mutation = MagicMock()
        mock_delta = MagicMock()
        mock_result = MagicMock()
        mock_result.delta = mock_delta
        mock_result.changed_paths = [Path("mod.py")]
        mock_mutation.write_source.return_value = mock_result

        result = await mixin.apply("ref1", mock_mutation)

        assert result.status == "applied"
        assert result.refactor_id == "ref1"
        assert "ref1" not in mixin._pending  # type: ignore[attr-defined]

        # Verify the edit passed to write_source has the replacement
        edit_arg = mock_mutation.write_source.call_args[0][0]
        assert len(edit_arg) == 1
        assert "NewName" in edit_arg[0].content
        assert "OldName" not in edit_arg[0].content

    @pytest.mark.asyncio
    async def test_skips_missing_files(self, tmp_path: Path) -> None:
        preview = _make_preview(edits=[
            FileEdit(path="gone.py", hunks=[
                EditHunk(old="x", new="y", line=1, certainty="high"),
            ]),
        ])
        mixin = _make_mixin(repo_root=tmp_path, pending={"ref1": preview})

        mock_mutation = MagicMock()
        mock_mutation.write_source.return_value = MagicMock(
            delta=MagicMock(), changed_paths=[]
        )

        result = await mixin.apply("ref1", mock_mutation)
        # No edits passed since file doesn't exist
        edit_arg = mock_mutation.write_source.call_args[0][0]
        assert len(edit_arg) == 0
        assert result.status == "applied"

    @pytest.mark.asyncio
    async def test_applies_multi_hunk_on_same_line(self, tmp_path: Path) -> None:
        src = tmp_path / "multi.py"
        src.write_text("a = foo + foo\n", encoding="utf-8")

        preview = _make_preview(edits=[
            FileEdit(path="multi.py", hunks=[
                EditHunk(old="foo", new="bar", line=1, certainty="high"),
            ]),
        ])
        mixin = _make_mixin(repo_root=tmp_path, pending={"ref2": preview})

        mock_mutation = MagicMock()
        mock_mutation.write_source.return_value = MagicMock(
            delta=MagicMock(), changed_paths=[]
        )

        await mixin.apply("ref2", mock_mutation)
        edit_arg = mock_mutation.write_source.call_args[0][0]
        # str.replace replaces all occurrences on the line
        assert "bar" in edit_arg[0].content
        assert "foo" not in edit_arg[0].content

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_git_mv_for_tracked_file(self, mock_run: MagicMock, tmp_path: Path) -> None:
        src = tmp_path / "old.py"
        src.write_text("content\n", encoding="utf-8")

        preview = _make_preview(
            edits=[],
            move_from="old.py",
            move_to="new.py",
        )
        mixin = _make_mixin(repo_root=tmp_path, pending={"mv1": preview})

        mock_mutation = MagicMock()
        mock_mutation.write_source.return_value = MagicMock(
            delta=MagicMock(), changed_paths=[]
        )
        # First subprocess.run: ls-files check succeeds (tracked)
        # Second: git mv succeeds
        mock_run.return_value = MagicMock(returncode=0)

        await mixin.apply("mv1", mock_mutation)
        # git mv should have been called
        calls = mock_run.call_args_list
        assert any("mv" in str(c) for c in calls)

    @pytest.mark.asyncio
    @patch("shutil.move")
    @patch("subprocess.run")
    async def test_shutil_move_for_untracked_file(
        self, mock_run: MagicMock, mock_shutil_move: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "untracked.py"
        src.write_text("content\n", encoding="utf-8")

        preview = _make_preview(
            edits=[],
            move_from="untracked.py",
            move_to="moved.py",
        )
        mixin = _make_mixin(repo_root=tmp_path, pending={"mv2": preview})

        mock_mutation = MagicMock()
        mock_mutation.write_source.return_value = MagicMock(
            delta=MagicMock(), changed_paths=[]
        )
        # ls-files fails (untracked)
        from subprocess import CalledProcessError
        mock_run.side_effect = CalledProcessError(1, "git ls-files")

        await mixin.apply("mv2", mock_mutation)
        mock_shutil_move.assert_called_once()


class TestCancel:
    """Tests for _ApplyMixin.cancel()."""

    @pytest.mark.asyncio
    async def test_cancels_existing(self) -> None:
        mixin = _make_mixin(pending={"ref1": _make_preview()})
        result = await mixin.cancel("ref1")
        assert result.status == "cancelled"
        assert result.refactor_id == "ref1"
        assert "ref1" not in mixin._pending  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_is_noop(self) -> None:
        mixin = _make_mixin()
        result = await mixin.cancel("ghost")
        assert result.status == "cancelled"


class TestClearPending:
    """Tests for _ApplyMixin.clear_pending()."""

    def test_clears_all(self) -> None:
        pending = {"a": _make_preview(), "b": _make_preview()}
        mixin = _make_mixin(pending=pending)
        mixin.clear_pending()
        assert len(mixin._pending) == 0  # type: ignore[attr-defined]

    def test_noop_when_empty(self) -> None:
        mixin = _make_mixin()
        mixin.clear_pending()  # should not raise
