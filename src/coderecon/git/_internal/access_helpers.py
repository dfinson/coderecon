"""Helper classes and parse methods for RepoAccess."""
from __future__ import annotations

from collections.abc import Iterator

import structlog

from coderecon.git._internal.access_models import (
    GitBranchData,
    GitCommitData,
    GitReference,
    GitSignature,
)
from coderecon.git._internal.runner import GitRunner
from coderecon.git.errors import GitError

log = structlog.get_logger(__name__)


class _ParseMixin:
    """Mixin providing commit/log/blame parsing methods."""

    def _parse_commit(self, ref: str) -> GitCommitData:
        """Parse a commit into GitCommitData."""
        fmt = "%H%n%T%n%P%n%an%n%ae%n%at%n%cn%n%ce%n%ct%n%B"
        result = self._git.run("log", "-1", f"--format={fmt}", ref)
        lines = result.stdout.split("\n")
        if len(lines) < 9:
            raise GitError(f"Failed to parse commit {ref}")
        sha = lines[0]
        tree_sha = lines[1]
        parent_line = lines[2]
        parent_shas = tuple(parent_line.split()) if parent_line.strip() else ()
        author = GitSignature(
            name=lines[3],
            email=lines[4],
            time=int(lines[5]),
            offset=0,
        )
        committer = GitSignature(
            name=lines[6],
            email=lines[7],
            time=int(lines[8]),
            offset=0,
        )
        # Message is everything after line 9
        message = "\n".join(lines[9:]).strip()
        return GitCommitData(
            sha=sha,
            tree_sha=tree_sha,
            parent_shas=parent_shas,
            author=author,
            committer=committer,
            message=message,
        )
    def _parse_log_output(self, output: str) -> list[GitCommitData]:
        """Parse output from git log with NUL-separated entries."""
        commits: list[GitCommitData] = []
        entries = output.split("\x00")
        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue
            lines = entry.split("\n")
            if len(lines) < 9:
                continue
            sha = lines[0]
            tree_sha = lines[1]
            parent_line = lines[2]
            parent_shas = tuple(parent_line.split()) if parent_line.strip() else ()
            author = GitSignature(
                name=lines[3], email=lines[4], time=int(lines[5]), offset=0
            )
            committer = GitSignature(
                name=lines[6], email=lines[7], time=int(lines[8]), offset=0
            )
            message = "\n".join(lines[9:]).strip()
            commits.append(
                GitCommitData(
                    sha=sha,
                    tree_sha=tree_sha,
                    parent_shas=parent_shas,
                    author=author,
                    committer=committer,
                    message=message,
                )
            )
        return commits
    def _parse_blame_output(self, output: str) -> list[dict]:
        """Parse git blame --porcelain output."""
        hunks: list[dict] = []
        current: dict | None = None
        lines = output.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            # Check if this is a commit header line
            parts = line.split()
            if len(parts) >= 4 and len(parts[0]) == 40:
                sha = parts[0]
                orig_line = int(parts[1])
                final_line = int(parts[2])
                num_lines = int(parts[3]) if len(parts) > 3 else 1
                # If this is a new hunk or different commit
                if current is None or current["sha"] != sha or current["final_line"] + current["num_lines"] != final_line:
                    if current:
                        hunks.append(current)
                    current = {
                        "sha": sha,
                        "final_line": final_line,
                        "num_lines": num_lines,
                        "orig_line": orig_line,
                        "author_name": "",
                        "author_email": "",
                        "author_time": 0,
                    }
                else:
                    # Extend current hunk
                    current["num_lines"] = (final_line - current["final_line"]) + num_lines
            elif current and line.startswith("author "):
                current["author_name"] = line[len("author "):]
            elif current and line.startswith("author-mail "):
                current["author_email"] = line[len("author-mail "):].strip("<>")
            elif current and line.startswith("author-time "):
                current["author_time"] = int(line[len("author-time "):])
            i += 1
        if current:
            hunks.append(current)
        return hunks


class _ReferenceHelper:
    """Helper for iterating references."""
    def __init__(self, git: GitRunner) -> None:
        self._git = git
    def __contains__(self, refname: str) -> bool:
        rc, _, _ = self._git.run_raw("rev-parse", "--verify", refname)
        return rc == 0
    def __iter__(self) -> Iterator[str]:
        result = self._git.run("for-each-ref", "--format=%(refname)")
        for line in result.stdout.strip().splitlines():
            if line:
                yield line
    def create(self, name: str, target_sha: str) -> None:
        self._git.run("update-ref", name, target_sha)
    def delete(self, name: str) -> None:
        self._git.run("update-ref", "-d", name)
    def __getitem__(self, name: str) -> GitReference:
        rc, stdout, _ = self._git.run_raw("rev-parse", "--verify", name)
        if rc != 0:
            raise KeyError(name)
        sha = stdout.strip()
        shorthand = name
        if name.startswith("refs/heads/"):
            shorthand = name[len("refs/heads/"):]
        elif name.startswith("refs/tags/"):
            shorthand = name[len("refs/tags/"):]
        return GitReference(name=name, target=sha, shorthand=shorthand)
class _BranchHelper:
    """Helper for branch iteration matching pygit2-like interface."""
    def __init__(self, git: GitRunner) -> None:
        self._git = git
    @property
    def local(self) -> _BranchCategory:
        return _BranchCategory(self._git, remote=False)
    @property
    def remote(self) -> _BranchCategory:
        return _BranchCategory(self._git, remote=True)
class _BranchCategory:
    """Category of branches (local or remote)."""
    def __init__(self, git: GitRunner, *, remote: bool) -> None:
        self._git = git
        self._remote = remote
    def __iter__(self) -> Iterator[str]:
        flag = "-r" if self._remote else "--list"
        result = self._git.run("branch", flag, "--format=%(refname:short)")
        for line in result.stdout.strip().splitlines():
            name = line.strip()
            if name:
                yield name
    def __contains__(self, name: str) -> bool:
        prefix = "refs/remotes/" if self._remote else "refs/heads/"
        rc, _, _ = self._git.run_raw("rev-parse", "--verify", f"{prefix}{name}")
        return rc == 0
    def __getitem__(self, name: str) -> GitBranchData:
        prefix = "refs/remotes/" if self._remote else "refs/heads/"
        rc, stdout, _ = self._git.run_raw("rev-parse", "--verify", f"{prefix}{name}")
        if rc != 0:
            raise KeyError(name)
        sha = stdout.strip()
        return GitBranchData(
            name=f"{prefix}{name}",
            shorthand=name,
            target=sha,
        )
