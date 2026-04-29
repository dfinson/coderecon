"""Apply/cancel mixin for RefactorOps."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from coderecon.adapters.mutation.ops import Edit, MutationOps

from coderecon.refactor.ops_models import RefactorResult

log = structlog.get_logger(__name__)


class _ApplyMixin:
    """Mixin providing apply/cancel/clear_pending methods for RefactorOps."""

    async def apply(self, refactor_id: str, mutation_ops: MutationOps) -> RefactorResult:
        """Apply a previewed refactoring.
        Args:
            refactor_id: ID from preview result
            mutation_ops: MutationOps instance to perform edits
        Returns:
            RefactorResult with applied delta.
        """
        if refactor_id not in self._pending:
            raise ValueError(f"No pending refactor with ID: {refactor_id}")
        preview = self._pending[refactor_id]
        edits: list[Edit] = []
        # Import Edit here to avoid circular imports if not available at module level
        # But we added it to TYPE_CHECKING. We need it at runtime.
        from coderecon.adapters.mutation.ops import Edit
        for file_edit in preview.edits:
            full_path = self._repo_root / file_edit.path
            if not full_path.exists():
                # Skip or warn? For now, skip files that disappeared
                continue
            # Read file content
            content = full_path.read_text(encoding="utf-8")
            lines = content.splitlines(keepends=True)
            # Group hunks by line for this file
            hunks_by_line: dict[int, list[EditHunk]] = {}
            for hunk in file_edit.hunks:
                hunks_by_line.setdefault(hunk.line, []).append(hunk)
            # Apply edits to lines
            new_lines = []
            for i, line_content in enumerate(lines, 1):  # 1-based indexing
                if i in hunks_by_line:
                    # Apply replacements on this line
                    # Sort by length of 'old' descending to avoid substring issues often
                    # but simple replace is dangerous without columns.
                    # Proceeding with simple replace per current arch.
                    current_line = line_content
                    for hunk in hunks_by_line[i]:
                        current_line = current_line.replace(hunk.old, hunk.new)
                    new_lines.append(current_line)
                else:
                    new_lines.append(line_content)
            # Reconstruct content
            new_content = "".join(new_lines)
            edits.append(Edit(path=file_edit.path, action="update", content=new_content))
        # Execute mutation (import reference updates)
        mutation_result = mutation_ops.write_source(edits)
        # Physical file move (per SPEC.md §lines 1524-1531)
        if preview.move_from and preview.move_to:
            src = self._repo_root / preview.move_from
            dst = self._repo_root / preview.move_to
            if src.exists():
                import shutil
                import subprocess
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Check if file is git-tracked
                try:
                    subprocess.run(
                        ["git", "ls-files", "--error-unmatch", preview.move_from],
                        cwd=self._repo_root,
                        capture_output=True,
                        check=True,
                        timeout=30,
                    )
                    # Tracked: use git mv to preserve history
                    subprocess.run(
                        ["git", "mv", preview.move_from, preview.move_to],
                        cwd=self._repo_root,
                        capture_output=True,
                        check=True,
                        timeout=30,
                    )
                except subprocess.CalledProcessError:
                    # Untracked or dirty: plain filesystem move
                    shutil.move(str(src), str(dst))
        # Clear pending
        del self._pending[refactor_id]
        return RefactorResult(
            refactor_id=refactor_id,
            status="applied",
            applied=mutation_result.delta,
            changed_paths=mutation_result.changed_paths,
        )
    async def cancel(self, refactor_id: str) -> RefactorResult:
        """Cancel a pending refactoring.
        Args:
            refactor_id: ID from preview result
        Returns:
            RefactorResult with cancelled status.
        """
        if refactor_id in self._pending:
            del self._pending[refactor_id]
        return RefactorResult(
            refactor_id=refactor_id,
            status="cancelled",
        )
    def clear_pending(self) -> None:
        """Discard all pending refactor previews.
        Called by checkpoint to prevent stale previews from
        accumulating across edit cycles.
        """
        self._pending.clear()
