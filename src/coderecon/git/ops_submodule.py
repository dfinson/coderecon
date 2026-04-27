"""Submodule operations mixin for GitOps."""
from __future__ import annotations

import subprocess
from collections.abc import Sequence

import structlog

from coderecon.git.errors import (
    GitError,
    SubmoduleError,
    SubmoduleNotFoundError,
)
from coderecon.git.models import (
    SubmoduleInfo,
    SubmoduleState,
    SubmoduleStatus,
    SubmoduleUpdateResult,
)

log = structlog.get_logger(__name__)


class _SubmoduleMixin:
    """Mixin providing submodule operations for GitOps."""

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
