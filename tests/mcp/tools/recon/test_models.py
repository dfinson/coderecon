"""Tests for recon domain models — classifiers, intent extraction, candidate properties."""

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from coderecon.mcp.tools.recon.models import (
    ArtifactKind,
    EvidenceRecord,
    HarvestCandidate,
    ParsedTask,
    TaskIntent,
    _classify_artifact,
    _extract_intent,
    _is_barrel_file,
    _is_test_file,
)


# ── _is_test_file ────────────────────────────────────────────────


class TestIsTestFile:
    def test_test_prefix(self):
        assert _is_test_file("test_foo.py") is True

    def test_test_suffix(self):
        assert _is_test_file("foo_test.py") is True

    def test_tests_directory(self):
        assert _is_test_file("tests/unit/helpers.py") is True

    def test_test_directory(self):
        assert _is_test_file("test/unit/helpers.py") is True

    def test_regular_source(self):
        assert _is_test_file("src/module/core.py") is False

    def test_conftest_in_tests(self):
        assert _is_test_file("tests/conftest.py") is True

    def test_test_in_filename_not_prefix(self):
        # "testing.py" does NOT match — it doesn't start with "test_"
        assert _is_test_file("src/testing.py") is False


# ── _is_barrel_file ──────────────────────────────────────────────


class TestIsBarrelFile:
    def test_python_init(self):
        assert _is_barrel_file("src/pkg/__init__.py") is True

    def test_js_index(self):
        assert _is_barrel_file("lib/index.js") is True

    def test_ts_index(self):
        assert _is_barrel_file("lib/index.ts") is True

    def test_rust_mod(self):
        assert _is_barrel_file("crate/mod.rs") is True

    def test_regular_file(self):
        assert _is_barrel_file("src/utils.py") is False

    def test_tsx_index(self):
        assert _is_barrel_file("components/index.tsx") is True


# ── _classify_artifact ──────────────────────────────────────────


class TestClassifyArtifact:
    def test_python_source(self):
        assert _classify_artifact("src/module/core.py") == ArtifactKind.code

    def test_test_file(self):
        assert _classify_artifact("tests/test_core.py") == ArtifactKind.test

    def test_test_prefix(self):
        assert _classify_artifact("test_something.py") == ArtifactKind.test

    def test_config_yaml(self):
        assert _classify_artifact("config/app.yaml") == ArtifactKind.config

    def test_config_json(self):
        assert _classify_artifact("settings.json") == ArtifactKind.config

    def test_config_toml(self):
        assert _classify_artifact("config.toml") == ArtifactKind.config

    def test_doc_markdown(self):
        assert _classify_artifact("docs/readme.md") == ArtifactKind.doc

    def test_doc_rst(self):
        assert _classify_artifact("docs/api.rst") == ArtifactKind.doc

    def test_build_dockerfile(self):
        assert _classify_artifact("Dockerfile") == ArtifactKind.build

    def test_build_makefile(self):
        assert _classify_artifact("Makefile") == ArtifactKind.build

    def test_pyproject_toml_is_build(self):
        assert _classify_artifact("pyproject.toml") == ArtifactKind.build

    def test_test_takes_precedence_over_config(self):
        # A .json file inside tests/ → test, not config
        assert _classify_artifact("tests/fixtures/data.json") == ArtifactKind.test


# ── _extract_intent ──────────────────────────────────────────────


class TestExtractIntent:
    def test_debug_intent(self):
        assert _extract_intent("fix the bug in the parser") == TaskIntent.debug

    def test_implement_intent(self):
        assert _extract_intent("add a new feature for exporting") == TaskIntent.implement

    def test_refactor_intent(self):
        assert _extract_intent("refactor the module to simplify logic") == TaskIntent.refactor

    def test_understand_intent(self):
        assert _extract_intent("explain how the pipeline works") == TaskIntent.understand

    def test_test_intent(self):
        assert _extract_intent("write tests for the coverage parser") == TaskIntent.test

    def test_unknown_intent(self):
        assert _extract_intent("do something with the code") == TaskIntent.unknown

    def test_empty_string(self):
        assert _extract_intent("") == TaskIntent.unknown

    def test_multiple_intents_highest_count_wins(self):
        # "fix broken failing error" → 4 debug keywords
        # "add" → 1 implement keyword
        result = _extract_intent("fix broken failing error add")
        assert result == TaskIntent.debug

    def test_case_insensitive(self):
        assert _extract_intent("FIX THE BUG") == TaskIntent.debug


# ── ArtifactKind ──────────────────────────────────────────────────


class TestArtifactKind:
    def test_is_str_enum(self):
        assert ArtifactKind.code == "code"
        assert ArtifactKind.test == "test"

    def test_all_values(self):
        expected = {"code", "test", "config", "doc", "build"}
        assert {k.value for k in ArtifactKind} == expected


# ── TaskIntent ───────────────────────────────────────────────────


class TestTaskIntent:
    def test_all_values(self):
        expected = {"debug", "implement", "refactor", "understand", "test", "unknown"}
        assert {k.value for k in TaskIntent} == expected


# ── EvidenceRecord ───────────────────────────────────────────────


class TestEvidenceRecord:
    def test_basic_creation(self):
        ev = EvidenceRecord(category="term_match", detail="matched foo", score=0.8)
        assert ev.category == "term_match"
        assert ev.score == 0.8

    def test_default_score(self):
        ev = EvidenceRecord(category="explicit", detail="agent provided")
        assert ev.score == 0.0


# ── HarvestCandidate properties ──────────────────────────────────


class TestHarvestCandidate:
    def test_evidence_axes_none(self):
        c = HarvestCandidate(def_uid="uid1")
        assert c.evidence_axes == 0

    def test_evidence_axes_single(self):
        c = HarvestCandidate(def_uid="uid1", from_term_match=True)
        assert c.evidence_axes == 1

    def test_evidence_axes_multiple(self):
        c = HarvestCandidate(
            def_uid="uid1",
            from_term_match=True,
            from_explicit=True,
            from_graph=True,
        )
        assert c.evidence_axes == 3

    def test_evidence_axes_all(self):
        c = HarvestCandidate(
            def_uid="uid1",
            from_term_match=True,
            from_explicit=True,
            from_graph=True,
            from_coverage=True,
        )
        assert c.evidence_axes == 4

    def test_has_semantic_evidence_two_terms(self):
        c = HarvestCandidate(def_uid="uid1", matched_terms={"foo", "bar"})
        assert c.has_semantic_evidence is True

    def test_has_semantic_evidence_single_term_low_hub(self):
        c = HarvestCandidate(def_uid="uid1", matched_terms={"foo"}, hub_score=2)
        assert c.has_semantic_evidence is False

    def test_has_semantic_evidence_single_term_high_hub(self):
        c = HarvestCandidate(def_uid="uid1", matched_terms={"foo"}, hub_score=3)
        assert c.has_semantic_evidence is True

    def test_has_semantic_evidence_explicit(self):
        c = HarvestCandidate(def_uid="uid1", from_explicit=True)
        assert c.has_semantic_evidence is True

    def test_has_semantic_evidence_graph(self):
        c = HarvestCandidate(def_uid="uid1", from_graph=True)
        assert c.has_semantic_evidence is True

    def test_has_semantic_evidence_none(self):
        c = HarvestCandidate(def_uid="uid1")
        assert c.has_semantic_evidence is False

    def test_has_structural_evidence_hub(self):
        c = HarvestCandidate(def_uid="uid1", hub_score=1)
        assert c.has_structural_evidence is True

    def test_has_structural_evidence_shares_file(self):
        c = HarvestCandidate(def_uid="uid1", shares_file_with_seed=True)
        assert c.has_structural_evidence is True

    def test_has_structural_evidence_callee(self):
        c = HarvestCandidate(def_uid="uid1", is_callee_of_top=True)
        assert c.has_structural_evidence is True

    def test_has_structural_evidence_imported(self):
        c = HarvestCandidate(def_uid="uid1", is_imported_by_top=True)
        assert c.has_structural_evidence is True

    def test_has_structural_evidence_none(self):
        c = HarvestCandidate(def_uid="uid1")
        assert c.has_structural_evidence is False

    def test_matches_negative_name(self):
        mock_def = MagicMock()
        mock_def.name = "FooHelper"
        c = HarvestCandidate(def_uid="uid1", def_fact=mock_def, file_path="src/bar.py")
        assert c.matches_negative(["foohelper"]) is True

    def test_matches_negative_path(self):
        mock_def = MagicMock()
        mock_def.name = "Baz"
        c = HarvestCandidate(def_uid="uid1", def_fact=mock_def, file_path="src/deprecated/old.py")
        assert c.matches_negative(["deprecated"]) is True

    def test_matches_negative_empty_list(self):
        c = HarvestCandidate(def_uid="uid1")
        assert c.matches_negative([]) is False

    def test_has_strong_single_axis_explicit(self):
        c = HarvestCandidate(def_uid="uid1", from_explicit=True)
        assert c.has_strong_single_axis is True

    def test_has_strong_single_axis_high_hub(self):
        c = HarvestCandidate(def_uid="uid1", hub_score=8)
        assert c.has_strong_single_axis is True

    def test_has_strong_single_axis_many_terms(self):
        c = HarvestCandidate(def_uid="uid1", matched_terms={"a", "b", "c"})
        assert c.has_strong_single_axis is True

    def test_has_strong_single_axis_none(self):
        c = HarvestCandidate(def_uid="uid1", hub_score=7, matched_terms={"a", "b"})
        assert c.has_strong_single_axis is False


# ── ParsedTask ───────────────────────────────────────────────────


class TestParsedTask:
    def test_frozen(self):
        pt = ParsedTask(raw="some task")
        with pytest.raises(FrozenInstanceError):
            pt.raw = "other"  # type: ignore[misc]

    def test_defaults(self):
        pt = ParsedTask(raw="some task")
        assert pt.intent == TaskIntent.unknown
        assert pt.primary_terms == []
        assert pt.secondary_terms == []
        assert pt.explicit_paths == []
        assert pt.explicit_symbols == []
        assert pt.keywords == []
        assert pt.query_text == ""
        assert pt.negative_mentions == []
        assert pt.is_stacktrace_driven is False
        assert pt.is_test_driven is False
