from __future__ import annotations

from coderecon.index.ops_glob import (
    _compile_glob_pattern,
    _compile_glob_set,
    _glob_to_regex,
    _matches_filter_paths,
    _matches_glob,
)


# ---------------------------------------------------------------------------
# _glob_to_regex — anchoring rules
# ---------------------------------------------------------------------------

class TestGlobToRegexAnchoring:
    """Verify anchoring behaviour for different pattern shapes."""

    def test_absolute_pattern_anchored(self):
        regex = _glob_to_regex("/src/foo.py")
        assert regex.startswith("^")
        assert regex.endswith("$")

    def test_double_star_prefix_anchored(self):
        regex = _glob_to_regex("**/foo.py")
        assert regex.startswith("^")
        assert regex.endswith("$")

    def test_relative_with_slash_right_anchored(self):
        regex = _glob_to_regex("src/foo.py")
        assert regex.startswith("(?:^|.*/)")

    def test_bare_pattern_matches_last_component(self):
        regex = _glob_to_regex("*.py")
        assert regex.startswith("(?:^|/)")

    def test_double_star_alone(self):
        regex = _glob_to_regex("**")
        assert ".*" in regex


# ---------------------------------------------------------------------------
# _glob_to_regex — wildcard translation
# ---------------------------------------------------------------------------

class TestGlobToRegexWildcards:
    def test_single_star_no_separator(self):
        regex = _glob_to_regex("*.py")
        assert "[^/]*" in regex

    def test_question_mark(self):
        regex = _glob_to_regex("?.py")
        assert "[^/]" in regex

    def test_double_star_slash(self):
        regex = _glob_to_regex("**/test.py")
        assert "(?:.+/)?" in regex

    def test_character_class_passthrough(self):
        regex = _glob_to_regex("[abc].py")
        assert "[abc]" in regex

    def test_character_class_negation(self):
        regex = _glob_to_regex("[!abc].py")
        assert "[^abc]" in regex


# ---------------------------------------------------------------------------
# _matches_glob — end-to-end matching
# ---------------------------------------------------------------------------

class TestMatchesGlob:
    def test_bare_star_py(self):
        assert _matches_glob("foo.py", "*.py")
        assert not _matches_glob("foo.txt", "*.py")

    def test_bare_star_py_nested(self):
        assert _matches_glob("src/foo.py", "*.py")

    def test_double_star_py(self):
        assert _matches_glob("src/deep/foo.py", "**/*.py")
        assert _matches_glob("foo.py", "**/*.py")

    def test_relative_with_slash(self):
        assert _matches_glob("src/foo.py", "src/foo.py")
        assert _matches_glob("root/src/foo.py", "src/foo.py")
        assert not _matches_glob("other/foo.py", "src/foo.py")

    def test_absolute_pattern(self):
        assert _matches_glob("/src/foo.py", "/src/foo.py")
        assert not _matches_glob("extra/src/foo.py", "/src/foo.py")

    def test_question_mark(self):
        assert _matches_glob("a.py", "?.py")
        assert not _matches_glob("ab.py", "?.py")

    def test_character_class(self):
        assert _matches_glob("a.py", "[abc].py")
        assert not _matches_glob("d.py", "[abc].py")

    def test_empty_pattern_empty_path(self):
        assert _matches_glob("", "")

    def test_empty_pattern_nonempty_path(self):
        assert not _matches_glob("foo.py", "")

    def test_empty_path_nonempty_pattern(self):
        assert not _matches_glob("", "*.py")

    def test_double_star_at_end(self):
        assert _matches_glob("src/a/b/c.py", "src/**")


# ---------------------------------------------------------------------------
# _compile_glob_pattern — caching
# ---------------------------------------------------------------------------

def test_compile_glob_pattern_returns_same_object():
    """LRU cache should return the identical compiled pattern."""
    p1 = _compile_glob_pattern("*.py")
    p2 = _compile_glob_pattern("*.py")
    assert p1 is p2


# ---------------------------------------------------------------------------
# _compile_glob_set
# ---------------------------------------------------------------------------

def test_compile_glob_set_empty():
    assert _compile_glob_set([]) is None


def test_compile_glob_set_single():
    pat = _compile_glob_set(["*.py"])
    assert pat is not None
    assert pat.search("foo.py")


def test_compile_glob_set_multiple():
    pat = _compile_glob_set(["*.py", "*.ts"])
    assert pat is not None
    assert pat.search("foo.py")
    assert pat.search("bar.ts")
    assert not pat.search("baz.rb")


def test_compile_glob_set_caching():
    """Same pattern list should hit cache."""
    p1 = _compile_glob_set(["*.py", "*.ts"])
    p2 = _compile_glob_set(["*.py", "*.ts"])
    assert p1 is p2


# ---------------------------------------------------------------------------
# _matches_filter_paths
# ---------------------------------------------------------------------------

class TestMatchesFilterPaths:
    def test_exact_file_match(self):
        assert _matches_filter_paths("src/foo.py", ["src/foo.py"])

    def test_directory_prefix_with_slash(self):
        assert _matches_filter_paths("src/foo.py", ["src/"])

    def test_directory_prefix_without_slash(self):
        assert _matches_filter_paths("src/foo.py", ["src"])

    def test_directory_prefix_boundary(self):
        """'src' should NOT match 'src2/foo.py'."""
        assert not _matches_filter_paths("src2/foo.py", ["src"])

    def test_glob_pattern(self):
        assert _matches_filter_paths("src/foo.py", ["**/*.py"])

    def test_no_match(self):
        assert not _matches_filter_paths("src/foo.rb", ["*.py", "tests/"])

    def test_multiple_patterns_any_match(self):
        assert _matches_filter_paths("tests/test_a.py", ["src/", "tests/"])

    def test_empty_filter_paths(self):
        assert not _matches_filter_paths("src/foo.py", [])

    def test_normalized_trailing_slash_exact(self):
        """'src/' should match the directory path 'src' exactly."""
        assert _matches_filter_paths("src", ["src/"])
