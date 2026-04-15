"""Interactive rebase implementation using subprocess git operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from coderecon.git._internal.constants import RESET_HARD
from coderecon.git.errors import (
    GitError,
    NoRebaseInProgressError,
    RebaseConflictError,
    RebaseError,
    RebaseInProgressError,
    RefNotFoundError,
)
from coderecon.git.models import RebasePlan, RebaseResult, RebaseStep

if TYPE_CHECKING:
    from coderecon.git._internal.access import RepoAccess


# Rebase state file stored in .git/
REBASE_STATE_FILE = "coderecon-rebase-state.json"


@dataclass
class RebaseState:
    """Persisted rebase state for recovery."""

    original_head: str
    original_branch: str | None
    onto: str
    steps: list[dict[str, str | None]]
    current_step: int
    completed_commits: list[str]


class RebasePlanner:
    """Generates rebase plans from commit ranges."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def plan(self, upstream: str, onto: str | None = None) -> RebasePlan:
        """Generate a rebase plan."""
        head_sha = self._access.must_head_target()
        upstream_sha = self._access.resolve_ref_oid(upstream)
        onto_ref = onto or upstream

        # Find commits to rebase: upstream..HEAD (topological order, oldest first)
        commits = self._access.walk_commits_excluding(head_sha, upstream_sha)

        steps = tuple(
            RebaseStep(action="pick", commit_sha=c.sha, message=c.message) for c in commits
        )

        return RebasePlan(upstream=upstream, onto=onto_ref, steps=steps)


class RebaseFlow:
    """Executes rebase plans with state persistence."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    @property
    def _state_path(self) -> Path:
        return self._access.git_dir / REBASE_STATE_FILE

    def has_rebase_in_progress(self) -> bool:
        return self._state_path.exists()

    def execute(self, plan: RebasePlan) -> RebaseResult:
        """Execute a rebase plan."""
        if self.has_rebase_in_progress():
            raise RebaseInProgressError()

        original_head = self._access.must_head_target()
        original_branch = self._access.current_branch_name()

        try:
            onto_sha = self._access.resolve_ref_oid(plan.onto)
        except RefNotFoundError as e:
            raise RebaseError(f"Invalid onto ref: {plan.onto}") from e

        self._access.checkout_detached(onto_sha)

        state = RebaseState(
            original_head=original_head,
            original_branch=original_branch,
            onto=plan.onto,
            steps=[
                {"action": s.action, "commit_sha": s.commit_sha, "message": s.message}
                for s in plan.steps
            ],
            current_step=0,
            completed_commits=[],
        )
        self._save_state(state)

        return self._execute_steps(state)

    def continue_rebase(self) -> RebaseResult:
        """Continue a paused rebase."""
        state = self._load_state()
        if state is None:
            raise NoRebaseInProgressError()

        if self._has_conflicts():
            conflicts = self._get_conflict_paths()
            raise RebaseConflictError(conflicts)

        step = self._step_from_dict(state.steps[state.current_step])
        original_commit = self._access.resolve_commit(step.commit_sha)
        head_sha = self._access.must_head_target()
        tree_sha = self._access.index.write_tree()

        if step.action in ("squash", "fixup") and state.completed_commits:
            self._squash_into_previous(step, state, concat_message=(step.action == "squash"))
            state.current_step += 1
            self._save_state(state)
            return self._execute_steps(state)

        if step.action == "reword":
            message = step.message or original_commit.message
        else:
            message = step.message or original_commit.message

        new_sha = self._access.create_commit(
            "HEAD",
            original_commit.author,
            self._access.default_signature,
            message,
            tree_sha,
            [head_sha],
        )
        state.completed_commits.append(new_sha)
        state.current_step += 1
        self._save_state(state)

        return self._execute_steps(state)

    def skip(self) -> RebaseResult:
        """Skip the current commit and continue."""
        state = self._load_state()
        if state is None:
            raise NoRebaseInProgressError()

        head_sha = self._access.must_head_target()
        self._access.reset(head_sha, RESET_HARD)
        self._access.state_cleanup()

        state.current_step += 1
        self._save_state(state)

        return self._execute_steps(state)

    def abort(self) -> None:
        """Abort the rebase and restore original state."""
        state = self._load_state()
        if state is None:
            raise NoRebaseInProgressError()

        original_sha = self._access.resolve_ref_oid(state.original_head)
        self._access.reset(original_sha, RESET_HARD)

        if state.original_branch:
            self._access.set_head(f"refs/heads/{state.original_branch}")

        self._access.state_cleanup()
        self._state_path.unlink(missing_ok=True)

    def _execute_steps(self, state: RebaseState) -> RebaseResult:
        """Execute remaining steps in the plan."""
        total = len(state.steps)

        while state.current_step < total:
            step_dict = state.steps[state.current_step]
            step = self._step_from_dict(step_dict)

            if step.action == "drop":
                state.current_step += 1
                self._save_state(state)
                continue

            result = self._apply_step(step, state)
            if not result.success:
                return result

            state.current_step += 1
            self._save_state(state)

        return self._finalize(state)

    def _apply_step(self, step: RebaseStep, state: RebaseState) -> RebaseResult:
        """Apply a single rebase step."""
        commit = self._access.resolve_commit(step.commit_sha)
        head_sha = self._access.must_head_target()
        total = len(state.steps)

        if step.action == "edit":
            self._access.cherrypick(commit.sha)

            if self._has_conflicts():
                conflicts = self._get_conflict_paths()
                return RebaseResult(
                    success=False,
                    completed_steps=state.current_step,
                    total_steps=total,
                    state="conflict",
                    conflict_paths=tuple(conflicts),
                    current_commit=step.commit_sha,
                )

            return RebaseResult(
                success=False,
                completed_steps=state.current_step,
                total_steps=total,
                state="edit_pause",
                current_commit=step.commit_sha,
            )

        self._access.cherrypick(commit.sha)

        if self._has_conflicts():
            conflicts = self._get_conflict_paths()
            return RebaseResult(
                success=False,
                completed_steps=state.current_step,
                total_steps=total,
                state="conflict",
                conflict_paths=tuple(conflicts),
                current_commit=step.commit_sha,
            )

        if step.action == "squash":
            if state.completed_commits:
                return self._squash_into_previous(step, state, concat_message=True)
            message = step.message or commit.message
        elif step.action == "fixup":
            if state.completed_commits:
                return self._squash_into_previous(step, state, concat_message=False)
            message = commit.message
        elif step.action == "reword":
            message = step.message or commit.message
        else:  # pick
            message = commit.message

        tree_sha = self._access.index.write_tree()
        new_sha = self._access.create_commit(
            "HEAD",
            commit.author,
            self._access.default_signature,
            message,
            tree_sha,
            [head_sha],
        )
        state.completed_commits.append(new_sha)

        return RebaseResult(
            success=True,
            completed_steps=state.current_step + 1,
            total_steps=total,
            state="done",
        )

    def _squash_into_previous(
        self, step: RebaseStep, state: RebaseState, *, concat_message: bool
    ) -> RebaseResult:
        """Squash current changes into the previous commit."""
        commit = self._access.resolve_commit(step.commit_sha)
        total = len(state.steps)

        prev_sha = state.completed_commits[-1]
        prev_commit = self._access.resolve_commit(prev_sha)

        if concat_message:
            message = prev_commit.message.rstrip() + "\n\n" + commit.message
        else:
            message = prev_commit.message

        tree_sha = self._access.index.write_tree()
        parents = list(prev_commit.parent_shas)

        new_sha = self._access.create_commit(
            None,
            prev_commit.author,
            self._access.default_signature,
            message,
            tree_sha,
            parents,
        )

        state.completed_commits[-1] = new_sha
        self._access.reset(new_sha, RESET_HARD)

        return RebaseResult(
            success=True,
            completed_steps=state.current_step + 1,
            total_steps=total,
            state="done",
        )

    def _has_conflicts(self) -> bool:
        """Check if index has conflicts."""
        return self._access.index.conflicts is not None

    def _get_conflict_paths(self) -> list[str]:
        """Get paths with conflicts."""
        conflicts = self._access.index.conflicts
        if conflicts is None:
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for entry_tuple in conflicts:
            for side in entry_tuple:
                if side is None:
                    continue
                path = side.path
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
        return paths

    def _finalize(self, state: RebaseState) -> RebaseResult:
        """Finalize the rebase."""
        new_head_sha = self._access.must_head_target()

        if state.original_branch:
            self._access.set_branch_target(state.original_branch, new_head_sha)
            self._access.set_head(f"refs/heads/{state.original_branch}")

        self._state_path.unlink(missing_ok=True)
        self._access.state_cleanup()

        return RebaseResult(
            success=True,
            completed_steps=len(state.steps),
            total_steps=len(state.steps),
            state="done",
            new_head=new_head_sha,
        )

    def _save_state(self, state: RebaseState) -> None:
        data = {
            "original_head": state.original_head,
            "original_branch": state.original_branch,
            "onto": state.onto,
            "steps": state.steps,
            "current_step": state.current_step,
            "completed_commits": state.completed_commits,
        }
        self._state_path.write_text(json.dumps(data))

    def _load_state(self) -> RebaseState | None:
        if not self._state_path.exists():
            return None
        try:
            data = json.loads(self._state_path.read_text())
            return RebaseState(
                original_head=data["original_head"],
                original_branch=data.get("original_branch"),
                onto=data["onto"],
                steps=data["steps"],
                current_step=data["current_step"],
                completed_commits=data["completed_commits"],
            )
        except (json.JSONDecodeError, KeyError) as e:
            raise GitError(f"Corrupt rebase state file: {e}") from e

    def _step_from_dict(self, d: dict[str, str | None]) -> RebaseStep:
        action = d["action"]
        commit_sha = d["commit_sha"]
        if action is None or commit_sha is None:
            raise GitError("Invalid rebase step: missing action or commit_sha")
        return RebaseStep(
            action=action,  # type: ignore[arg-type]
            commit_sha=commit_sha,
            message=d.get("message"),
        )
