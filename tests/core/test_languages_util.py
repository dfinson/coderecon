"""Smoke tests for languages_util — language detection and metadata utilities."""

from coderecon.core.languages import (
    build_include_specs,
    build_marker_definitions,
    detect_language_family,
    exportable_kinds_for_language,
    get_include_globs,
    get_markers,
    is_ambiguous_extension,
    is_name_exported,
    validate_language_families,
    validate_markers_are_exact_filenames,
)


class TestExportableKinds:
    def test_python_kinds(self) -> None:
        kinds = exportable_kinds_for_language("python")
        assert isinstance(kinds, frozenset)
        assert "function" in kinds
        assert "class" in kinds

    def test_unknown_language_returns_defaults(self) -> None:
        kinds = exportable_kinds_for_language("nonexistent")
        assert "function" in kinds
        assert "class" in kinds


class TestIsNameExported:
    def test_python_public(self) -> None:
        assert is_name_exported("my_func", "python") is True

    def test_python_private(self) -> None:
        assert is_name_exported("_private", "python") is False

    def test_go_exported(self) -> None:
        assert is_name_exported("Handler", "go") is True

    def test_go_unexported(self) -> None:
        assert is_name_exported("handler", "go") is False

    def test_other_language_default(self) -> None:
        assert is_name_exported("anything", "rust") is True


class TestDetectLanguageFamily:
    def test_python_file(self) -> None:
        assert detect_language_family("foo.py") == "python"

    def test_javascript_file(self) -> None:
        assert detect_language_family("app.js") == "javascript"

    def test_unknown_extension(self) -> None:
        assert detect_language_family("file.xyz_unknown") is None


class TestAmbiguousExtension:
    def test_unambiguous(self) -> None:
        assert is_ambiguous_extension(".py") is False

    def test_h_is_not_ambiguous(self) -> None:
        # .h is mapped to a single language in this codebase
        assert is_ambiguous_extension(".h") is False


class TestGetIncludeGlobs:
    def test_python_globs(self) -> None:
        globs = get_include_globs("python")
        assert isinstance(globs, tuple)
        assert any("*.py" in g for g in globs)


class TestGetMarkers:
    def test_returns_tuples(self) -> None:
        includes, excludes = get_markers("python")
        assert isinstance(includes, tuple)
        assert isinstance(excludes, tuple)


class TestBuildSpecs:
    def test_include_specs(self) -> None:
        specs = build_include_specs()
        assert isinstance(specs, dict)
        assert "python" in specs

    def test_marker_definitions(self) -> None:
        defs = build_marker_definitions()
        assert isinstance(defs, dict)


class TestValidation:
    def test_validate_families(self) -> None:
        errors = validate_language_families()
        assert errors == []

    def test_validate_markers(self) -> None:
        errors = validate_markers_are_exact_filenames()
        assert errors == []
