"""Worktree and submodule mixin for RepoAccess."""
from __future__ import annotations

import re
from pathlib import Path

import structlog

from coderecon.git.errors import GitError

log = structlog.get_logger(__name__)


class _WorktreeMixin:
    """Mixin providing worktree and submodule operations."""

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
    # Worktree Operations
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
    # Submodule Operations
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
    def lookup_submodule(self, name: str) -> dict[str, str | None]:
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
                structlog.get_logger().debug("submodule_lookup_failed", name=name, exc_info=True)
                continue
        return None
    def lookup_submodule_by_path(self, path: str) -> dict[str, str | None]:
        """Get submodule by path."""
        name = self.submodule_name_for_path(path)
        if name is None:
            raise GitError(f"Submodule not found at path: {path}")
        return self.lookup_submodule(name)
    # Merge Base
    def merge_base(self, sha1: str, sha2: str) -> str | None:
        """Find merge base of two commits. Returns None if unrelated."""
        rc, stdout, _ = self._git.run_raw("merge-base", sha1, sha2)
        if rc != 0:
            return None
        return stdout.strip()
    # Diff Parsing
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
