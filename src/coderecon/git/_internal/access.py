"""Repository access layer - subprocess-based git operations."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from coderecon.git._internal.constants import (
    GIT_REPOSITORY_STATE_APPLY_MAILBOX,
    GIT_REPOSITORY_STATE_APPLY_MAILBOX_OR_REBASE,
    GIT_REPOSITORY_STATE_BISECT,
    GIT_REPOSITORY_STATE_CHERRYPICK,
    GIT_REPOSITORY_STATE_CHERRYPICK_SEQUENCE,
    GIT_REPOSITORY_STATE_MERGE,
    GIT_REPOSITORY_STATE_NONE,
    GIT_REPOSITORY_STATE_REBASE,
    GIT_REPOSITORY_STATE_REBASE_INTERACTIVE,
    GIT_REPOSITORY_STATE_REBASE_MERGE,
    GIT_REPOSITORY_STATE_REVERT,
    GIT_REPOSITORY_STATE_REVERT_SEQUENCE,
    STATUS_INDEX_DELETED,
    STATUS_INDEX_MODIFIED,
    STATUS_INDEX_NEW,
    STATUS_WT_DELETED,
    STATUS_WT_MODIFIED,
    STATUS_WT_NEW,
)
from coderecon.git._internal.runner import GitRunner
from coderecon.git.errors import (
    AuthenticationError,
    GitError,
    NotARepositoryError,
    RefNotFoundError,
    RemoteError,
)


# ---------------------------------------------------------------------------
# Lightweight replacement types (avoid dependency on pygit2 types)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GitSignature:
    """Lightweight git signature (author/committer)."""

    name: str
    email: str
    time: int  # Unix timestamp
    offset: int  # UTC offset in minutes


@dataclass(frozen=True, slots=True)
class GitCommitData:
    """Parsed commit data from git CLI."""

    sha: str
    tree_sha: str
    parent_shas: tuple[str, ...]
    author: GitSignature
    committer: GitSignature
    message: str


@dataclass(frozen=True, slots=True)
class GitReference:
    """Lightweight reference info."""

    name: str
    target: str  # SHA hex
    shorthand: str


@dataclass(frozen=True, slots=True)
class GitBranchData:
    """Parsed branch data."""

    name: str
    shorthand: str
    target: str  # SHA hex
    upstream: str | None = None


@dataclass(frozen=True, slots=True)
class GitIndexEntry:
    """A single entry in the git index."""

    path: str
    sha: str
    mode: int


@dataclass(frozen=True, slots=True)
class GitStashEntry:
    """A stash entry."""

    message: str
    commit_id: str


@dataclass(frozen=True, slots=True)
class GitTagData:
    """Parsed tag data."""

    name: str
    target_sha: str
    is_annotated: bool
    message: str | None = None
    tagger: GitSignature | None = None


# Status character → flag mapping for --porcelain=v1
_INDEX_STATUS_MAP = {
    "A": STATUS_INDEX_NEW,
    "M": STATUS_INDEX_MODIFIED,
    "D": STATUS_INDEX_DELETED,
    "R": STATUS_INDEX_MODIFIED,  # renamed = modified
    "C": STATUS_INDEX_NEW,  # copied = new
}

_WT_STATUS_MAP = {
    "?": STATUS_WT_NEW,
    "M": STATUS_WT_MODIFIED,
    "D": STATUS_WT_DELETED,
}


class GitIndex:
    """Git index operations via subprocess."""

    def __init__(self, git: GitRunner, path: Path) -> None:
        self._git = git
        self._path = path
        self._conflicts: list[tuple[GitIndexEntry | None, GitIndexEntry | None, GitIndexEntry | None]] | None = None

    def add(self, path_or_entry: str | GitIndexEntry) -> None:
        """Stage a file."""
        p = path_or_entry if isinstance(path_or_entry, str) else path_or_entry.path
        self._git.run("add", "--", p)
        self._conflicts = None  # Invalidate

    def remove(self, path: str) -> None:
        """Remove a file from the index."""
        self._git.run("rm", "--cached", "--", path)
        self._conflicts = None

    def write(self) -> None:
        """No-op: subprocess git add/rm are immediate."""

    def read(self) -> None:
        """No-op: subprocess always reads current index."""

    def write_tree(self) -> str:
        """Write current index as a tree object. Returns tree SHA."""
        result = self._git.run("write-tree")
        return result.stdout.strip()

    @property
    def conflicts(self) -> list[tuple[GitIndexEntry | None, GitIndexEntry | None, GitIndexEntry | None]] | None:
        """Get conflict entries, or None if no conflicts."""
        if self._conflicts is not None:
            return self._conflicts if self._conflicts else None

        result = self._git.run("ls-files", "-u")
        if not result.stdout.strip():
            self._conflicts = []
            return None

        # Parse default ls-files -u output: <mode> <sha> <stage>\t<path>
        conflicts_by_path: dict[str, list[GitIndexEntry | None]] = {}
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            meta, path = line.split("\t", 1)
            parts = meta.split()
            if len(parts) < 3:
                continue
            mode, sha, stage = int(parts[0], 8), parts[1], int(parts[2])
            if path not in conflicts_by_path:
                conflicts_by_path[path] = [None, None, None]
            if 1 <= stage <= 3:
                conflicts_by_path[path][stage - 1] = GitIndexEntry(path, sha, mode)

        self._conflicts = [
            (entries[0], entries[1], entries[2]) for entries in conflicts_by_path.values()
        ]
        return self._conflicts if self._conflicts else None

    def diff_to_tree(self, tree_sha: str) -> str:
        """Diff index against a tree. Returns raw diff text."""
        result = self._git.run("diff-index", "-p", "--no-color", tree_sha)
        return result.stdout

    def __contains__(self, path: str) -> bool:
        """Check if path is in the index."""
        rc, stdout, _ = self._git.run_raw("ls-files", "--error-unmatch", "--", path)
        return rc == 0

    def __getitem__(self, path: str) -> GitIndexEntry:
        """Get index entry by path."""
        result = self._git.run("ls-files", "-s", "--", path)
        for line in result.stdout.strip().splitlines():
            # Format: <mode> <sha> <stage>\t<path>
            meta, entry_path = line.split("\t", 1)
            parts = meta.split()
            if entry_path == path:
                return GitIndexEntry(path, parts[1], int(parts[0], 8))
        raise KeyError(path)

    def __len__(self) -> int:
        """Count files in index."""
        result = self._git.run("ls-files")
        lines = result.stdout.strip().splitlines()
        if not lines:
            return 0
        return len(lines) if lines[0] else 0

    def __iter__(self) -> Iterator[GitIndexEntry]:
        """Iterate all index entries."""
        result = self._git.run("ls-files", "-s")
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            meta, path = line.split("\t", 1)
            parts = meta.split()
            yield GitIndexEntry(path, parts[1], int(parts[0], 8))


class RepoAccess:
    """Owns subprocess git runner and provides normalized access to repo state."""

    def __init__(self, repo_path: Path | str) -> None:
        self._path = Path(repo_path)
        # Verify it's a git repo
        self._git = GitRunner(self._path)
        rc, stdout, stderr = self._git.run_raw("rev-parse", "--git-dir")
        if rc != 0:
            raise NotARepositoryError(str(self._path))
        self._git_dir = Path(stdout.strip())
        if not self._git_dir.is_absolute():
            self._git_dir = self._path / self._git_dir
        self._index = GitIndex(self._git, self._path)

    @property
    def git(self) -> GitRunner:
        """Direct access to the git runner."""
        return self._git

    @property
    def path(self) -> Path:
        return self._path

    @property
    def git_dir(self) -> Path:
        """The .git directory path."""
        return self._git_dir

    @property
    def index(self) -> GitIndex:
        return self._index

    # =========================================================================
    # Repository State Facts
    # =========================================================================

    @property
    def is_unborn(self) -> bool:
        rc, _, _ = self._git.run_raw("rev-parse", "--verify", "HEAD")
        return rc != 0

    @property
    def is_detached(self) -> bool:
        rc, _, _ = self._git.run_raw("symbolic-ref", "-q", "HEAD")
        return rc != 0

    @property
    def head_ref(self) -> GitReference:
        """Get HEAD reference (branch or detached)."""
        if self.is_detached:
            sha = self._git.run("rev-parse", "HEAD").stdout.strip()
            return GitReference(name="HEAD", target=sha, shorthand="HEAD")
        result = self._git.run("symbolic-ref", "HEAD")
        refname = result.stdout.strip()
        sha = self._git.run("rev-parse", "HEAD").stdout.strip()
        shorthand = refname.removeprefix("refs/heads/")
        return GitReference(name=refname, target=sha, shorthand=shorthand)

    @property
    def head_target(self) -> str:
        """Return HEAD target as SHA hex string."""
        return self._git.run("rev-parse", "HEAD").stdout.strip()

    def head_commit(self) -> GitCommitData | None:
        if self.is_unborn:
            return None
        return self._parse_commit("HEAD")

    def head_tree(self) -> str | None:
        """Return HEAD tree SHA, or None if unborn."""
        if self.is_unborn:
            return None
        result = self._git.run("rev-parse", "HEAD^{tree}")
        return result.stdout.strip()

    @property
    def default_signature(self) -> GitSignature:
        """Get configured user signature."""
        name = self._git.run("config", "user.name").stdout.strip()
        email = self._git.run("config", "user.email").stdout.strip()
        import time

        ts = int(time.time())
        # Get timezone offset
        local = time.localtime(ts)
        offset = local.tm_gmtoff // 60 if hasattr(local, "tm_gmtoff") else 0
        return GitSignature(name=name, email=email, time=ts, offset=offset)

    def current_branch_name(self) -> str | None:
        if self.is_unborn:
            # Check symbolic ref target
            rc, stdout, _ = self._git.run_raw("symbolic-ref", "-q", "HEAD")
            if rc == 0:
                ref = stdout.strip()
                if ref.startswith("refs/heads/"):
                    return ref[len("refs/heads/"):]
            return None
        if self.is_detached:
            return None
        result = self._git.run("branch", "--show-current")
        name = result.stdout.strip()
        return name or None

    def state(self) -> int:
        """Determine repository state by checking state files in .git/."""
        git_dir = self._git_dir
        if (git_dir / "rebase-merge" / "interactive").exists():
            return GIT_REPOSITORY_STATE_REBASE_INTERACTIVE
        if (git_dir / "rebase-merge").exists():
            return GIT_REPOSITORY_STATE_REBASE_MERGE
        if (git_dir / "rebase-apply" / "rebasing").exists():
            return GIT_REPOSITORY_STATE_REBASE
        if (git_dir / "rebase-apply" / "applying").exists():
            return GIT_REPOSITORY_STATE_APPLY_MAILBOX
        if (git_dir / "rebase-apply").exists():
            return GIT_REPOSITORY_STATE_APPLY_MAILBOX_OR_REBASE
        if (git_dir / "MERGE_HEAD").exists():
            return GIT_REPOSITORY_STATE_MERGE
        if (git_dir / "REVERT_HEAD").exists():
            if (git_dir / "sequencer").exists():
                return GIT_REPOSITORY_STATE_REVERT_SEQUENCE
            return GIT_REPOSITORY_STATE_REVERT
        if (git_dir / "CHERRY_PICK_HEAD").exists():
            if (git_dir / "sequencer").exists():
                return GIT_REPOSITORY_STATE_CHERRYPICK_SEQUENCE
            return GIT_REPOSITORY_STATE_CHERRYPICK
        if (git_dir / "BISECT_LOG").exists():
            return GIT_REPOSITORY_STATE_BISECT
        return GIT_REPOSITORY_STATE_NONE

    # =========================================================================
    # Resolution Helpers
    # =========================================================================

    def resolve_ref_oid(self, ref: str) -> str:
        """Resolve any ref to a SHA hex string."""
        rc, stdout, _ = self._git.run_raw("rev-parse", "--verify", ref)
        if rc != 0:
            raise RefNotFoundError(ref)
        return stdout.strip()

    def resolve_commit(self, ref: str) -> GitCommitData:
        """Resolve a ref to commit data."""
        # Ensure it resolves to a commit
        rc, stdout, _ = self._git.run_raw("rev-parse", "--verify", f"{ref}^{{commit}}")
        if rc != 0:
            raise RefNotFoundError(f"{ref} is not a commit")
        sha = stdout.strip()
        return self._parse_commit(sha)

    # =========================================================================
    # Must Helpers
    # =========================================================================

    def must_head_target(self) -> str:
        """Return HEAD target SHA, raising if unborn."""
        if self.is_unborn:
            raise GitError("HEAD has no target (unborn branch)")
        return self.head_target

    def must_head_commit(self) -> GitCommitData:
        commit = self.head_commit()
        if commit is None:
            raise GitError("HEAD has no commits (unborn branch)")
        return commit

    def must_head_tree(self) -> str:
        """Return HEAD tree SHA, raising if unborn."""
        tree = self.head_tree()
        if tree is None:
            raise GitError("HEAD has no tree (unborn branch)")
        return tree

    def must_local_branch(self, name: str) -> GitBranchData:
        if not self.has_local_branch(name):
            raise GitError(f"Branch {name!r} not found")
        return self._get_branch_data(name, remote=False)

    def must_remote_branch(self, name: str) -> GitBranchData:
        if not self.has_remote_branch(name):
            raise GitError(f"Remote branch {name!r} not found")
        return self._get_branch_data(name, remote=True)

    # =========================================================================
    # Normalization Helpers
    # =========================================================================

    def normalize_path(self, path: str | Path) -> str:
        p = Path(path)
        if p.is_absolute():
            with contextlib.suppress(ValueError):
                p = p.relative_to(self.path)
        return str(p)

    # =========================================================================
    # Remote Access
    # =========================================================================

    def get_remote(self, name: str) -> str:
        """Get remote URL. Raises RemoteError if not found."""
        rc, stdout, _ = self._git.run_raw("remote", "get-url", name)
        if rc != 0:
            raise RemoteError(name, "Remote not found")
        return stdout.strip()

    @property
    def remotes(self) -> list[tuple[str, str, str | None]]:
        """List remotes as (name, fetch_url, push_url) tuples."""
        result = self._git.run("remote", "-v")
        remotes_dict: dict[str, dict[str, str]] = {}
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 3:
                name = parts[0]
                url = parts[1]
                kind = parts[2].strip("()")
                if name not in remotes_dict:
                    remotes_dict[name] = {}
                remotes_dict[name][kind] = url
        return [
            (name, urls.get("fetch", ""), urls.get("push"))
            for name, urls in remotes_dict.items()
        ]

    # =========================================================================
    # Branch Access
    # =========================================================================

    def has_local_branch(self, name: str) -> bool:
        rc, _, _ = self._git.run_raw("rev-parse", "--verify", f"refs/heads/{name}")
        return rc == 0

    def has_remote_branch(self, name: str) -> bool:
        rc, _, _ = self._git.run_raw("rev-parse", "--verify", f"refs/remotes/{name}")
        return rc == 0

    def _get_branch_data(self, name: str, *, remote: bool) -> GitBranchData:
        """Get branch data by name."""
        prefix = "refs/remotes/" if remote else "refs/heads/"
        refname = f"{prefix}{name}"
        sha = self._git.run("rev-parse", refname).stdout.strip()

        # Get upstream if local branch
        upstream: str | None = None
        if not remote:
            rc, stdout, _ = self._git.run_raw(
                "config", f"branch.{name}.remote"
            )
            if rc == 0:
                remote_name = stdout.strip()
                rc2, stdout2, _ = self._git.run_raw(
                    "config", f"branch.{name}.merge"
                )
                if rc2 == 0:
                    merge_ref = stdout2.strip()
                    upstream = f"{remote_name}/{merge_ref.removeprefix('refs/heads/')}"

        return GitBranchData(
            name=refname if remote else name,
            shorthand=name,
            target=sha,
            upstream=upstream,
        )

    def create_local_branch(self, name: str, target_sha: str) -> GitBranchData:
        """Create a local branch at target. Returns branch data."""
        self._git.run("branch", name, target_sha)
        return self._get_branch_data(name, remote=False)

    def set_branch_target(self, name: str, sha: str) -> None:
        """Move a branch to point to a new SHA."""
        self._git.run("branch", "-f", name, sha)

    def delete_branch(self, name: str) -> None:
        """Force delete a local branch."""
        self._git.run("branch", "-D", name)

    # =========================================================================
    # Low-level Git Operations
    # =========================================================================

    def status(self) -> dict[str, int]:
        """Get status flags by path, compatible with existing flag constants."""
        result = self._git.run("status", "--porcelain=v1", "-uall")
        status_dict: dict[str, int] = {}
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            x_char = line[0]
            y_char = line[1]
            # Handle renames: "R  old -> new"
            path_part = line[3:]
            if " -> " in path_part and x_char == "R":
                _, path_part = path_part.rsplit(" -> ", 1)
            path = path_part

            flags = 0
            # Index flags (X column)
            if x_char in _INDEX_STATUS_MAP:
                flags |= _INDEX_STATUS_MAP[x_char]
            # Working tree flags (Y column)
            if y_char in _WT_STATUS_MAP:
                flags |= _WT_STATUS_MAP[y_char]
            # Untracked: ?? means both columns are ?
            if x_char == "?" and y_char == "?":
                flags = STATUS_WT_NEW

            if flags:
                status_dict[path] = flags

        return status_dict

    def blame(self, path: str, **kwargs: int) -> list[dict]:
        """Get blame data as list of hunk dicts."""
        cmd = ["blame", "--porcelain"]
        if "min_line" in kwargs:
            cmd.extend(["-L", f"{kwargs['min_line']},"])
            if "max_line" in kwargs:
                cmd[-1] = f"{kwargs['min_line']},{kwargs['max_line']}"
        elif "max_line" in kwargs:
            cmd.extend(["-L", f"1,{kwargs['max_line']}"])
        cmd.append(path)
        result = self._git.run(*cmd)
        return self._parse_blame_output(result.stdout)

    def walk_commits(self, start_sha: str, limit: int = 10000) -> list[GitCommitData]:
        """Walk commits from start SHA."""
        result = self._git.run(
            "log",
            f"--max-count={limit}",
            "--format=%H%n%T%n%P%n%an%n%ae%n%at%n%cn%n%ce%n%ct%n%B%x00",
            start_sha,
        )
        return self._parse_log_output(result.stdout)

    def walk_commits_excluding(
        self, include: str, exclude: str
    ) -> list[GitCommitData]:
        """Walk commits reachable from include but not from exclude (topological, oldest first)."""
        result = self._git.run(
            "log",
            "--reverse",
            "--topo-order",
            "--format=%H%n%T%n%P%n%an%n%ae%n%at%n%cn%n%ce%n%ct%n%B%x00",
            f"{exclude}..{include}",
        )
        return self._parse_log_output(result.stdout)

    def diff_working_tree(self) -> str:
        """Diff working tree against index. Returns raw diff text."""
        return self._git.run("diff", "--no-color").stdout

    def diff_staged(self) -> str:
        """Diff staged changes against HEAD (or empty tree if unborn)."""
        if self.is_unborn:
            empty_tree = self._git.run("hash-object", "-t", "tree", "/dev/null").stdout.strip()
            return self._git.run("diff-index", "-p", "--no-color", "--cached", empty_tree).stdout
        return self._git.run("diff", "--cached", "--no-color").stdout

    def diff_refs(self, base_sha: str, target_sha: str | None = None) -> str:
        """Diff between refs. Returns raw diff text."""
        if target_sha is None:
            return self._git.run("diff", "--no-color", base_sha).stdout
        return self._git.run("diff", "--no-color", base_sha, target_sha).stdout

    def get_empty_tree_sha(self) -> str:
        """Get the SHA of an empty tree."""
        result = self._git.run("hash-object", "-t", "tree", "--stdin", input="")
        return result.stdout.strip()

    def checkout_branch(self, name: str) -> None:
        """Checkout a local branch."""
        self._git.run("checkout", name)

    def checkout_detached(self, sha: str) -> None:
        """Checkout a specific commit (detached HEAD)."""
        self._git.run("checkout", "--detach", sha)

    def set_head(self, refname: str) -> None:
        """Set HEAD to a ref name (e.g., 'refs/heads/main')."""
        self._git.run("symbolic-ref", "HEAD", refname)

    def merge_analysis(self, their_sha: str) -> int:
        """Analyze potential merge. Returns bitmask of MERGE_* flags."""
        from coderecon.git._internal.constants import (
            MERGE_FASTFORWARD,
            MERGE_NORMAL,
            MERGE_UP_TO_DATE,
        )

        head_sha = self.must_head_target()

        # Check if already up-to-date (their commit is ancestor of HEAD)
        rc, _, _ = self._git.run_raw("merge-base", "--is-ancestor", their_sha, head_sha)
        if rc == 0:
            return MERGE_UP_TO_DATE

        # Check if fast-forward is possible (HEAD is ancestor of their commit)
        rc, _, _ = self._git.run_raw("merge-base", "--is-ancestor", head_sha, their_sha)
        if rc == 0:
            return MERGE_FASTFORWARD

        # Normal merge needed
        return MERGE_NORMAL

    def merge(self, their_sha: str) -> None:
        """Merge without commit (--no-commit)."""
        self._git.run("merge", "--no-commit", "--no-ff", their_sha, check=False)
        self._index._conflicts = None  # Invalidate after merge

    def cherrypick(self, commit_sha: str) -> None:
        """Cherry-pick without commit."""
        self._git.run("cherry-pick", "--no-commit", commit_sha, check=False)
        self._index._conflicts = None  # Invalidate after cherry-pick

    def revert_commit(self, commit_sha: str) -> None:
        """Revert a commit without committing."""
        self._git.run("revert", "--no-commit", commit_sha, check=False)
        self._index._conflicts = None  # Invalidate after revert

    def reset(self, sha: str, mode: str) -> None:
        """Reset HEAD. mode: 'soft', 'mixed', or 'hard'."""
        self._git.run("reset", f"--{mode}", sha)
        self._index._conflicts = None  # Invalidate

    def state_cleanup(self) -> None:
        """Remove merge/cherrypick/revert state files."""
        git_dir = self._git_dir
        for fname in ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            f = git_dir / fname
            if f.exists():
                f.unlink()

    def stash(
        self, message: str | None, *, include_untracked: bool
    ) -> str:
        """Stash changes. Returns stash commit SHA."""
        cmd = ["stash", "push"]
        if message:
            cmd.extend(["-m", message])
        if include_untracked:
            cmd.append("--include-untracked")
        self._git.run(*cmd)
        # Get the stash commit SHA
        result = self._git.run("rev-parse", "stash@{0}")
        return result.stdout.strip()

    def stash_apply(self, index: int) -> None:
        self._git.run("stash", "apply", f"stash@{{{index}}}")

    def stash_drop(self, index: int) -> None:
        self._git.run("stash", "drop", f"stash@{{{index}}}")

    def listall_stashes(self) -> list[GitStashEntry]:
        """List stash entries."""
        rc, stdout, _ = self._git.run_raw("stash", "list", "--format=%H %s")
        if rc != 0 or not stdout.strip():
            return []
        entries = []
        for line in stdout.strip().splitlines():
            parts = line.split(" ", 1)
            sha = parts[0]
            msg = parts[1] if len(parts) > 1 else ""
            entries.append(GitStashEntry(message=msg, commit_id=sha))
        return entries

    def create_tag(
        self,
        name: str,
        target_sha: str,
        message: str | None = None,
    ) -> str:
        """Create a tag. Returns the tag object SHA (annotated) or target SHA (lightweight)."""
        if message:
            self._git.run("tag", "-a", "-m", message, name, target_sha)
            result = self._git.run("rev-parse", f"refs/tags/{name}")
            return result.stdout.strip()
        self._git.run("tag", name, target_sha)
        return target_sha

    def create_reference(self, name: str, target_sha: str) -> None:
        """Create or update a reference."""
        self._git.run("update-ref", name, target_sha)

    def delete_reference(self, name: str) -> None:
        """Delete a reference."""
        self._git.run("update-ref", "-d", name)

    def has_reference(self, name: str) -> bool:
        rc, _, _ = self._git.run_raw("rev-parse", "--verify", name)
        return rc == 0

    def get_reference_target(self, name: str) -> str:
        """Get target SHA of a reference."""
        return self._git.run("rev-parse", name).stdout.strip()

    def create_commit(
        self,
        ref: str | None,
        author: GitSignature,
        committer: GitSignature,
        message: str,
        tree_sha: str,
        parent_shas: list[str],
    ) -> str:
        """Create a commit using git commit-tree. Returns commit SHA."""
        import os

        env_overrides = {
            "GIT_AUTHOR_NAME": author.name,
            "GIT_AUTHOR_EMAIL": author.email,
            "GIT_AUTHOR_DATE": f"@{author.time} {'+' if author.offset >= 0 else '-'}{abs(author.offset) // 60:02d}{abs(author.offset) % 60:02d}",
            "GIT_COMMITTER_NAME": committer.name,
            "GIT_COMMITTER_EMAIL": committer.email,
            "GIT_COMMITTER_DATE": f"@{committer.time} {'+' if committer.offset >= 0 else '-'}{abs(committer.offset) // 60:02d}{abs(committer.offset) % 60:02d}",
        }

        cmd = ["commit-tree", tree_sha]
        for parent in parent_shas:
            cmd.extend(["-p", parent])

        # Use subprocess directly to set env vars
        import subprocess

        full_cmd = ["git", *cmd]
        env = {**os.environ, **env_overrides}
        result = subprocess.run(
            full_cmd,
            cwd=self._path,
            capture_output=True,
            text=True,
            timeout=60,
            input=message,
            env=env,
        )
        if result.returncode != 0:
            raise GitError(f"commit-tree failed: {result.stderr.strip()}")

        oid = result.stdout.strip()

        # Update ref if specified
        if ref:
            self._git.run("update-ref", ref, oid)

        return oid

    def descendant_of(self, commit_sha: str, ancestor_sha: str) -> bool:
        """Check if commit is a descendant of ancestor."""
        rc, _, _ = self._git.run_raw("merge-base", "--is-ancestor", ancestor_sha, commit_sha)
        return rc == 0

    @property
    def references(self) -> _ReferenceHelper:
        """Reference helper for iteration."""
        return _ReferenceHelper(self._git)

    @property
    def branches(self) -> _BranchHelper:
        """Branch helper."""
        return _BranchHelper(self._git)

    # =========================================================================
    # Index Helpers
    # =========================================================================

    def best_effort_index_remove(self, paths: Iterator[str]) -> None:
        """Remove paths from index, suppressing errors for missing entries."""
        for p in paths:
            self._git.run_raw("rm", "--cached", "--ignore-unmatch", "--", p)

    def index_reset_entry(self, path: str, tree_sha: str) -> None:
        """Reset a single index entry to match tree, or remove if not in tree."""
        # Try to get the entry from the tree
        rc, stdout, _ = self._git.run_raw("ls-tree", tree_sha, "--", path)
        if rc == 0 and stdout.strip():
            # Entry exists in tree - restore it
            self._git.run("checkout", tree_sha, "--", path)
            self._git.run("reset", "HEAD", "--", path)
            # Actually, the correct approach is to update the index entry
            # to match the tree without touching the working tree
            parts = stdout.strip().split(None, 3)
            if len(parts) >= 3:
                mode, obj_type, sha = parts[0], parts[1], parts[2]
                # Use update-index to set the entry directly
                self._git.run(
                    "update-index", "--cacheinfo", f"{mode},{sha},{path}"
                )
        else:
            # Not in tree - remove from index
            self._git.run_raw("rm", "--cached", "--ignore-unmatch", "--", path)

    # =========================================================================
    # Tag Iteration
    # =========================================================================

    def iter_tags(self) -> Iterator[GitTagData]:
        """Iterate tags as GitTagData objects."""
        result = self._git.run(
            "for-each-ref",
            "--format=%(refname:short)%09%(objecttype)%09%(*objectname)%09%(objectname)%09%(contents:subject)%09%(taggername)%09%(taggeremail)%09%(taggerdate:unix)",
            "refs/tags/",
        )
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name = parts[0]
            obj_type = parts[1]
            deref_sha = parts[2] if len(parts) > 2 else ""
            tag_sha = parts[3]
            message = parts[4] if len(parts) > 4 else None
            tagger_name = parts[5] if len(parts) > 5 else None
            tagger_email = parts[6] if len(parts) > 6 else None
            tagger_time = parts[7] if len(parts) > 7 else None

            if obj_type == "tag":
                # Annotated tag
                target = deref_sha or tag_sha
                tagger = None
                if tagger_name and tagger_email:
                    email = tagger_email.strip("<>")
                    ts = int(tagger_time) if tagger_time else 0
                    tagger = GitSignature(tagger_name, email, ts, 0)
                yield GitTagData(
                    name=name,
                    target_sha=target,
                    is_annotated=True,
                    message=message,
                    tagger=tagger,
                )
            else:
                # Lightweight tag
                yield GitTagData(
                    name=name,
                    target_sha=tag_sha,
                    is_annotated=False,
                )

    # =========================================================================
    # Remote Operations
    # =========================================================================

    def run_remote_operation(
        self,
        remote_name: str,
        op_name: str,
        args: list[str],
        *,
        timeout: int = 300,
    ) -> None:
        """Run a remote operation with centralized error mapping."""
        # Verify remote exists
        self.get_remote(remote_name)
        try:
            self._git.run(*args, timeout=timeout)
        except GitError as e:
            msg = str(e).lower()
            if "authentication" in msg or "credential" in msg:
                raise AuthenticationError(remote_name, op_name) from e
            raise RemoteError(remote_name, f"{op_name} failed: {e}") from e

    # =========================================================================
    # Worktree Operations
    # =========================================================================

    def list_worktrees(self) -> list[str]:
        """List worktree names (excluding main)."""
        result = self._git.run("worktree", "list", "--porcelain")
        names: list[str] = []
        current_is_main = True
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current_is_main:
                    current_is_main = False
                    continue
                # Extract name from path
                wt_path = line[len("worktree "):]
                names.append(Path(wt_path).name)
        return names

    def worktree_path(self, name: str) -> str:
        """Get worktree path by name."""
        result = self._git.run("worktree", "list", "--porcelain")
        current_path = ""
        current_is_main = True
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current_is_main:
                    current_is_main = False
                    current_path = line[len("worktree "):]
                    continue
                current_path = line[len("worktree "):]
                if Path(current_path).name == name:
                    return current_path
        raise GitError(f"Worktree not found: {name}")

    def worktree_is_prunable(self, name: str) -> bool:
        """Check if worktree is prunable (directory missing)."""
        try:
            wt_path = self.worktree_path(name)
            return not Path(wt_path).exists()
        except GitError:
            return True

    def add_worktree(self, name: str, path: str, branch: str | None = None) -> None:
        """Add a new worktree."""
        cmd = ["worktree", "add"]
        if branch:
            cmd.extend(["-b" if not self.has_local_branch(branch) else "--checkout", path, branch])
        else:
            cmd.extend(["--detach", path])
        # Actually, for existing branch:
        cmd = ["worktree", "add", path]
        if branch:
            cmd.append(branch)
        self._git.run(*cmd)

    def is_worktree(self) -> bool:
        """True if this repository is a worktree (not the main working directory)."""
        git_path = self.path / ".git"
        return git_path.is_file()

    @property
    def workdir(self) -> str | None:
        """Working directory path."""
        rc, stdout, _ = self._git.run_raw("rev-parse", "--show-toplevel")
        if rc == 0:
            return stdout.strip() + "/"
        return None

    def worktree_gitdir(self, name: str) -> Path:
        """Get the git admin directory for a worktree."""
        return self._git_dir / "worktrees" / name

    def remove_worktree(self, name: str, force: bool = False) -> None:
        """Remove worktree."""
        wt_path = self.worktree_path(name)
        cmd = ["worktree", "remove"]
        if force:
            cmd.extend(["--force", "--force"])
        cmd.append(wt_path)
        self._git.run(*cmd)

    # =========================================================================
    # Submodule Operations
    # =========================================================================

    def listall_submodules(self) -> list[str]:
        """List submodule names."""
        rc, stdout, _ = self._git.run_raw("config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
        if rc != 0:
            return []
        names = []
        for line in stdout.strip().splitlines():
            # Format: submodule.<name>.path <path>
            match = re.match(r"submodule\.(.+)\.path\s+", line)
            if match:
                names.append(match.group(1))
        return names

    def lookup_submodule(self, name: str) -> dict:
        """Get submodule info as dict."""
        rc, path_out, _ = self._git.run_raw("config", "--file", ".gitmodules", f"submodule.{name}.path")
        if rc != 0:
            raise GitError(f"Submodule not found: {name}")
        path = path_out.strip()
        rc2, url_out, _ = self._git.run_raw("config", "--file", ".gitmodules", f"submodule.{name}.url")
        url = url_out.strip() if rc2 == 0 else ""
        rc3, branch_out, _ = self._git.run_raw("config", "--file", ".gitmodules", f"submodule.{name}.branch")
        branch = branch_out.strip() if rc3 == 0 else None

        # Get recorded HEAD sha from index
        head_id = None
        rc4, ls_out, _ = self._git.run_raw("ls-tree", "HEAD", "--", path)
        if rc4 == 0 and ls_out.strip():
            parts = ls_out.strip().split()
            if len(parts) >= 3:
                head_id = parts[2]

        return {"name": name, "path": path, "url": url, "branch": branch, "head_id": head_id}

    def init_submodule(self, name: str) -> None:
        """Initialize a submodule."""
        sm = self.lookup_submodule(name)
        self._git.run("submodule", "init", "--", sm["path"])

    def submodule_name_for_path(self, path: str) -> str | None:
        """Get submodule name for a given path."""
        for name in self.listall_submodules():
            try:
                sm = self.lookup_submodule(name)
                if sm["path"] == path:
                    return name
            except GitError:
                continue
        return None

    def lookup_submodule_by_path(self, path: str) -> dict:
        """Get submodule by path."""
        name = self.submodule_name_for_path(path)
        if name is None:
            raise GitError(f"Submodule not found at path: {path}")
        return self.lookup_submodule(name)

    # =========================================================================
    # Merge Base
    # =========================================================================

    def merge_base(self, sha1: str, sha2: str) -> str | None:
        """Find merge base of two commits. Returns None if unrelated."""
        rc, stdout, _ = self._git.run_raw("merge-base", sha1, sha2)
        if rc != 0:
            return None
        return stdout.strip()

    # =========================================================================
    # Diff Parsing
    # =========================================================================

    def diff_numstat(self, *args: str) -> list[tuple[str, int, int, str]]:
        """Run diff with --numstat and return (status, additions, deletions, path) tuples."""
        result = self._git.run("diff", "--numstat", "--diff-filter=ACDMR", "--no-color", *args)
        # Also get name-status for the delta status
        status_result = self._git.run("diff", "--name-status", "--diff-filter=ACDMR", "--no-color", *args)

        status_map: dict[str, str] = {}
        for line in status_result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                status_char = parts[0][0]  # First char of status
                path = parts[-1]  # Last part is the path (handles renames)
                status_map[path] = status_char

        entries = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                adds = int(parts[0]) if parts[0] != "-" else 0
                dels = int(parts[1]) if parts[1] != "-" else 0
                path = parts[2]
                status = status_map.get(path, "M")
                entries.append((status, adds, dels, path))
        return entries

    # =========================================================================
    # Internal Parsing
    # =========================================================================

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
