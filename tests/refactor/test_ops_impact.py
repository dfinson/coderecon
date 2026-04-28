"""Tests for refactor impact-analysis mixin."""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.refactor.ops_models import (
    EditHunk,
    FileEdit,
    RefactorPreview,
    RefactorResult,
)
from coderecon.refactor.ops_impact import _ImpactMixin


def _make_mixin(
    *,
    repo_root: Path | None = None,
    pending: dict | None = None,
) -> _ImpactMixin:
    """Build an _ImpactMixin instance with mocked internals."""
    mixin = _ImpactMixin.__new__(_ImpactMixin)
    mixin._repo_root = repo_root or Path("/fake/repo")  # type: ignore[attr-defined]
    mixin._pending = pending if pending is not None else {}  # type: ignore[attr-defined]
    mixin._coordinator = MagicMock()  # type: ignore[attr-defined]
    return mixin


class TestImpact:
    """Tests for _ImpactMixin.impact()."""

    @pytest.mark.asyncio
    async def test_symbol_impact_calls_find_symbol_references(self) -> None:
        mixin = _make_mixin()
        # _add_comment_occurrences is inherited from RefactorOps, attach it as mock
        mixin._add_comment_occurrences = AsyncMock()  # type: ignore[attr-defined]
        with (
            patch.object(mixin, "_find_symbol_references", new_callable=AsyncMock) as mock_sym,
            patch.object(mixin, "_add_impact_lexical_fallback", new_callable=AsyncMock) as mock_lex,
            patch.object(mixin, "_build_impact_preview") as mock_build,
        ):
            preview = _make_preview_with_refs()
            mock_build.return_value = preview

            result = await mixin.impact("MyClass", include_comments=True)

            mock_sym.assert_awaited_once()
            mock_lex.assert_awaited_once()
            mixin._add_comment_occurrences.assert_awaited_once()  # type: ignore[attr-defined]
            assert result.status == "previewed"
            assert result.refactor_id in mixin._pending  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_file_impact_calls_find_file_references(self) -> None:
        mixin = _make_mixin()
        mixin._add_comment_occurrences = AsyncMock()  # type: ignore[attr-defined]
        with (
            patch.object(mixin, "_find_file_references", new_callable=AsyncMock) as mock_file,
            patch.object(mixin, "_add_impact_lexical_fallback", new_callable=AsyncMock),
            patch.object(mixin, "_build_impact_preview") as mock_build,
        ):
            mock_build.return_value = _make_preview_with_refs()

            result = await mixin.impact("src/utils/helpers.py", include_comments=False)

            mock_file.assert_awaited_once()
            assert result.status == "previewed"

    @pytest.mark.asyncio
    async def test_impact_without_comments(self) -> None:
        mixin = _make_mixin()
        mixin._add_comment_occurrences = AsyncMock()  # type: ignore[attr-defined]
        with (
            patch.object(mixin, "_find_symbol_references", new_callable=AsyncMock),
            patch.object(mixin, "_add_impact_lexical_fallback", new_callable=AsyncMock),
            patch.object(mixin, "_build_impact_preview") as mock_build,
        ):
            mock_build.return_value = _make_preview_with_refs()

            await mixin.impact("sym", include_comments=False)

            mixin._add_comment_occurrences.assert_not_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_impact_stores_in_pending(self) -> None:
        mixin = _make_mixin()
        mixin._add_comment_occurrences = AsyncMock()  # type: ignore[attr-defined]
        with (
            patch.object(mixin, "_find_symbol_references", new_callable=AsyncMock),
            patch.object(mixin, "_add_impact_lexical_fallback", new_callable=AsyncMock),
            patch.object(mixin, "_build_impact_preview") as mock_build,
        ):
            mock_build.return_value = _make_preview_with_refs()

            result = await mixin.impact("Target")
            assert result.refactor_id in mixin._pending  # type: ignore[attr-defined]
            assert mixin._pending[result.refactor_id] is mock_build.return_value  # type: ignore[attr-defined]


class TestFindSymbolReferences:
    """Tests for _ImpactMixin._find_symbol_references()."""

    @pytest.mark.asyncio
    async def test_collects_definitions_and_references(self) -> None:
        mixin = _make_mixin()

        def_fact = MagicMock()
        def_fact.name = "MyFunc"
        def_fact.file_id = 1
        def_fact.start_line = 10

        ref_fact = MagicMock()
        ref_fact.file_id = 2
        ref_fact.start_line = 25
        ref_fact.certainty = "CERTAIN"

        mixin._coordinator.get_all_defs = AsyncMock(return_value=[def_fact])  # type: ignore[attr-defined]
        mixin._coordinator.get_all_references = AsyncMock(return_value=[ref_fact])  # type: ignore[attr-defined]

        async def get_file_path(fid: int) -> str | None:
            return {1: "src/a.py", 2: "src/b.py"}.get(fid)

        mixin._get_file_path = get_file_path  # type: ignore[attr-defined]

        seen: set[tuple[str, int]] = set()
        edits: dict[str, list[EditHunk]] = {}

        await mixin._find_symbol_references("MyFunc", seen, edits)

        assert ("src/a.py", 10) in seen  # definition site
        assert ("src/b.py", 25) in seen  # reference site
        assert len(edits["src/a.py"]) == 1
        assert edits["src/a.py"][0].certainty == "high"
        assert len(edits["src/b.py"]) == 1
        assert edits["src/b.py"][0].certainty == "high"  # CERTAIN ref -> high

    @pytest.mark.asyncio
    async def test_deduplicates_locations(self) -> None:
        mixin = _make_mixin()

        def_fact = MagicMock()
        def_fact.name = "dup"
        def_fact.file_id = 1
        def_fact.start_line = 5

        mixin._coordinator.get_all_defs = AsyncMock(return_value=[def_fact])  # type: ignore[attr-defined]
        mixin._coordinator.get_all_references = AsyncMock(return_value=[])  # type: ignore[attr-defined]

        async def get_file_path(fid: int) -> str | None:
            return "src/a.py"

        mixin._get_file_path = get_file_path  # type: ignore[attr-defined]

        seen: set[tuple[str, int]] = {("src/a.py", 5)}  # already seen
        edits: dict[str, list[EditHunk]] = {}

        await mixin._find_symbol_references("dup", seen, edits)

        # Should not add duplicate
        assert "src/a.py" not in edits

    @pytest.mark.asyncio
    async def test_low_certainty_for_uncertain_refs(self) -> None:
        mixin = _make_mixin()

        def_fact = MagicMock()
        def_fact.name = "ambig"
        def_fact.file_id = 1
        def_fact.start_line = 3

        ref_fact = MagicMock()
        ref_fact.file_id = 2
        ref_fact.start_line = 10
        ref_fact.certainty = "UNCERTAIN"

        mixin._coordinator.get_all_defs = AsyncMock(return_value=[def_fact])  # type: ignore[attr-defined]
        mixin._coordinator.get_all_references = AsyncMock(return_value=[ref_fact])  # type: ignore[attr-defined]

        async def get_file_path(fid: int) -> str | None:
            return {1: "a.py", 2: "b.py"}.get(fid)

        mixin._get_file_path = get_file_path  # type: ignore[attr-defined]

        seen: set[tuple[str, int]] = set()
        edits: dict[str, list[EditHunk]] = {}

        await mixin._find_symbol_references("ambig", seen, edits)
        assert edits["b.py"][0].certainty == "low"


class TestAddImpactLexicalFallback:
    """Tests for _ImpactMixin._add_impact_lexical_fallback()."""

    @pytest.mark.asyncio
    async def test_adds_matches_from_search(self) -> None:
        mixin = _make_mixin()

        hit = MagicMock()
        hit.path = "src/found.py"
        hit.line = 42
        hit.snippet = "from foo import MyTarget"

        search_response = MagicMock()
        search_response.results = [hit]
        mixin._coordinator.search = AsyncMock(return_value=search_response)  # type: ignore[attr-defined]

        seen: set[tuple[str, int]] = set()
        edits: dict[str, list[EditHunk]] = {}

        await mixin._add_impact_lexical_fallback("MyTarget", seen, edits)

        assert ("src/found.py", 42) in seen
        assert len(edits["src/found.py"]) == 1
        assert edits["src/found.py"][0].certainty == "low"

    @pytest.mark.asyncio
    async def test_skips_already_seen_locations(self) -> None:
        mixin = _make_mixin()

        hit = MagicMock()
        hit.path = "src/dup.py"
        hit.line = 10
        hit.snippet = "uses MyTarget"

        search_response = MagicMock()
        search_response.results = [hit]
        mixin._coordinator.search = AsyncMock(return_value=search_response)  # type: ignore[attr-defined]

        seen: set[tuple[str, int]] = {("src/dup.py", 10)}
        edits: dict[str, list[EditHunk]] = {}

        await mixin._add_impact_lexical_fallback("MyTarget", seen, edits)

        assert "src/dup.py" not in edits

    @pytest.mark.asyncio
    async def test_skips_non_word_boundary_matches(self) -> None:
        mixin = _make_mixin()

        hit = MagicMock()
        hit.path = "src/partial.py"
        hit.line = 5
        hit.snippet = "MyTargetExtra is not a boundary match"

        search_response = MagicMock()
        search_response.results = [hit]
        mixin._coordinator.search = AsyncMock(return_value=search_response)  # type: ignore[attr-defined]

        seen: set[tuple[str, int]] = set()
        edits: dict[str, list[EditHunk]] = {}

        await mixin._add_impact_lexical_fallback("MyTarget", seen, edits)

        # "MyTargetExtra" should NOT match word boundary for "MyTarget"
        assert "src/partial.py" not in edits


class TestBuildImpactPreview:
    """Tests for _ImpactMixin._build_impact_preview()."""

    def test_sets_verification_guidance(self) -> None:
        mixin = _make_mixin()
        # Provide the _build_preview method that _build_impact_preview delegates to
        mixin._build_preview = MagicMock(return_value=RefactorPreview(  # type: ignore[attr-defined]
            files_affected=2,
            edits=[
                FileEdit(path="a.py", hunks=[
                    EditHunk(old="sym", new="", line=1, certainty="high"),
                ]),
                FileEdit(path="b.py", hunks=[
                    EditHunk(old="sym", new="", line=5, certainty="low"),
                ]),
            ],
            high_certainty_count=1,
            low_certainty_count=1,
        ))

        edits_by_file = {
            "a.py": [EditHunk(old="sym", new="", line=1, certainty="high")],
            "b.py": [EditHunk(old="sym", new="", line=5, certainty="low")],
        }

        preview = mixin._build_impact_preview("sym", edits_by_file)

        assert preview.verification_required is True
        assert "2 references" in preview.verification_guidance
        assert "sym" in preview.verification_guidance
        assert "High certainty: 1" in preview.verification_guidance
        assert "Low certainty: 1" in preview.verification_guidance

    def test_reference_count_in_guidance(self) -> None:
        mixin = _make_mixin()
        hunks = [EditHunk(old="t", new="", line=i, certainty="high") for i in range(1, 4)]
        mixin._build_preview = MagicMock(return_value=RefactorPreview(  # type: ignore[attr-defined]
            files_affected=1,
            edits=[FileEdit(path="x.py", hunks=hunks)],
            high_certainty_count=3,
            low_certainty_count=0,
        ))

        edits_by_file = {"x.py": hunks}
        preview = mixin._build_impact_preview("t", edits_by_file)
        assert "3 references" in preview.verification_guidance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_preview_with_refs() -> RefactorPreview:
    """Return a minimal preview for mocking."""
    return RefactorPreview(
        files_affected=1,
        edits=[
            FileEdit(path="src/a.py", hunks=[
                EditHunk(old="Target", new="", line=10, certainty="high"),
            ]),
        ],
        high_certainty_count=1,
        low_certainty_count=0,
        verification_required=True,
        verification_guidance="stub",
    )
