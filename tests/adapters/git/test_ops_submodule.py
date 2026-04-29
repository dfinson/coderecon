"""Tests for git submodule operations mixin."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.adapters.git.errors import SubmoduleError, SubmoduleNotFoundError
from coderecon.adapters.git.ops_submodule import _SubmoduleMixin


def _make_mixin(
    *,
    repo_path: Path | None = None,
    submodule_names: list[str] | None = None,
    submodule_lookup: dict | None = None,
) -> _SubmoduleMixin:
    """Build a _SubmoduleMixin instance with a mocked _access."""
    mixin = _SubmoduleMixin.__new__(_SubmoduleMixin)
    access = MagicMock()
    access.path = repo_path or Path("/fake/repo")
    access.listall_submodules.return_value = submodule_names or []
    if submodule_lookup:
        access.lookup_submodule.side_effect = lambda n: submodule_lookup[n]
        access.lookup_submodule_by_path.side_effect = lambda p: next(
            (v for v in submodule_lookup.values() if v["path"] == p), None
        )
    access.submodule_name_for_path.return_value = None
    access.git_dir = repo_path / ".git" if repo_path else Path("/fake/repo/.git")
    access.index = MagicMock()
    access.git = MagicMock()
    mixin._access = access  # type: ignore[attr-defined]
    return mixin


SM_LIB = {
    "name": "lib",
    "path": "external/lib",
    "url": "https://example.com/lib.git",
    "branch": "main",
    "head_id": "aaa111",
}

SM_UTILS = {
    "name": "utils",
    "path": "external/utils",
    "url": "https://example.com/utils.git",
    "branch": None,
    "head_id": "bbb222",
}


class TestSubmodulesList:
    """Tests for _SubmoduleMixin.submodules()."""

    @patch.object(_SubmoduleMixin, "_determine_submodule_status", return_value="clean")
    def test_lists_submodules(self, mock_status: MagicMock) -> None:
        mixin = _make_mixin(
            submodule_names=["lib", "utils"],
            submodule_lookup={"lib": SM_LIB, "utils": SM_UTILS},
        )
        result = mixin.submodules()
        assert len(result) == 2
        assert result[0].name == "lib"
        assert result[0].url == "https://example.com/lib.git"
        assert result[1].name == "utils"

    def test_handles_lookup_error_gracefully(self) -> None:
        from coderecon.adapters.git.errors import GitError

        mixin = _make_mixin(submodule_names=["broken"])
        mixin._access.lookup_submodule.side_effect = GitError("corrupt")  # type: ignore[attr-defined]
        result = mixin.submodules()
        assert len(result) == 1
        assert result[0].status == "missing"
        assert result[0].name == "broken"


class TestDetermineSubmoduleStatus:
    """Tests for _SubmoduleMixin._determine_submodule_status()."""

    def test_uninitialized_when_path_missing(self, tmp_path: Path) -> None:
        mixin = _make_mixin(repo_path=tmp_path)
        sm = {"path": "nonexistent", "head_id": "abc"}
        assert mixin._determine_submodule_status(sm) == "uninitialized"

    def test_uninitialized_when_no_dotgit(self, tmp_path: Path) -> None:
        sm_dir = tmp_path / "sub"
        sm_dir.mkdir()
        mixin = _make_mixin(repo_path=tmp_path)
        assert mixin._determine_submodule_status({"path": "sub", "head_id": "abc"}) == "uninitialized"

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_dirty_when_porcelain_has_output(self, mock_run: MagicMock, tmp_path: Path) -> None:
        sm_dir = tmp_path / "sub"
        sm_dir.mkdir()
        (sm_dir / ".git").touch()
        mock_run.return_value = MagicMock(stdout=" M file.txt\n", returncode=0)
        mixin = _make_mixin(repo_path=tmp_path)
        assert mixin._determine_submodule_status({"path": "sub", "head_id": "abc"}) == "dirty"

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_outdated_when_sha_mismatch(self, mock_run: MagicMock, tmp_path: Path) -> None:
        sm_dir = tmp_path / "sub"
        sm_dir.mkdir()
        (sm_dir / ".git").touch()
        # First call: status --porcelain (clean)
        # Second call: rev-parse HEAD (different sha)
        mock_run.side_effect = [
            MagicMock(stdout="", returncode=0),
            MagicMock(stdout="different_sha\n", returncode=0),
        ]
        mixin = _make_mixin(repo_path=tmp_path)
        assert mixin._determine_submodule_status({"path": "sub", "head_id": "recorded_sha"}) == "outdated"

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_clean_when_at_recorded_commit(self, mock_run: MagicMock, tmp_path: Path) -> None:
        sm_dir = tmp_path / "sub"
        sm_dir.mkdir()
        (sm_dir / ".git").touch()
        mock_run.side_effect = [
            MagicMock(stdout="", returncode=0),
            MagicMock(stdout="abc123\n", returncode=0),
        ]
        mixin = _make_mixin(repo_path=tmp_path)
        assert mixin._determine_submodule_status({"path": "sub", "head_id": "abc123"}) == "clean"

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_missing_on_subprocess_error(self, mock_run: MagicMock, tmp_path: Path) -> None:
        sm_dir = tmp_path / "sub"
        sm_dir.mkdir()
        (sm_dir / ".git").touch()
        mock_run.side_effect = subprocess.SubprocessError("fail")
        mixin = _make_mixin(repo_path=tmp_path)
        assert mixin._determine_submodule_status({"path": "sub", "head_id": "abc"}) == "missing"


class TestSubmoduleStatus:
    """Tests for _SubmoduleMixin.submodule_status()."""

    def test_raises_not_found(self) -> None:
        from coderecon.adapters.git.errors import GitError

        mixin = _make_mixin()
        mixin._access.lookup_submodule_by_path.side_effect = GitError("nope")  # type: ignore[attr-defined]
        with pytest.raises(SubmoduleNotFoundError):
            mixin.submodule_status("no/such/path")

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    @patch.object(_SubmoduleMixin, "_determine_submodule_status", return_value="dirty")
    def test_returns_detailed_status(
        self, mock_det: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        sm_dir = tmp_path / "external" / "lib"
        sm_dir.mkdir(parents=True)
        mixin = _make_mixin(
            repo_path=tmp_path,
            submodule_lookup={"lib": SM_LIB},
        )
        mixin._access.lookup_submodule_by_path.side_effect = None  # type: ignore[attr-defined]
        mixin._access.lookup_submodule_by_path.return_value = SM_LIB  # type: ignore[attr-defined]
        # rev-parse HEAD
        mock_run.side_effect = [
            MagicMock(stdout="actual_sha\n", returncode=0),
            MagicMock(stdout=" M dirty.txt\n?? untracked.txt\n", returncode=0),
        ]
        status = mixin.submodule_status("external/lib")
        assert status.info.name == "lib"
        assert status.actual_sha == "actual_sha"
        assert status.workdir_dirty is True
        assert status.untracked_count == 1


class TestSubmoduleInit:
    """Tests for _SubmoduleMixin.submodule_init()."""

    def test_init_all(self) -> None:
        mixin = _make_mixin(
            submodule_names=["lib", "utils"],
            submodule_lookup={"lib": SM_LIB, "utils": SM_UTILS},
        )
        result = mixin.submodule_init()
        assert set(result) == {"external/lib", "external/utils"}

    def test_init_specific_paths(self) -> None:
        mixin = _make_mixin()
        mixin._access.submodule_name_for_path.return_value = "lib"  # type: ignore[attr-defined]
        mixin._access.lookup_submodule.return_value = SM_LIB  # type: ignore[attr-defined]
        result = mixin.submodule_init(paths=["external/lib"])
        assert result == ["external/lib"]

    def test_init_raises_for_unknown_path(self) -> None:
        mixin = _make_mixin()
        mixin._access.submodule_name_for_path.return_value = None  # type: ignore[attr-defined]
        with pytest.raises(SubmoduleNotFoundError):
            mixin.submodule_init(paths=["nonexistent"])


class TestSubmoduleUpdate:
    """Tests for _SubmoduleMixin.submodule_update()."""

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Submodule path 'lib': checked out 'abc'\n",
        )
        mixin = _make_mixin()
        result = mixin.submodule_update()
        assert result.updated == ("lib",)
        assert result.failed == ()

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="fatal: something went wrong",
        )
        mixin = _make_mixin()
        result = mixin.submodule_update()
        assert result.updated == ()
        assert len(result.failed) == 1

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=300)
        mixin = _make_mixin()
        result = mixin.submodule_update()
        assert "timed out" in result.failed[0][1].lower()

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_recursive_and_paths(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        mixin = _make_mixin()
        mixin.submodule_update(paths=["lib"], recursive=True, init=False)
        cmd = mock_run.call_args[0][0]
        assert "--recursive" in cmd
        assert "--init" not in cmd
        assert "lib" in cmd


class TestSubmoduleSync:
    """Tests for _SubmoduleMixin.submodule_sync()."""

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        mock_run.return_value.check_returncode = MagicMock()
        mixin = _make_mixin()
        mixin.submodule_sync()  # should not raise

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd="git", stderr="sync failed"
        )
        mixin = _make_mixin()
        with pytest.raises(SubmoduleError, match="sync"):
            mixin.submodule_sync()


class TestSubmoduleAdd:
    """Tests for _SubmoduleMixin.submodule_add()."""

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        mixin = _make_mixin(submodule_lookup={"newmod": {
            "name": "newmod", "path": "vendor/newmod",
            "url": "https://example.com/new.git", "head_id": "ccc333",
        }})
        mixin._access.lookup_submodule_by_path.return_value = {  # type: ignore[attr-defined]
            "name": "newmod", "path": "vendor/newmod",
            "url": "https://example.com/new.git", "head_id": "ccc333",
        }
        result = mixin.submodule_add("https://example.com/new.git", "vendor/newmod", branch="main")
        assert result.name == "newmod"
        assert result.branch == "main"

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="clone failed")
        mixin = _make_mixin()
        with pytest.raises(SubmoduleError, match="Failed to add"):
            mixin.submodule_add("https://example.com/bad.git", "vendor/bad")


class TestSubmoduleDeinit:
    """Tests for _SubmoduleMixin.submodule_deinit()."""

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        mixin = _make_mixin()
        mixin.submodule_deinit("lib")  # should not raise

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_force_flag(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        mixin = _make_mixin()
        mixin.submodule_deinit("lib", force=True)
        cmd = mock_run.call_args[0][0]
        assert "--force" in cmd

    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="deinit error")
        mixin = _make_mixin()
        with pytest.raises(SubmoduleError, match="deinit"):
            mixin.submodule_deinit("lib")


class TestSubmoduleRemove:
    """Tests for _SubmoduleMixin.submodule_remove()."""

    def test_raises_not_found(self) -> None:
        mixin = _make_mixin()
        mixin._access.submodule_name_for_path.return_value = None  # type: ignore[attr-defined]
        with pytest.raises(SubmoduleNotFoundError):
            mixin.submodule_remove("no/such")

    @patch("shutil.rmtree")
    @patch("coderecon.adapters.git.ops_submodule.subprocess.run")
    @patch.object(_SubmoduleMixin, "submodule_deinit")
    def test_full_removal(
        self, mock_deinit: MagicMock, mock_run: MagicMock, mock_rmtree: MagicMock, tmp_path: Path
    ) -> None:
        sm_dir = tmp_path / "external" / "lib"
        sm_dir.mkdir(parents=True)
        modules_dir = tmp_path / ".git" / "modules" / "lib"
        modules_dir.mkdir(parents=True)
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.touch()

        mixin = _make_mixin(repo_path=tmp_path)
        mixin._access.submodule_name_for_path.return_value = "lib"  # type: ignore[attr-defined]
        mixin._access.git_dir = tmp_path / ".git"  # type: ignore[attr-defined]
        mock_run.return_value = MagicMock(returncode=0)

        mixin.submodule_remove("external/lib")

        mock_deinit.assert_called_once_with("external/lib", force=True)
        mixin._access.git.run_raw.assert_called_once()  # type: ignore[attr-defined]
