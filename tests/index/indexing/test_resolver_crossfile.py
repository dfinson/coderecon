"""Tests for index/_internal/indexing/resolver_crossfile.py module.

Covers:
- CrossFileResolutionStats dataclass
- _build_file_filter() SQL fragment builder
- _build_unit_filter() SQL fragment builder
- _path_to_python_module() path → module converter
- _find_python_module_file() module → file_id resolver
- _TYPE_KINDS / _TYPE_KIND_FILTER constants
"""

from __future__ import annotations

from coderecon.index.resolution.crossfile import (
    CrossFileResolutionStats,
    _TYPE_KIND_FILTER,
    _TYPE_KINDS,
    _build_file_filter,
    _build_unit_filter,
    _find_python_module_file,
    _path_to_python_module,
)


class TestCrossFileResolutionStats:
    """CrossFileResolutionStats dataclass defaults."""

    def test_defaults_zero(self) -> None:
        stats = CrossFileResolutionStats()
        assert stats.refs_upgraded == 0
        assert stats.refs_matched == 0

    def test_custom_values(self) -> None:
        stats = CrossFileResolutionStats(refs_upgraded=5, refs_matched=10)
        assert stats.refs_upgraded == 5
        assert stats.refs_matched == 10


class TestTypeKindsConstants:
    """Validate _TYPE_KINDS and _TYPE_KIND_FILTER."""

    def test_type_kinds_is_nonempty_tuple(self) -> None:
        assert isinstance(_TYPE_KINDS, tuple)
        assert len(_TYPE_KINDS) > 0

    def test_type_kinds_expected_entries(self) -> None:
        assert "class" in _TYPE_KINDS
        assert "struct" in _TYPE_KINDS
        assert "interface" in _TYPE_KINDS
        assert "enum" in _TYPE_KINDS

    def test_type_kind_filter_contains_all_kinds(self) -> None:
        for kind in _TYPE_KINDS:
            assert repr(kind) in _TYPE_KIND_FILTER


class TestBuildFileFilter:
    """_build_file_filter produces SQL fragments and bind dicts."""

    def test_none_returns_empty(self) -> None:
        sql, binds = _build_file_filter(None)
        assert sql == ""
        assert binds == {}

    def test_empty_list_returns_empty(self) -> None:
        sql, binds = _build_file_filter([])
        assert sql == ""
        assert binds == {}

    def test_single_id(self) -> None:
        sql, binds = _build_file_filter([42])
        assert "AND" in sql
        assert "IN" in sql
        assert ":fid_0" in sql
        assert binds == {"fid_0": 42}

    def test_multiple_ids(self) -> None:
        sql, binds = _build_file_filter([1, 2, 3])
        assert ":fid_0" in sql
        assert ":fid_1" in sql
        assert ":fid_2" in sql
        assert binds == {"fid_0": 1, "fid_1": 2, "fid_2": 3}

    def test_custom_alias(self) -> None:
        sql, _binds = _build_file_filter([10], alias="f")
        assert "f.file_id" in sql

    def test_default_alias(self) -> None:
        sql, _binds = _build_file_filter([10])
        assert "rf.file_id" in sql


class TestBuildUnitFilter:
    """_build_unit_filter produces SQL fragments and bind dicts."""

    def test_none_returns_empty(self) -> None:
        sql, binds = _build_unit_filter(None)
        assert sql == ""
        assert binds == {}

    def test_with_unit_id(self) -> None:
        sql, binds = _build_unit_filter(7)
        assert "AND" in sql
        assert "unit_id = :unit_id" in sql
        assert binds == {"unit_id": 7}

    def test_custom_alias(self) -> None:
        sql, _binds = _build_unit_filter(3, alias="df")
        assert "df.unit_id" in sql

    def test_default_alias(self) -> None:
        sql, _binds = _build_unit_filter(3)
        assert "rf.unit_id" in sql

    def test_zero_is_not_none(self) -> None:
        """unit_id=0 is a valid value, not treated as None."""
        sql, binds = _build_unit_filter(0)
        assert "unit_id = :unit_id" in sql
        assert binds == {"unit_id": 0}


class TestPathToPythonModule:
    """_path_to_python_module converts file paths to dotted module paths."""

    def test_simple_py_file(self) -> None:
        assert _path_to_python_module("foo/bar.py") == "foo.bar"

    def test_init_file(self) -> None:
        assert _path_to_python_module("foo/bar/__init__.py") == "foo.bar"

    def test_nested_path(self) -> None:
        assert _path_to_python_module("src/pkg/sub/module.py") == "src.pkg.sub.module"

    def test_non_python_returns_none(self) -> None:
        assert _path_to_python_module("foo/bar.js") is None
        assert _path_to_python_module("README.md") is None

    def test_root_init(self) -> None:
        result = _path_to_python_module("__init__.py")
        # Root __init__.py → empty string after stripping
        assert result is not None

    def test_backslash_path(self) -> None:
        result = _path_to_python_module("foo\\bar.py")
        assert result == "foo.bar"

    def test_leading_dot_stripped(self) -> None:
        result = _path_to_python_module("./foo/bar.py")
        assert result is not None
        assert not result.startswith(".")


class TestFindPythonModuleFile:
    """_find_python_module_file resolves import source literals to file_ids."""

    def test_direct_match(self) -> None:
        module_map = {"foo.bar": 42}
        result = _find_python_module_file("foo.bar", 1, module_map, [])
        assert result == 42

    def test_suffix_match_py(self) -> None:
        all_files: list[tuple[int | None, str]] = [(10, "src/foo/bar.py")]
        result = _find_python_module_file("foo.bar", 1, {}, all_files)
        assert result == 10

    def test_suffix_match_init(self) -> None:
        all_files: list[tuple[int | None, str]] = [(20, "src/foo/bar/__init__.py")]
        result = _find_python_module_file("foo.bar", 1, {}, all_files)
        assert result == 20

    def test_no_match_returns_none(self) -> None:
        all_files: list[tuple[int | None, str]] = [(1, "unrelated.py")]
        result = _find_python_module_file("foo.bar", 1, {}, all_files)
        assert result is None

    def test_direct_match_preferred_over_suffix(self) -> None:
        module_map = {"foo.bar": 99}
        all_files: list[tuple[int | None, str]] = [(10, "src/foo/bar.py")]
        result = _find_python_module_file("foo.bar", 1, module_map, all_files)
        assert result == 99

    def test_none_file_id_skipped(self) -> None:
        all_files: list[tuple[int | None, str]] = [(None, "src/foo/bar.py")]
        result = _find_python_module_file("foo.bar", 1, {}, all_files)
        assert result is None

    def test_none_path_skipped(self) -> None:
        all_files: list[tuple[int | None, str]] = [(10, None)]  # type: ignore[list-item]
        result = _find_python_module_file("foo.bar", 1, {}, all_files)
        assert result is None

    def test_no_false_positive_on_suffix_substring(self) -> None:
        """'foo.bar' should NOT match 'afoo/bar.py' (must be preceded by separator)."""
        all_files: list[tuple[int | None, str]] = [(10, "afoo/bar.py")]
        result = _find_python_module_file("foo.bar", 1, {}, all_files)
        assert result is None
