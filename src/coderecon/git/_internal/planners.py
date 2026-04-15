"""Decision planners that separate "what to do" from "how to do it"."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from coderecon.git._internal.access import RepoAccess
from coderecon.git.errors import GitError, RefNotFoundError


class DiffType(Enum):
    """Types of diff operations."""

    WORKING_TREE = auto()
    STAGED_UNBORN = auto()
    STAGED_NORMAL = auto()
    REF_TO_WORKING = auto()
    REF_TO_REF = auto()


@dataclass(frozen=True, slots=True)
class DiffPlan:
    """Plan for executing a diff operation."""

    diff_type: DiffType
    base_sha: str | None = None
    target_sha: str | None = None


@dataclass(frozen=True, slots=True)
class DiffResult:
    """Result of a diff execution - raw diff text and parsed stats."""

    diff_text: str
    numstat: list[tuple[str, int, int, str]]  # (status, adds, dels, path)


class DiffPlanner:
    """Plans and executes diff operations."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def plan(
        self,
        base: str | None,
        target: str | None,
        staged: bool,
    ) -> DiffPlan:
        """Determine the type of diff to perform and resolve refs upfront."""
        if staged:
            if self._access.is_unborn:
                return DiffPlan(DiffType.STAGED_UNBORN)
            return DiffPlan(DiffType.STAGED_NORMAL)

        if base is None and target is None:
            return DiffPlan(DiffType.WORKING_TREE)

        # Resolve refs at plan time - fail early with clear errors
        if base is None and self._access.is_unborn:
            raise RefNotFoundError("HEAD (unborn)")

        base_sha = (
            self._access.resolve_commit(base).sha if base else self._access.must_head_commit().sha
        )

        if target is None:
            return DiffPlan(DiffType.REF_TO_WORKING, base_sha=base_sha)

        target_sha = self._access.resolve_commit(target).sha
        return DiffPlan(DiffType.REF_TO_REF, base_sha=base_sha, target_sha=target_sha)

    def execute(self, plan: DiffPlan) -> DiffResult:
        """Execute a diff plan. Returns DiffResult with raw text and stats."""
        if plan.diff_type == DiffType.WORKING_TREE:
            diff_text = self._access.diff_working_tree()
            numstat = self._access.diff_numstat()
            return DiffResult(diff_text, numstat)

        if plan.diff_type == DiffType.STAGED_UNBORN:
            diff_text = self._access.diff_staged()
            numstat = self._access.diff_numstat("--cached")
            return DiffResult(diff_text, numstat)

        if plan.diff_type == DiffType.STAGED_NORMAL:
            diff_text = self._access.diff_staged()
            numstat = self._access.diff_numstat("--cached")
            return DiffResult(diff_text, numstat)

        if plan.diff_type == DiffType.REF_TO_WORKING:
            diff_text = self._access.diff_refs(plan.base_sha)  # type: ignore[arg-type]
            numstat = self._access.diff_numstat(plan.base_sha)  # type: ignore[arg-type]
            return DiffResult(diff_text, numstat)

        # REF_TO_REF
        diff_text = self._access.diff_refs(plan.base_sha, plan.target_sha)  # type: ignore[arg-type]
        numstat = self._access.diff_numstat(plan.base_sha, plan.target_sha)  # type: ignore[arg-type]
        return DiffResult(diff_text, numstat)


class CheckoutType(Enum):
    """Types of checkout operations."""

    LOCAL_BRANCH = auto()
    REMOTE_BRANCH_NEW_LOCAL = auto()
    REMOTE_BRANCH_EXISTING_LOCAL = auto()
    DETACHED = auto()


@dataclass(frozen=True, slots=True)
class CheckoutPlan:
    """Plan for executing a checkout operation."""

    checkout_type: CheckoutType
    ref: str
    local_name: str | None = None


class CheckoutPlanner:
    """Plans and executes checkout operations."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def plan(self, ref: str) -> CheckoutPlan:
        """Determine the type of checkout to perform."""
        if self._access.has_local_branch(ref):
            return CheckoutPlan(CheckoutType.LOCAL_BRANCH, ref)

        if self._access.has_remote_branch(ref):
            local_name = ref.split("/", 1)[-1]
            if self._access.has_local_branch(local_name):
                return CheckoutPlan(CheckoutType.REMOTE_BRANCH_EXISTING_LOCAL, ref, local_name)
            return CheckoutPlan(CheckoutType.REMOTE_BRANCH_NEW_LOCAL, ref, local_name)

        return CheckoutPlan(CheckoutType.DETACHED, ref)

    def execute(self, plan: CheckoutPlan) -> None:
        """Execute a checkout plan."""
        if plan.checkout_type == CheckoutType.LOCAL_BRANCH:
            self._access.checkout_branch(plan.ref)

        elif plan.checkout_type == CheckoutType.REMOTE_BRANCH_NEW_LOCAL:
            # Create local branch tracking remote and checkout
            remote_sha = self._access.resolve_ref_oid(f"refs/remotes/{plan.ref}")
            assert plan.local_name is not None
            self._access.create_local_branch(plan.local_name, remote_sha)
            self._access.checkout_branch(plan.local_name)

        elif plan.checkout_type == CheckoutType.REMOTE_BRANCH_EXISTING_LOCAL:
            assert plan.local_name is not None
            self._access.checkout_branch(plan.local_name)

        else:  # DETACHED
            sha = self._access.resolve_ref_oid(plan.ref)
            self._access.checkout_detached(sha)
