"""Integration tests for refactor operations — rename, impact, move, apply, cancel."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from coderecon.adapters.mutation.ops import MutationOps
from coderecon.refactor.ops import RefactorOps
from coderecon.refactor.ops_models import (
    EditHunk,
    FileEdit,
    RefactorPreview,
)

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = pytest.mark.integration


@pytest.fixture
def refactor_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a git repo with multiple modules referencing each other."""
    repo = tmp_path / "refactor_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True, check=True
    )

    (repo / "src").mkdir()
    (repo / "src" / "calculator.py").write_text(
        'class Calculator:\n    """Calculator class."""\n    def add(self, a, b):\n        return a + b\n'
    )
    (repo / "src" / "main.py").write_text(
        'from calculator import Calculator\n\ndef run():\n    # Use Calculator\n    c = Calculator()\n    return c.add(1, 2)\n'
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        'from calculator import Calculator\n\ndef test_calc():\n    """Test Calculator.add method."""\n    assert Calculator().add(1, 2) == 3\n'
    )

    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True
    )
    yield repo


def _mock_coordinator() -> MagicMock:
    """Create a mock coordinator with default async returns."""
    coordinator = MagicMock()
    coordinator.get_all_defs = AsyncMock(return_value=[])
    coordinator.get_all_references = AsyncMock(return_value=[])
    coordinator.search = AsyncMock(return_value=MagicMock(results=[]))
    coordinator._db = MagicMock()
    return coordinator


class TestRefactorOpsInit:
    def test_creates_refactor_ops(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        assert ops._repo_root == refactor_repo
        assert ops._pending == {}


class TestExtractSymbolAtLocation:
    @pytest.mark.asyncio
    async def test_extracts_class(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        symbol = await ops._extract_symbol_at_location("src/calculator.py", 1)
        assert symbol == "Calculator"

    @pytest.mark.asyncio
    async def test_extracts_function(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        symbol = await ops._extract_symbol_at_location("src/main.py", 3)
        assert symbol == "run"

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_none(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        result = await ops._extract_symbol_at_location("ghost.py", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_out_of_range_line_returns_none(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        result = await ops._extract_symbol_at_location("src/calculator.py", 9999)
        assert result is None


class TestRenamePathLineParsing:
    @pytest.mark.asyncio
    async def test_detects_path_line_col_format(self, refactor_repo: Path) -> None:
        """When agent passes path:line:col, ops extracts the actual symbol."""
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        result = await ops.rename("src/calculator.py:1:6", "NewCalc")
        # Should detect the format and extract "Calculator"
        assert result.status == "previewed"
        # The warning field should mention the detection
        if result.warning:
            assert "path:line:col" in result.warning

    @pytest.mark.asyncio
    async def test_invalid_path_line_col_returns_error(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        result = await ops.rename("ghost.py:999:0", "NewName")
        assert result.status == "previewed"
        assert result.preview is not None
        assert result.preview.verification_required is True
        assert result.preview.verification_guidance is not None
        assert "ERROR" in result.preview.verification_guidance


class TestRenameBasic:
    @pytest.mark.asyncio
    async def test_rename_with_no_defs_uses_lexical(self, refactor_repo: Path) -> None:
        """When index has no defs, lexical fallback finds occurrences."""
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        result = await ops.rename("Calculator", "Calc")
        assert result.status == "previewed"
        assert result.refactor_id is not None
        # Store the preview so we can test apply/cancel
        assert result.refactor_id in ops._pending

    @pytest.mark.asyncio
    async def test_rename_returns_unique_ids(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        r1 = await ops.rename("Calculator", "Calc1")
        r2 = await ops.rename("Calculator", "Calc2")
        assert r1.refactor_id != r2.refactor_id


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_pending(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        preview = await ops.rename("Calculator", "Calc")
        result = await ops.cancel(preview.refactor_id)
        assert result.status == "cancelled"
        assert preview.refactor_id not in ops._pending

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_is_safe(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        result = await ops.cancel("nonexistent-id")
        assert result.status == "cancelled"


class TestClearPending:
    @pytest.mark.asyncio
    async def test_clear_removes_all(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        await ops.rename("Calculator", "Calc1")
        await ops.rename("Calculator", "Calc2")
        assert len(ops._pending) >= 2
        ops.clear_pending()
        assert len(ops._pending) == 0


class TestApply:
    @pytest.mark.asyncio
    async def test_apply_nonexistent_raises(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        mutation_ops = MutationOps(refactor_repo)
        with pytest.raises(ValueError, match="No pending"):
            await ops.apply("no-such-id", mutation_ops)

    @pytest.mark.asyncio
    async def test_apply_valid_preview(self, refactor_repo: Path) -> None:
        """Apply a manually-injected preview to verify the apply flow."""
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        mutation_ops = MutationOps(refactor_repo)

        # Manually inject a preview
        preview = RefactorPreview(
            files_affected=1,
            edits=[
                FileEdit(
                    path="src/calculator.py",
                    hunks=[EditHunk(old="Calculator", new="Calc", line=1, certainty="high")],
                ),
            ],
        )
        ops._pending["test-ref-id"] = preview

        result = await ops.apply("test-ref-id", mutation_ops)
        assert result.status == "applied"
        assert result.applied is not None
        # Verify file was actually changed on the target line
        content = (refactor_repo / "src" / "calculator.py").read_text()
        assert "class Calc:" in content
        # Pending should be cleared
        assert "test-ref-id" not in ops._pending

    @pytest.mark.asyncio
    async def test_apply_skips_missing_files(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        mutation_ops = MutationOps(refactor_repo)

        preview = RefactorPreview(
            files_affected=1,
            edits=[
                FileEdit(
                    path="deleted_file.py",
                    hunks=[EditHunk(old="foo", new="bar", line=1, certainty="high")],
                ),
            ],
        )
        ops._pending["skip-ref"] = preview

        result = await ops.apply("skip-ref", mutation_ops)
        assert result.status == "applied"


class TestComputeCertainty:
    def test_proven_ref(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        ref = MagicMock()
        ref.ref_tier = "PROVEN"
        ref.certainty = "CERTAIN"
        assert ops._compute_rename_certainty(ref) == "high"

    def test_strong_ref(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        ref = MagicMock()
        ref.ref_tier = "STRONG"
        ref.certainty = "CERTAIN"
        assert ops._compute_rename_certainty(ref) == "high"

    def test_anchored_ref(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        ref = MagicMock()
        ref.ref_tier = "ANCHORED"
        ref.certainty = None
        assert ops._compute_rename_certainty(ref) == "medium"

    def test_unknown_ref_certain_fallback(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        ref = MagicMock()
        ref.ref_tier = None
        ref.certainty = "CERTAIN"
        assert ops._compute_rename_certainty(ref) == "high"

    def test_unknown_ref_unknown_certainty(self, refactor_repo: Path) -> None:
        coordinator = _mock_coordinator()
        ops = RefactorOps(refactor_repo, coordinator)
        ref = MagicMock()
        ref.ref_tier = None
        ref.certainty = None
        assert ops._compute_rename_certainty(ref) == "low"
