"""Smoke tests for languages_testing — test file detection and pairing."""

from coderecon.core.languages import (
    find_test_pairs,
    get_test_patterns,
    is_test_file,
)


class TestGetTestPatterns:
    def test_known_language(self) -> None:
        patterns = get_test_patterns("python")
        assert isinstance(patterns, tuple)
        assert any("test_" in p for p in patterns)

    def test_unknown_language(self) -> None:
        assert get_test_patterns("nonexistent_lang_xyz") == ()


class TestIsTestFile:
    def test_python_test(self) -> None:
        assert is_test_file("tests/test_foo.py") is True

    def test_python_non_test(self) -> None:
        assert is_test_file("src/foo.py") is False

    def test_go_test(self) -> None:
        assert is_test_file("pkg/handler_test.go") is True

    def test_js_test(self) -> None:
        assert is_test_file("src/utils.test.js") is True

    def test_rust_test(self) -> None:
        # Rust test files are typically in tests/ directory
        assert is_test_file("src/main.rs") is False


class TestFindTestPairs:
    def test_python_source(self) -> None:
        pairs = find_test_pairs("src/coderecon/core/loader.py")
        assert isinstance(pairs, list)
        # Should suggest test file locations
        assert all(isinstance(p, str) for p in pairs)

    def test_non_source(self) -> None:
        pairs = find_test_pairs("README.md")
        assert isinstance(pairs, list)
