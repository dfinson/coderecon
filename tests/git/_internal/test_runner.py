"""Tests for coderecon.git._internal.runner.GitRunner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from coderecon.git._internal.runner import GitRunner
from coderecon.git.errors import GitError, NotARepositoryError


class TestGitRunnerInit:
    """Test GitRunner initialization."""

    def test_cwd_property(self, tmp_path: Path) -> None:
        runner = GitRunner(tmp_path)
        assert runner.cwd == tmp_path


class TestGitRunnerRun:
    """Test GitRunner.run method."""

    def test_run_returns_completed_process(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(args=["git", "version"], returncode=0, stdout="git version 2.40\n", stderr="")
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp) as mock_run:
            result = runner.run("version")
        assert result.stdout == "git version 2.40\n"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["git", "version"]
        assert call_args[1]["cwd"] == tmp_path

    def test_run_raises_git_error_on_nonzero_exit(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(args=["git", "bad"], returncode=128, stdout="", stderr="unknown command")
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp):
            with pytest.raises(GitError, match="git bad failed"):
                runner.run("bad")

    def test_run_raises_not_a_repository_error(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(
            args=["git", "status"], returncode=128, stdout="",
            stderr="fatal: not a git repository (or any of the parent directories): .git",
        )
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp):
            with pytest.raises(NotARepositoryError):
                runner.run("status")

    def test_run_no_check_does_not_raise(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(args=["git", "diff"], returncode=1, stdout="diff output", stderr="")
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp):
            result = runner.run("diff", check=False)
        assert result.returncode == 1

    def test_run_raises_git_error_on_file_not_found(self, tmp_path: Path) -> None:
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError("git")):
            with pytest.raises(GitError, match="not installed"):
                runner.run("status")

    def test_run_raises_git_error_on_timeout(self, tmp_path: Path) -> None:
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git status", timeout=60)):
            with pytest.raises(GitError, match="timed out"):
                runner.run("status")

    def test_run_passes_input_to_subprocess(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(args=["git", "hash-object", "--stdin"], returncode=0, stdout="abc123\n", stderr="")
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp) as mock_run:
            runner.run("hash-object", "--stdin", input="hello")
        assert mock_run.call_args[1]["input"] == "hello"

    def test_run_passes_timeout_to_subprocess(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(args=["git", "log"], returncode=0, stdout="", stderr="")
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp) as mock_run:
            runner.run("log", timeout=30)
        assert mock_run.call_args[1]["timeout"] == 30


class TestGitRunnerRunRaw:
    """Test GitRunner.run_raw method."""

    def test_run_raw_returns_tuple(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="/repo\n", stderr="")
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp):
            rc, stdout, stderr = runner.run_raw("rev-parse", "--show-toplevel")
        assert rc == 0
        assert stdout == "/repo\n"
        assert stderr == ""

    def test_run_raw_nonzero_does_not_raise(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=128, stdout="", stderr="not a repo")
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", return_value=cp):
            rc, stdout, stderr = runner.run_raw("rev-parse")
        assert rc == 128

    def test_run_raw_raises_on_file_not_found(self, tmp_path: Path) -> None:
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError("git")):
            with pytest.raises(GitError, match="not installed"):
                runner.run_raw("status")

    def test_run_raw_raises_on_timeout(self, tmp_path: Path) -> None:
        runner = GitRunner(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60)):
            with pytest.raises(GitError, match="timed out"):
                runner.run_raw("status")
