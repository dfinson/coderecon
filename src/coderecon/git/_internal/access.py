"""Repository access layer - subprocess-based git operations."""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import structlog

from coderecon.git._internal.access_helpers import (
    _BranchHelper,
    _ParseMixin,
    _ReferenceHelper,
)
from coderecon.git._internal.access_index import GitIndex
from coderecon.git._internal.access_models import (
    _INDEX_STATUS_MAP,
    _WT_STATUS_MAP,
    GitBranchData,
    GitCommitData,
    GitReference,
    GitSignature,
    GitStashEntry,
    GitTagData,
)
from coderecon.git._internal.access_worktree import _WorktreeMixin
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
    STATUS_WT_NEW,
)
from coderecon.git._internal.runner import GitRunner
from coderecon.git.errors import (
    GitError,
    NotARepositoryError,
    RefNotFoundError,
    RemoteError,
)

log = structlog.get_logger(__name__)

class RepoAccess(_WorktreeMixin, _ParseMixin):
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
    # Repository State Facts
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
    # Resolution Helpers
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
    # Must Helpers
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
    # Normalization Helpers
    def normalize_path(self, path: str | Path) -> str:
        p = Path(path)
        if p.is_absolute():
            with contextlib.suppress(ValueError):
                p = p.relative_to(self.path)
        return str(p)
    # Remote Access
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
    # Branch Access
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
    # Low-level Git Operations
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
    # Index Helpers
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
                mode, _obj_type, sha = parts[0], parts[1], parts[2]
                # Use update-index to set the entry directly
                self._git.run(
                    "update-index", "--cacheinfo", f"{mode},{sha},{path}"
                )
        else:
            # Not in tree - remove from index
            self._git.run_raw("rm", "--cached", "--ignore-unmatch", "--", path)
    # Tag Iteration
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
    # merge_base and diff_numstat inherited from _WorktreeMixin
    # Internal Parsing
