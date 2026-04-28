"""Tests for recon_constants pure functions and data."""

from __future__ import annotations

from coderecon.mcp.tools.recon.recon_constants import (
    ArtifactKind,
    TaskIntent,
    _classify_artifact,
    _extract_intent,
    _is_barrel_file,
    _is_test_file,
)


class TestArtifactKindEnum:
    def test_values(self) -> None:
        assert set(ArtifactKind) == {"code", "test", "config", "doc", "build"}

    def test_is_str_enum(self) -> None:
        assert isinstance(ArtifactKind.code, str)
        assert ArtifactKind.code == "code"


class TestTaskIntentEnum:
    def test_values(self) -> None:
        assert set(TaskIntent) == {
            "debug", "implement", "refactor", "understand", "test", "unknown",
        }

    def test_is_str_enum(self) -> None:
        assert isinstance(TaskIntent.debug, str)
        assert TaskIntent.unknown == "unknown"


class TestIsTestFile:
    def test_test_prefix(self) -> None:
        assert _is_test_file("tests/test_foo.py") is True

    def test_test_suffix(self) -> None:
        assert _is_test_file("src/foo_test.py") is True

    def test_tests_directory(self) -> None:
        assert _is_test_file("tests/unit/helpers.py") is True

    def test_test_directory(self) -> None:
        assert _is_test_file("test/integration/helpers.py") is True

    def test_not_test(self) -> None:
        assert _is_test_file("src/coderecon/core/utils.py") is False

    def test_conftest_in_tests(self) -> None:
        assert _is_test_file("tests/conftest.py") is True

    def test_production_code(self) -> None:
        assert _is_test_file("src/coderecon/index/models.py") is False


class TestIsBarrelFile:
    def test_python_init(self) -> None:
        assert _is_barrel_file("src/coderecon/__init__.py") is True

    def test_js_index(self) -> None:
        assert _is_barrel_file("packages/core/index.ts") is True

    def test_rust_mod(self) -> None:
        assert _is_barrel_file("src/mod.rs") is True

    def test_regular_file(self) -> None:
        assert _is_barrel_file("src/coderecon/core/utils.py") is False

    def test_index_jsx(self) -> None:
        assert _is_barrel_file("components/index.jsx") is True


class TestClassifyArtifact:
    def test_test_file(self) -> None:
        assert _classify_artifact("tests/test_foo.py") == ArtifactKind.test

    def test_source_code(self) -> None:
        assert _classify_artifact("src/coderecon/core/utils.py") == ArtifactKind.code

    def test_config_yaml(self) -> None:
        assert _classify_artifact("config/settings.yaml") == ArtifactKind.config

    def test_config_json(self) -> None:
        assert _classify_artifact("package.json") == ArtifactKind.config

    def test_doc_markdown(self) -> None:
        assert _classify_artifact("docs/architecture.md") == ArtifactKind.doc

    def test_build_makefile(self) -> None:
        assert _classify_artifact("Makefile") == ArtifactKind.build

    def test_build_dockerfile(self) -> None:
        assert _classify_artifact("Dockerfile") == ArtifactKind.build

    def test_pyproject_toml(self) -> None:
        assert _classify_artifact("pyproject.toml") == ArtifactKind.build

    def test_test_takes_priority_over_extension(self) -> None:
        assert _classify_artifact("tests/test_config.yaml") == ArtifactKind.test


class TestExtractIntent:
    def test_debug_intent(self) -> None:
        assert _extract_intent("fix the crash in parser") == TaskIntent.debug

    def test_implement_intent(self) -> None:
        assert _extract_intent("add support for TypeScript") == TaskIntent.implement

    def test_refactor_intent(self) -> None:
        assert _extract_intent("refactor the parser module") == TaskIntent.refactor

    def test_understand_intent(self) -> None:
        assert _extract_intent("explain how the index works") == TaskIntent.understand

    def test_test_intent(self) -> None:
        assert _extract_intent("write pytest coverage for ranking") == TaskIntent.test

    def test_unknown_intent(self) -> None:
        assert _extract_intent("xyzzy plugh") == TaskIntent.unknown

    def test_empty_string(self) -> None:
        assert _extract_intent("") == TaskIntent.unknown

    def test_highest_count_wins(self) -> None:
        """When multiple intents match, the one with more keyword hits wins."""
        # "fix bug error" has 3 debug keywords vs 1 implement keyword ("add")
        result = _extract_intent("fix bug error and add something")
        assert result == TaskIntent.debug
