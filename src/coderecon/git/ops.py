"""Git operations via subprocess - returns serializable data models."""
from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from coderecon.git._internal import (
    CheckoutPlanner,
    DiffPlanner,
    RebaseFlow,
    RebasePlanner,
    RepoAccess,
    WriteFlows,
    check_nothing_to_commit,
    first_line,
    make_tag_ref,
    require_branch_exists,
    require_current_branch,
    require_not_current_branch,
    require_not_unborn,
)
from coderecon.git._internal.constants import (
    MERGE_FASTFORWARD,
    MERGE_NORMAL,
    MERGE_UP_TO_DATE,
    RESET_HARD,
    RESET_MIXED,
    RESET_SOFT,
    STATUS_WT_DELETED,
    STATUS_WT_MODIFIED,
    STATUS_WT_NEW,
)
from coderecon.git.errors import (
    BranchExistsError,
    BranchNotFoundError,
    GitError,
    RefNotFoundError,
    StashNotFoundError,
    SubmoduleError,
    SubmoduleNotFoundError,
    UnmergedBranchError,
    WorktreeError,
    WorktreeExistsError,
    WorktreeLockedError,
    WorktreeNotFoundError,
)
from coderecon.git.models import (
    BlameInfo,
    BranchInfo,
    CommitInfo,
    DiffInfo,
    DiffSummary,
    MergeAnalysis,
    MergeResult,
    OperationResult,
    PullResult,
    RebasePlan,
    RebaseResult,
    RefInfo,
    RemoteInfo,
    Signature,
    StashEntry,
    SubmoduleInfo,
    SubmoduleState,
    SubmoduleStatus,
    SubmoduleUpdateResult,
    TagInfo,
    WorktreeInfo,
)

from coderecon.files.ops import atomic_write_text

log = structlog.get_logger(__name__)


class GitOps:
    """Git operations via subprocess with serializable data models."""
    def __init__(self, repo_path: Path | str) -> None:
        self._access = RepoAccess(repo_path)
        self._flows = WriteFlows(self._access)
        self._diff_planner = DiffPlanner(self._access)
        self._checkout_planner = CheckoutPlanner(self._access)
        self._rebase_planner = RebasePlanner(self._access)
        self._rebase_flow = RebaseFlow(self._access)
    def _head_sha(self) -> str:
        """Get HEAD target SHA, raising if unborn."""
        return self._access.must_head_target()
    @property
    def path(self) -> Path:
        """Repository root path."""
        return self._access.path
    # Read Operations
    def status(self) -> dict[str, int]:
        """Get status flags by path. Use STATUS_* constants to interpret."""
        return self._access.status()
    def head(self) -> RefInfo:
        """Get HEAD reference info."""
        ref = self._access.head_ref
        return RefInfo(
            name=ref.name,
            target_sha=ref.target,
            shorthand=ref.shorthand,
            is_detached=self._access.is_detached,
        )
    def head_commit(self) -> CommitInfo | None:
        """Get HEAD commit, or None if unborn."""
        commit = self._access.head_commit()
        return CommitInfo.from_git(commit) if commit else None
    def diff(
        self,
        base: str | None = None,
        target: str | None = None,
        staged: bool = False,
        include_patch: bool = False,
    ) -> DiffInfo:
        """Generate diff."""
        plan = self._diff_planner.plan(base, target, staged)
        result = self._diff_planner.execute(plan)
        return DiffInfo.from_diff_text(result.diff_text, result.numstat, include_patch=include_patch)
    def files_changed_vs(self, base_ref: str) -> list[str]:
        """Return repo-relative paths that differ between *base_ref* and HEAD.
        Uses ``git diff --name-only base_ref...HEAD`` (three-dot form, which
        finds the merge-base automatically).  Returns an empty list if the
        command fails or there are no differences.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["git", "-C", str(self._access.path), "diff", "--name-only",
                 f"{base_ref}...HEAD"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                return []
            return [line for line in result.stdout.splitlines() if line]
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001
            return []
    def diff_summary(
        self,
        base: str | None = None,
        target: str | None = None,
        staged: bool = False,
        *,
        include_per_file: bool = False,
        include_word_count: bool = False,
    ) -> DiffSummary:
        """Get diff statistics with progressive detail levels."""
        plan = self._diff_planner.plan(base, target, staged)
        result = self._diff_planner.execute(plan)
        return DiffSummary.from_diff_text(
            result.diff_text, result.numstat,
            include_per_file=include_per_file, include_word_count=include_word_count,
        )
    def blame(
        self, path: str, min_line: int | None = None, max_line: int | None = None
    ) -> BlameInfo:
        """Get blame for a file."""
        kwargs: dict[str, int] = {}
        if min_line is not None:
            kwargs["min_line"] = min_line
        if max_line is not None:
            kwargs["max_line"] = max_line
        blame_hunks = self._access.blame(path, **kwargs)
        return BlameInfo.from_blame_data(path, blame_hunks)
    def log(
        self,
        ref: str = "HEAD",
        limit: int = 50,
        since: str | None = None,
        until: str | None = None,
        paths: Sequence[str] | None = None,
    ) -> list[CommitInfo]:
        """Get commit history."""
        try:
            start_sha = self._access.resolve_ref_oid(ref)
        except RefNotFoundError as e:
            raise RefNotFoundError(ref) from e
        # Build git log command
        cmd = ["log", f"--max-count={limit}", "--format=%H%n%T%n%P%n%an%n%ae%n%at%n%cn%n%ce%n%ct%n%B%x00"]
        if since:
            cmd.append(f"--since={since}")
        if until:
            cmd.append(f"--until={until}")
        cmd.append(start_sha)
        if paths:
            cmd.append("--")
            cmd.extend(paths)
        result = self._access.git.run(*cmd)
        commits_data = self._access._parse_log_output(result.stdout)
        return [CommitInfo.from_git(c) for c in commits_data]
    def show(self, ref: str = "HEAD") -> CommitInfo:
        """Get commit info."""
        return CommitInfo.from_git(self._access.resolve_commit(ref))
    def branches(self, include_remote: bool = True) -> list[BranchInfo]:
        """List branches."""
        result: list[BranchInfo] = []
        for name in self._access.branches.local:
            branch_data = self._access.branches.local[name]
            result.append(BranchInfo.from_git(branch_data))
        if include_remote:
            for name in self._access.branches.remote:
                branch_data = self._access.branches.remote[name]
                # Skip symbolic refs like origin/HEAD
                if branch_data.target == "":
                    continue
                result.append(BranchInfo.from_git(branch_data))
        return result
    def tags(self) -> list[TagInfo]:
        """List tags."""
        result: list[TagInfo] = []
        for tag_data in self._access.iter_tags():
            if tag_data.is_annotated:
                tagger = (
                    Signature(
                        name=tag_data.tagger.name,
                        email=tag_data.tagger.email,
                        time=datetime.fromtimestamp(tag_data.tagger.time, tz=datetime.now().astimezone().tzinfo),
                    )
                    if tag_data.tagger
                    else None
                )
                result.append(TagInfo(tag_data.name, tag_data.target_sha, True, tag_data.message, tagger))
            else:
                result.append(TagInfo(tag_data.name, tag_data.target_sha, False))
        return result
    def remotes(self) -> list[RemoteInfo]:
        """List remotes."""
        return [
            RemoteInfo(name, url, push_url)
            for name, url, push_url in self._access.remotes
        ]
    def state(self) -> int:
        """Repository state."""
        return self._access.state()
    def current_branch(self) -> str | None:
        """Current branch name, or None if detached or unborn."""
        return self._access.current_branch_name()
    def default_branch(self) -> str:
        """Resolve the repo's default branch name.
        Checks ``HEAD`` of the main worktree first (works for non-bare
        clones).  Falls back to ``origin/HEAD`` symbolic ref, then to
        common names (``main``, ``master``).  Returns ``"main"`` as a
        last resort.
        """
        import subprocess
        repo = str(self._access.path)
        # 1. In the main worktree HEAD is usually the default branch.
        branch = self._access.current_branch_name()
        if branch:
            return branch
        # 2. origin/HEAD → origin/<default>
        try:
            result = subprocess.run(
                ["git", "-C", repo, "symbolic-ref", "refs/remotes/origin/HEAD"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode == 0:
                ref = result.stdout.strip()  # refs/remotes/origin/main
                return ref.rsplit("/", 1)[-1]
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001
            log.debug("symbolic_ref_failed", exc_info=True)
        # 3. Probe common branch names
        for candidate in ("main", "master"):
            try:
                result = subprocess.run(
                    ["git", "-C", repo, "rev-parse", "--verify", candidate],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                if result.returncode == 0:
                    return candidate
            except (OSError, subprocess.SubprocessError):  # noqa: BLE001
                log.debug("branch_probe_failed", candidate=candidate, exc_info=True)
                continue  # try next candidate branch name
        return "main"
    def tracked_files(self) -> list[str]:
        """List all files tracked in the git index."""
        result = self._access.git.run("ls-files")
        return [line for line in result.stdout.strip().splitlines() if line]
    # Write Operations
    def stage(self, paths: Sequence[str | Path]) -> None:
        """Stage files."""
        status = self._access.status()
        for path in paths:
            p = self._access.normalize_path(path)
            flags = status.get(p, 0)
            if flags & (STATUS_WT_NEW | STATUS_WT_MODIFIED):
                self._access.index.add(p)
            elif flags & STATUS_WT_DELETED:
                self._access.index.remove(p)
    def stage_all(self) -> list[str]:
        """Stage all changed files. Returns list of staged paths."""
        status = self._access.status()
        staged: list[str] = []
        for path, flags in status.items():
            if flags & (STATUS_WT_NEW | STATUS_WT_MODIFIED):
                self._access.index.add(path)
                staged.append(path)
            elif flags & STATUS_WT_DELETED:
                self._access.index.remove(path)
                staged.append(path)
        return staged
    def unstage(self, paths: Sequence[str | Path]) -> None:
        """Unstage files (keeps working tree changes)."""
        if self._access.is_unborn:
            normalized = (self._access.normalize_path(p) for p in paths)
            self._access.best_effort_index_remove(normalized)
            return
        head_tree_sha = self._access.must_head_tree()
        for p in paths:
            self._access.index_reset_entry(self._access.normalize_path(p), head_tree_sha)
    def discard(self, paths: Sequence[str | Path]) -> None:
        """Discard working tree changes, restoring files to index state."""
        for p in paths:
            norm = self._access.normalize_path(p)
            full_path = self._access.path / norm
            if norm in self._access.index:
                # Restore from index using git checkout-index
                self._access.git.run("checkout-index", "--force", "--", norm)
            elif full_path.exists():
                # Untracked file not in index - delete it
                full_path.unlink()
    def commit(self, message: str, allow_empty: bool = False) -> str:
        """Create commit from staged changes. Returns commit sha."""
        check_nothing_to_commit(self._access, allow_empty)
        return self._flows.commit_from_index(message)
    def amend(self, message: str | None = None) -> str:
        """Amend the most recent commit. Returns commit sha."""
        require_not_unborn(self._access, "amend")
        head_commit = self._access.must_head_commit()
        tree_sha = self._access.index.write_tree()
        new_message = message if message is not None else head_commit.message
        # Create orphan commit with same parents, then soft-reset HEAD to it
        oid = self._access.create_commit(
            None,
            head_commit.author,
            self._access.default_signature,
            new_message,
            tree_sha,
            list(head_commit.parent_shas),
        )
        self._access.reset(oid, RESET_SOFT)
        return oid
    def create_branch(self, name: str, ref: str = "HEAD") -> BranchInfo:
        """Create branch."""
        if self._access.has_local_branch(name):
            raise BranchExistsError(name)
        target_sha = self._access.resolve_ref_oid(ref)
        branch_data = self._access.create_local_branch(name, target_sha)
        return BranchInfo.from_git(branch_data)
    def checkout(self, ref: str, create: bool = False) -> None:
        """Checkout branch or ref."""
        if create:
            self.create_branch(ref)
        plan = self._checkout_planner.plan(ref)
        self._checkout_planner.execute(plan)
    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete branch."""
        require_branch_exists(self._access, name)
        require_not_current_branch(self._access, name)
        branch_sha = self._access.resolve_ref_oid(f"refs/heads/{name}")
        if not force and not self._access.descendant_of(self._head_sha(), branch_sha):
            raise UnmergedBranchError(name)
        self._access.delete_branch(name)
    def rename_branch(self, old_name: str, new_name: str) -> BranchInfo:
        """Rename a branch."""
        require_branch_exists(self._access, old_name)
        if self._access.has_local_branch(new_name):
            raise BranchExistsError(new_name)
        self._access.git.run("branch", "-m", old_name, new_name)
        branch_data = self._access.must_local_branch(new_name)
        return BranchInfo.from_git(branch_data)
    def reset(self, ref: str, mode: str = "mixed") -> None:
        """Reset HEAD. mode: 'soft', 'mixed', or 'hard'."""
        modes = {"soft": RESET_SOFT, "mixed": RESET_MIXED, "hard": RESET_HARD}
        if mode not in modes:
            raise ValueError(
                f"Invalid reset mode {mode!r}. Expected one of: {', '.join(sorted(modes))}"
            )
        sha = self._access.resolve_ref_oid(ref)
        self._access.reset(sha, modes[mode])
    def merge(self, ref: str) -> MergeResult:
        """Merge ref. Returns MergeResult with success, commit_sha, conflict_paths."""
        their_sha = self._access.resolve_ref_oid(ref)
        analysis = self._access.merge_analysis(their_sha)
        if analysis & MERGE_UP_TO_DATE:
            return MergeResult(True, None)
        if analysis & MERGE_FASTFORWARD:
            current = self._access.current_branch_name()
            self._access.checkout_detached(their_sha)
            if current:
                self._access.set_branch_target(current, their_sha)
                self._access.set_head(f"refs/heads/{current}")
            return MergeResult(True, their_sha)
        # Non-fastforward merge
        head_sha = self._head_sha()
        with self._flows.stateful_op():
            self._access.merge(their_sha)
            conflicts = self._flows.check_conflicts()
            if conflicts.has_conflicts:
                return MergeResult(False, None, conflicts.conflict_paths)
            sha = self._flows.write_tree_and_commit(f"Merge {ref}", [head_sha, their_sha])
            return MergeResult(True, sha)
    def abort_merge(self) -> None:
        """Abort in-progress merge."""
        self._access.state_cleanup()
        self._access.reset(self._head_sha(), RESET_HARD)
    def merge_analysis(self, ref: str) -> MergeAnalysis:
        """Analyze potential merge."""
        their_sha = self._access.resolve_ref_oid(ref)
        analysis = self._access.merge_analysis(their_sha)
        return MergeAnalysis(
            up_to_date=bool(analysis & MERGE_UP_TO_DATE),
            fastforward_possible=bool(analysis & MERGE_FASTFORWARD),
            conflicts_likely=bool(analysis & MERGE_NORMAL),
        )
    def cherrypick(self, ref: str) -> OperationResult:
        """Cherry-pick a commit."""
        commit = self._access.resolve_commit(ref)
        head_sha = self._head_sha()
        with self._flows.stateful_op():
            self._access.cherrypick(commit.sha)
            conflicts = self._flows.check_conflicts()
            if conflicts.has_conflicts:
                return OperationResult(False, conflicts.conflict_paths)
            self._flows.write_tree_and_commit(commit.message, [head_sha], author=commit.author)
            return OperationResult(True)
    def revert(self, ref: str) -> OperationResult:
        """Revert a commit."""
        commit = self._access.resolve_commit(ref)
        head_sha = self._head_sha()
        with self._flows.stateful_op():
            self._access.revert_commit(commit.sha)
            conflicts = self._flows.check_conflicts()
            if conflicts.has_conflicts:
                return OperationResult(False, conflicts.conflict_paths)
            message = f'Revert "{first_line(commit.message)}"'
            self._flows.write_tree_and_commit(message, [head_sha])
            return OperationResult(True)
    def stash_push(self, message: str | None = None, include_untracked: bool = False) -> str:
        """Stash changes. Returns stash commit sha."""
        return self._access.stash(message, include_untracked=include_untracked)
    def stash_pop(self, index: int = 0) -> None:
        """Pop stash entry."""
        stashes = self._access.listall_stashes()
        if index >= len(stashes):
            raise StashNotFoundError(index)
        self._access.stash_apply(index)
        self._access.stash_drop(index)
    def stash_list(self) -> list[StashEntry]:
        """List stash entries."""
        return [
            StashEntry(i, s.message, s.commit_id)
            for i, s in enumerate(self._access.listall_stashes())
        ]
    def create_tag(self, name: str, ref: str = "HEAD", message: str | None = None) -> str:
        """Create tag. Returns target sha."""
        target_sha = self._access.resolve_ref_oid(ref)
        if message:
            self._access.create_tag(name, target_sha, message)
            return target_sha
        self._access.create_reference(make_tag_ref(name), target_sha)
        return target_sha
    def delete_tag(self, name: str) -> None:
        """Delete tag."""
        ref = make_tag_ref(name)
        if not self._access.has_reference(ref):
            raise RefNotFoundError(name)
        self._access.delete_reference(ref)
    def fetch(self, remote: str = "origin", callbacks: object | None = None) -> None:
        """Fetch from remote with retry for transient network failures."""
        self._fetch_with_retry(remote)
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _fetch_with_retry(self, remote: str) -> None:
        """Internal fetch with retry decorator."""
        self._access.run_remote_operation(
            remote, "fetch", ["fetch", remote], timeout=300
        )
    def push(
        self,
        remote: str = "origin",
        force: bool = False,
        callbacks: object | None = None,
    ) -> None:
        """Push current branch to remote with retry."""
        branch = require_current_branch(self._access, "push")
        self._push_with_retry(remote, branch, force)
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _push_with_retry(self, remote: str, branch: str, force: bool) -> None:
        """Internal push with retry decorator."""
        cmd = ["push", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
        if force:
            cmd.insert(1, "--force")
        self._access.run_remote_operation(remote, "push", cmd, timeout=300)
    def pull(
        self,
        remote: str = "origin",
        branch: str | None = None,
        callbacks: object | None = None,
    ) -> PullResult:
        """Fetch from remote and merge into current branch."""
        self.fetch(remote)
        current = require_current_branch(self._access, "pull")
        merge_branch = branch or current
        remote_ref = f"{remote}/{merge_branch}"
        if not self._access.has_reference(f"refs/remotes/{remote_ref}"):
            raise RefNotFoundError(remote_ref)
        analysis = self.merge_analysis(remote_ref)
        if analysis.up_to_date:
            return PullResult(success=True, commit_sha=None, up_to_date=True)
        result = self.merge(remote_ref)
        return PullResult(
            success=result.success,
            commit_sha=result.commit_sha,
            up_to_date=False,
            conflict_paths=result.conflict_paths,
        )
    # Worktree Operations
    def worktrees(self) -> list[WorktreeInfo]:
        """List all worktrees including main working directory."""
        result: list[WorktreeInfo] = []
        # Parse git worktree list --porcelain for complete info
        wt_output = self._access.git.run("worktree", "list", "--porcelain")
        entries: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in wt_output.stdout.splitlines():
            if not line:
                if current:
                    entries.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line[len("worktree "):]
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
            elif line == "bare":
                current["bare"] = "true"
            elif line == "detached":
                current["detached"] = "true"
        if current:
            entries.append(current)
        for i, entry in enumerate(entries):
            is_main = i == 0
            wt_path = entry.get("path", "")
            head_sha = entry.get("head", "")
            branch_ref = entry.get("branch", "")
            is_detached = "detached" in entry
            is_bare = "bare" in entry
            if branch_ref:
                head_ref_name = branch_ref.removeprefix("refs/heads/")
            elif is_detached:
                head_ref_name = "HEAD"
            else:
                head_ref_name = ""
            name = "main" if is_main else Path(wt_path).name
            # Check lock status
            is_locked = False
            lock_reason = None
            if not is_main and name:
                lock_file = self._access.worktree_gitdir(name) / "locked"
                if lock_file.exists():
                    is_locked = True
                    lock_reason = lock_file.read_text().strip() or None
            is_prunable = not is_main and wt_path and not Path(wt_path).exists()
            result.append(
                WorktreeInfo(
                    name=name,
                    path=wt_path,
                    head_ref=head_ref_name,
                    head_sha=head_sha,
                    is_main=is_main,
                    is_bare=is_bare,
                    is_locked=is_locked,
                    lock_reason=lock_reason,
                    is_prunable=is_prunable,
                )
            )
        return result
    def worktree_add(self, path: Path, ref: str, checkout: bool = True) -> GitOps:  # noqa: ARG002
        """Add worktree at path for ref. Returns GitOps for new worktree."""
        if not self._access.has_local_branch(ref):
            raise BranchNotFoundError(ref)
        if path.exists():
            raise WorktreeError(f"Path already exists: {path}")
        name = path.name
        existing_names = self._access.list_worktrees()
        if name in existing_names:
            raise WorktreeExistsError(name)
        for wt_name in existing_names:
            try:
                wt_path = self._access.worktree_path(wt_name)
                if Path(wt_path).resolve() == path.resolve():
                    raise WorktreeError(f"Path already in use by worktree '{wt_name}'")
            except GitError:
                log.debug("worktree_path_check_failed", worktree=wt_name, exc_info=True)
        self._access.add_worktree(name, str(path), ref)
        return GitOps(path)
    def worktree_open(self, name: str) -> GitOps:
        """Get GitOps instance for existing worktree by name."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        wt_path = self._access.worktree_path(name)
        return GitOps(wt_path)
    def worktree_remove(self, name: str, force: bool = False) -> None:
        """Remove worktree."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        lock_file = self._access.worktree_gitdir(name) / "locked"
        if lock_file.exists() and not force:
            raise WorktreeLockedError(name)
        try:
            self._access.remove_worktree(name, force)
        except (OSError, subprocess.SubprocessError) as e:
            raise WorktreeError(f"Failed to remove worktree '{name}': {e}") from e
    def worktree_lock(self, name: str, reason: str | None = None) -> None:
        """Lock worktree to prevent pruning."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        gitdir = self._access.worktree_gitdir(name)
        if not gitdir.exists():
            raise WorktreeError(f"Invalid worktree gitdir (missing): {gitdir}")
        lock_file = gitdir / "locked"
        if lock_file.exists():
            raise WorktreeLockedError(name)
        try:
            atomic_write_text(lock_file, reason or "")
        except OSError as e:
            raise WorktreeError(f"Failed to lock worktree {name}: {e}") from e
    def worktree_unlock(self, name: str) -> None:
        """Unlock worktree."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        lock_file = self._access.worktree_gitdir(name) / "locked"
        if lock_file.exists():
            lock_file.unlink()
    def worktree_prune(self) -> list[str]:
        """Remove stale worktree entries. Returns pruned names."""
        pruned = []
        for name in self._access.list_worktrees():
            if self._access.worktree_is_prunable(name):
                self._access.git.run("worktree", "prune")
                pruned.append(name)
                break  # Prune removes all stale at once
        return pruned
    def is_worktree(self) -> bool:
        """True if this GitOps is for a worktree (not main working directory)."""
        return self._access.is_worktree()
    def worktree_info(self) -> WorktreeInfo | None:
        """Get info about this worktree, or None if main working directory."""
        if not self.is_worktree():
            return None
        ref = self._access.head_ref
        return WorktreeInfo(
            name=self._access.path.name,
            path=str(self._access.path),
            head_ref=ref.shorthand if not self._access.is_detached else "HEAD",
            head_sha=self._access.head_target if not self._access.is_unborn else "",
            is_main=False,
            is_bare=False,
            is_locked=False,
            lock_reason=None,
            is_prunable=False,
        )
    # Submodule Operations
    def submodules(self) -> list[SubmoduleInfo]:
        """List all submodules with status."""
        result = []
        for name in self._access.listall_submodules():
            try:
                sm = self._access.lookup_submodule(name)
                status = self._determine_submodule_status(sm)
                result.append(
                    SubmoduleInfo(
                        name=sm["name"],
                        path=sm["path"],
                        url=sm.get("url", ""),
                        branch=sm.get("branch"),
                        head_sha=sm.get("head_id"),
                        status=status,
                    )
                )
            except GitError:
                result.append(
                    SubmoduleInfo(
                        name=name,
                        path=name,
                        url="",
                        branch=None,
                        head_sha=None,
                        status="missing",
                    )
                )
        return result
    def _determine_submodule_status(self, sm: dict) -> SubmoduleState:
        """Determine submodule status."""
        sm_path = self._access.path / sm["path"]
        if not sm_path.exists():
            return "uninitialized"
        if not (sm_path / ".git").exists():
            return "uninitialized"
        try:
            # Check status using git -C
            result = subprocess.run(
                ["git", "-C", str(sm_path), "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                return "dirty"
            # Check if at recorded commit
            head_result = subprocess.run(
                ["git", "-C", str(sm_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            actual_sha = head_result.stdout.strip()
            recorded_sha = sm.get("head_id")
            if recorded_sha and actual_sha != recorded_sha:
                return "outdated"
            return "clean"
        except (subprocess.SubprocessError, OSError):
            return "missing"
    def submodule_status(self, path: str) -> SubmoduleStatus:
        """Detailed status for one submodule."""
        try:
            sm = self._access.lookup_submodule_by_path(path)
        except GitError:
            raise SubmoduleNotFoundError(path) from None
        status = self._determine_submodule_status(sm)
        info = SubmoduleInfo(
            name=sm["name"],
            path=sm["path"],
            url=sm.get("url", ""),
            branch=sm.get("branch"),
            head_sha=sm.get("head_id"),
            status=status,
        )
        sm_path = self._access.path / path
        workdir_dirty = False
        index_dirty = False
        untracked_count = 0
        actual_sha = None
        if sm_path.exists():
            try:
                head_result = subprocess.run(
                    ["git", "-C", str(sm_path), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=10,
                )
                if head_result.returncode == 0:
                    actual_sha = head_result.stdout.strip()
                status_result = subprocess.run(
                    ["git", "-C", str(sm_path), "status", "--porcelain=v1"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in status_result.stdout.splitlines():
                    if len(line) < 4:
                        continue
                    x, y = line[0], line[1]
                    if y in ("M", "D"):
                        workdir_dirty = True
                    if x in ("A", "M", "D"):
                        index_dirty = True
                    if x == "?" and y == "?":
                        untracked_count += 1
            except (subprocess.SubprocessError, OSError):
                log.debug("submodule_status_check_failed", exc_info=True)
        return SubmoduleStatus(
            info=info,
            workdir_dirty=workdir_dirty,
            index_dirty=index_dirty,
            untracked_count=untracked_count,
            recorded_sha=sm.get("head_id", ""),
            actual_sha=actual_sha,
        )
    def submodule_init(self, paths: Sequence[str] | None = None) -> list[str]:
        """Initialize submodules."""
        initialized = []
        if paths is None:
            for name in self._access.listall_submodules():
                try:
                    self._access.init_submodule(name)
                    sm = self._access.lookup_submodule(name)
                    initialized.append(sm["path"])
                except GitError:
                    log.debug("submodule_init_failed", submodule=name, exc_info=True)
        else:
            for path in paths:
                sm_name = self._access.submodule_name_for_path(path)
                if sm_name is None:
                    raise SubmoduleNotFoundError(path)
                try:
                    self._access.init_submodule(sm_name)
                    initialized.append(path)
                except GitError:
                    log.debug("submodule_init_failed", path=path, exc_info=True)
        return initialized
    def submodule_update(
        self,
        paths: Sequence[str] | None = None,
        recursive: bool = False,
        init: bool = True,
    ) -> SubmoduleUpdateResult:
        """Update submodules to recorded commits."""
        cmd = ["git", "submodule", "update"]
        if init:
            cmd.append("--init")
        if recursive:
            cmd.append("--recursive")
        if paths:
            cmd.append("--")
            cmd.extend(paths)
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self._access.path),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                updated = []
                for line in result.stdout.splitlines():
                    if line.startswith("Submodule path"):
                        parts = line.split("'")
                        if len(parts) >= 2:
                            updated.append(parts[1])
                return SubmoduleUpdateResult(
                    updated=tuple(updated),
                    failed=(),
                    already_current=(),
                )
            else:
                return SubmoduleUpdateResult(
                    updated=(),
                    failed=(("*", result.stderr.strip()),),
                    already_current=(),
                )
        except subprocess.TimeoutExpired:
            return SubmoduleUpdateResult(
                updated=(),
                failed=(("*", "Operation timed out"),),
                already_current=(),
            )
    def submodule_sync(self, paths: Sequence[str] | None = None) -> None:
        """Sync submodule URLs from .gitmodules to .git/config."""
        cmd = ["git", "submodule", "sync"]
        if paths:
            cmd.append("--")
            cmd.extend(paths)
        try:
            subprocess.run(
                cmd,
                cwd=str(self._access.path),
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            raise SubmoduleError(f"Failed to sync submodules: {stderr.strip()}") from exc
    def submodule_add(self, url: str, path: str, branch: str | None = None) -> SubmoduleInfo:
        """Add new submodule."""
        cmd = ["git", "submodule", "add"]
        if branch:
            cmd.extend(["-b", branch])
        cmd.extend([url, path])
        result = subprocess.run(
            cmd,
            cwd=str(self._access.path),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise SubmoduleError(f"Failed to add submodule: {result.stderr.strip()}")
        sm = self._access.lookup_submodule_by_path(path)
        return SubmoduleInfo(
            name=sm["name"],
            path=sm["path"],
            url=sm.get("url", url),
            branch=branch,
            head_sha=sm.get("head_id"),
            status="clean",
        )
    def submodule_deinit(self, path: str, force: bool = False) -> None:
        """Deinitialize submodule."""
        cmd = ["git", "submodule", "deinit"]
        if force:
            cmd.append("--force")
        cmd.append(path)
        result = subprocess.run(
            cmd,
            cwd=str(self._access.path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise SubmoduleError(f"Failed to deinit submodule: {result.stderr.strip()}")
    def submodule_remove(self, path: str) -> None:
        """Fully remove submodule."""
        import shutil
        name = self._access.submodule_name_for_path(path)
        if name is None:
            raise SubmoduleNotFoundError(path)
        self.submodule_deinit(path, force=True)
        result = subprocess.run(
            ["git", "config", "--file", ".gitmodules", "--remove-section", f"submodule.{name}"],
            cwd=str(self._access.path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode not in (0, 128):
            raise SubmoduleError(
                f"Failed to remove submodule from .gitmodules: {result.stderr.strip()}"
            )
        gitmodules_path = self._access.path / ".gitmodules"
        if gitmodules_path.exists():
            self._access.index.add(".gitmodules")
        result = subprocess.run(
            ["git", "config", "--remove-section", f"submodule.{name}"],
            cwd=str(self._access.path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode not in (0, 128):
            raise SubmoduleError(
                f"Failed to remove submodule from .git/config: {result.stderr.strip()}"
            )
        # Remove from index
        self._access.git.run_raw("rm", "--cached", "--ignore-unmatch", "--", path)
        sm_path = self._access.path / path
        if sm_path.exists():
            shutil.rmtree(sm_path)
        modules_path = self._access.git_dir / "modules" / name
        if modules_path.exists():
            shutil.rmtree(modules_path)
    # Rebase Operations
    def rebase_plan(self, upstream: str, onto: str | None = None) -> RebasePlan:
        """Generate default rebase plan."""
        return self._rebase_planner.plan(upstream, onto)
    def rebase_execute(self, plan: RebasePlan) -> RebaseResult:
        """Execute rebase plan."""
        return self._rebase_flow.execute(plan)
    def rebase_continue(self) -> RebaseResult:
        """Resume after conflict resolution or edit completion."""
        return self._rebase_flow.continue_rebase()
    def rebase_abort(self) -> None:
        """Abort and restore original state."""
        self._rebase_flow.abort()
    def rebase_skip(self) -> RebaseResult:
        """Skip current commit and continue."""
        return self._rebase_flow.skip()
    def rebase_in_progress(self) -> bool:
        """Check if a rebase is in progress."""
        return self._rebase_flow.has_rebase_in_progress()
