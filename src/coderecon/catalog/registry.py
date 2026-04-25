"""Catalog registry — register, unregister, and look up repos + worktrees."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import structlog
from sqlmodel import select

log = structlog.get_logger(__name__)

from coderecon.catalog.db import CatalogDB
from coderecon.catalog.models import RepoEntry, WorktreeEntry


def _repo_hash(git_dir: str) -> str:
    """Deterministic short hash for a git dir path (for storage dir names)."""
    return hashlib.sha256(git_dir.encode()).hexdigest()[:12]


def _resolve_git_dir(repo_root: Path) -> str:
    """Resolve the canonical .git directory for a path.

    Handles both normal repos (.git is a directory) and worktrees
    (.git is a file pointing to the real git dir).
    """
    git_path = repo_root / ".git"

    if git_path.is_file():
        # Worktree: .git is a file containing "gitdir: <path>"
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir = content[8:]
            # Resolve relative paths
            resolved = (repo_root / gitdir).resolve()
            # Walk up to the main .git dir (worktree gitdirs are inside .git/worktrees/<name>/)
            # The canonical git_dir is the parent of worktrees/
            if "worktrees" in resolved.parts:
                idx = resolved.parts.index("worktrees")
                return str(Path(*resolved.parts[:idx]))
            return str(resolved)

    if git_path.is_dir():
        return str(git_path.resolve())

    # Fallback: use git rev-parse
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            common = result.stdout.strip()
            return str((repo_root / common).resolve())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.debug("git_resolve_failed", exc_info=True)

    msg = f"Cannot resolve git directory for {repo_root}"
    raise ValueError(msg)


def _detect_worktree_name(repo_root: Path, git_dir: str) -> tuple[str, bool]:
    """Detect worktree name and whether this is the main checkout.

    Returns (name, is_main).
    """
    git_path = repo_root / ".git"

    if git_path.is_dir():
        # Main checkout
        return "main", True

    if git_path.is_file():
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir_path = Path(content[8:])
            # Worktree gitdirs look like: ../.git/worktrees/<name>/
            if "worktrees" in gitdir_path.parts:
                idx = gitdir_path.parts.index("worktrees")
                if idx + 1 < len(gitdir_path.parts):
                    return gitdir_path.parts[idx + 1], False

    return repo_root.name, True


def _get_current_branch(repo_root: Path) -> str | None:
    """Get current branch name for a worktree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch != "HEAD" else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.debug("git_branch_detect_failed", exc_info=True)
    return None


class CatalogRegistry:
    """High-level operations on the repo/worktree catalog."""

    def __init__(self, catalog: CatalogDB) -> None:
        self.catalog = catalog
        self.catalog.create_all()

    def register(self, repo_root: Path) -> tuple[RepoEntry, WorktreeEntry]:
        """Register a repo (and its worktree) in the catalog.

        If the repo already exists, just ensures the worktree entry exists.
        Returns (repo, worktree).
        """
        repo_root = repo_root.resolve()
        git_dir = _resolve_git_dir(repo_root)
        wt_name, is_main = _detect_worktree_name(repo_root, git_dir)
        branch = _get_current_branch(repo_root)

        with self.catalog.session() as session:
            # Find or create repo
            repo = session.exec(
                select(RepoEntry).where(RepoEntry.git_dir == git_dir)
            ).first()

            if repo is None:
                storage_hash = _repo_hash(git_dir)
                storage_dir = str(self.catalog.repos_dir / storage_hash)
                Path(storage_dir).mkdir(parents=True, exist_ok=True)

                # Resolve name collision: if another repo already uses this
                # directory name as its slug, append a short hash suffix.
                desired_name = repo_root.name
                name_taken = session.exec(
                    select(RepoEntry).where(RepoEntry.name == desired_name)
                ).first()
                if name_taken is not None:
                    desired_name = f"{repo_root.name}-{storage_hash[:7]}"

                repo = RepoEntry(
                    name=desired_name,
                    git_dir=git_dir,
                    storage_dir=storage_dir,
                    default_branch=branch if is_main else None,
                )
                session.add(repo)
                session.flush()

            # Find or create worktree
            wt = session.exec(
                select(WorktreeEntry).where(
                    WorktreeEntry.root_path == str(repo_root)
                )
            ).first()

            if wt is None:
                wt = WorktreeEntry(
                    repo_id=repo.id,  # type: ignore[arg-type]
                    name=wt_name,
                    root_path=str(repo_root),
                    branch=branch,
                    is_main=is_main,
                )
                session.add(wt)

            session.commit()
            session.refresh(repo)
            session.refresh(wt)

            return repo, wt

    def unregister(self, repo_root: Path) -> bool:
        """Remove a worktree from the catalog. If last worktree, remove repo too.

        Returns True if something was removed.
        """
        repo_root = repo_root.resolve()

        with self.catalog.session() as session:
            wt = session.exec(
                select(WorktreeEntry).where(
                    WorktreeEntry.root_path == str(repo_root)
                )
            ).first()

            if wt is None:
                return False

            repo_id = wt.repo_id
            session.delete(wt)

            # Check if this was the last worktree for this repo
            remaining = session.exec(
                select(WorktreeEntry).where(
                    WorktreeEntry.repo_id == repo_id
                )
            ).all()

            if not remaining:
                repo = session.get(RepoEntry, repo_id)
                if repo:
                    session.delete(repo)

            session.commit()
            return True

    def lookup_by_path(self, path: Path) -> tuple[RepoEntry, WorktreeEntry] | None:
        """Find repo + worktree for a filesystem path.

        Walks up from path to find a registered worktree root.
        """
        path = path.resolve()
        current = path

        with self.catalog.session() as session:
            while current != current.parent:
                wt = session.exec(
                    select(WorktreeEntry).where(
                        WorktreeEntry.root_path == str(current)
                    )
                ).first()

                if wt is not None:
                    repo = session.get(RepoEntry, wt.repo_id)
                    if repo:
                        return repo, wt

                current = current.parent

        return None

    def lookup_by_name(self, name: str) -> tuple[RepoEntry, WorktreeEntry] | None:
        """Find repo + main worktree by repo name."""
        with self.catalog.session() as session:
            repo = session.exec(
                select(RepoEntry).where(RepoEntry.name == name)
            ).first()

            if repo is None:
                return None

            # Prefer main worktree
            wt = session.exec(
                select(WorktreeEntry).where(
                    WorktreeEntry.repo_id == repo.id,
                    WorktreeEntry.is_main == True,  # noqa: E712
                )
            ).first()

            if wt is None:
                # Fall back to any worktree
                wt = session.exec(
                    select(WorktreeEntry).where(
                        WorktreeEntry.repo_id == repo.id
                    )
                ).first()

            if wt is None:
                return None

            return repo, wt

    def list_repos(self) -> list[RepoEntry]:
        """List all registered repositories."""
        with self.catalog.session() as session:
            return list(session.exec(select(RepoEntry)).all())

    def list_worktrees(self, repo_id: int) -> list[WorktreeEntry]:
        """List all worktrees for a repository."""
        with self.catalog.session() as session:
            return list(
                session.exec(
                    select(WorktreeEntry).where(
                        WorktreeEntry.repo_id == repo_id
                    )
                ).all()
            )

    def lookup_worktree(self, repo_id: int, name: str) -> WorktreeEntry | None:
        """Look up a single worktree by repo_id and name."""
        with self.catalog.session() as session:
            return session.exec(
                select(WorktreeEntry).where(
                    WorktreeEntry.repo_id == repo_id,
                    WorktreeEntry.name == name,
                )
            ).first()

    def get_storage_dir(self, repo: RepoEntry) -> Path:
        """Get the per-repo storage directory."""
        return Path(repo.storage_dir)

    def get_repo_name_for_path(self, path: Path) -> str | None:
        """Return the repo slug for a registered worktree path, or None."""
        result = self.lookup_by_path(path)
        return result[0].name if result else None

    def update_last_indexed_at(self, wt_root: Path, ts: float) -> None:
        """Write *ts* into WorktreeEntry.last_indexed_at for the given worktree path."""
        wt_root = wt_root.resolve()
        with self.catalog.session() as session:
            wt = session.exec(
                select(WorktreeEntry).where(
                    WorktreeEntry.root_path == str(wt_root)
                )
            ).first()
            if wt is None:
                return
            wt.last_indexed_at = ts
            session.add(wt)
            session.commit()

    def discover_worktrees(self, repo_root: Path) -> list[Path]:
        """Discover all git worktrees for a repository.

        Uses `git worktree list` to find all worktree paths.
        """
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return [repo_root]

            paths: list[Path] = []
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    paths.append(Path(line[9:]))

            return paths or [repo_root]

        except (subprocess.TimeoutExpired, FileNotFoundError):
            log.debug("git_worktree_list_failed", exc_info=True)
            return [repo_root]
