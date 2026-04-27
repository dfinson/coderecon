"""Subprocess runner for git CLI commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

from coderecon.git.errors import GitError, NotARepositoryError

class GitRunner:
    """Runs git commands in a specific repository directory."""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    @property
    def cwd(self) -> Path:
        return self._cwd

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: int = 60,
        input: str | None = None,  # noqa: A002
    ) -> subprocess.CompletedProcess[str]:
        """
        Run a git command and return the result.

        Args:
            *args: Git subcommand and arguments (e.g., "status", "--porcelain").
            check: If True, raise GitError on non-zero exit.
            timeout: Max seconds to wait.
            input: Optional stdin data.

        Returns:
            CompletedProcess with stdout/stderr.

        Raises:
            GitError: On non-zero exit (when check=True).
            NotARepositoryError: When git reports "not a git repository".
        """
        cmd = ["git", *args]
        try:
            result = subprocess.run(
                cmd,
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input,
            )
        except FileNotFoundError:
            raise GitError("git is not installed or not on PATH") from None
        except subprocess.TimeoutExpired as e:
            raise GitError(f"git command timed out after {timeout}s: {' '.join(cmd)}") from e

        if check and result.returncode != 0:
            stderr = result.stderr.strip()
            if "not a git repository" in stderr.lower():
                raise NotARepositoryError(str(self._cwd))
            raise GitError(f"git {args[0]} failed (exit {result.returncode}): {stderr}")

        return result

    def run_raw(
        self,
        *args: str,
        timeout: int = 60,
    ) -> tuple[int, str, str]:
        """
        Run a git command and return (returncode, stdout, stderr) without raising.

        Use this for commands where non-zero exit is expected (e.g., rev-parse checks).
        """
        cmd = ["git", *args]
        try:
            result = subprocess.run(
                cmd,
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            raise GitError("git is not installed or not on PATH") from None
        except subprocess.TimeoutExpired as e:
            raise GitError(f"git command timed out after {timeout}s: {' '.join(cmd)}") from e
