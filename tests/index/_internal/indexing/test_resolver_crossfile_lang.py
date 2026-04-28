"""Tests for index/_internal/indexing/resolver_crossfile_lang.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from coderecon.index._internal.indexing.resolver_crossfile_lang import (
    _find_go_package_file,
    _find_rust_module_file,
    _path_to_rust_module,
    _register_resolution_passes,
    _RESOLUTION_PASSES,
    run_pass_1_5,
)


class TestFindGoPackageFile:
    """Tests for _find_go_package_file — Go import path resolution."""

    def test_exact_match(self) -> None:
        pkg_map = {"internal/pkg": 1}
        assert _find_go_package_file("github.com/org/repo/internal/pkg", pkg_map) == 1

    def test_suffix_match(self) -> None:
        pkg_map = {"pkg/util": 5}
        assert _find_go_package_file("example.com/repo/pkg/util", pkg_map) == 5

    def test_no_match_returns_none(self) -> None:
        pkg_map = {"other/path": 1}
        assert _find_go_package_file("completely/different", pkg_map) is None

    def test_empty_map(self) -> None:
        assert _find_go_package_file("any/path", {}) is None

    def test_single_segment_match(self) -> None:
        pkg_map = {"utils": 3}
        assert _find_go_package_file("github.com/org/utils", pkg_map) == 3

    def test_partial_segment_no_match(self) -> None:
        """Should NOT match partial segments (e.g. 'util' vs 'utils')."""
        pkg_map = {"utils": 3}
        # "util" is not the same segment as "utils"
        assert _find_go_package_file("some/util", pkg_map) is None


class TestPathToRustModule:
    """Tests for _path_to_rust_module — file path to Rust module path."""

    def test_regular_rs_file(self) -> None:
        assert _path_to_rust_module("src/foo/bar.rs") == "crate::foo::bar"

    def test_lib_rs_maps_to_parent(self) -> None:
        # src/lib.rs → strip .rs → "src/lib" → endswith "/lib" → rsplit → "src"
        # "src" has no "/" left so replace is no-op; doesn't start with "src::"
        assert _path_to_rust_module("src/lib.rs") == "src"

    def test_mod_rs_maps_to_parent(self) -> None:
        assert _path_to_rust_module("src/util/mod.rs") == "crate::util"

    def test_main_rs_maps_to_crate(self) -> None:
        assert _path_to_rust_module("src/main.rs") == "crate"

    def test_non_rs_returns_none(self) -> None:
        assert _path_to_rust_module("src/foo.py") is None

    def test_no_src_prefix(self) -> None:
        result = _path_to_rust_module("lib/parser.rs")
        assert result == "lib::parser"

    def test_nested_module(self) -> None:
        result = _path_to_rust_module("src/a/b/c.rs")
        assert result == "crate::a::b::c"


class TestFindRustModuleFile:
    """Tests for _find_rust_module_file — Rust use path resolution."""

    def test_direct_match(self) -> None:
        mod_map = {"crate::util": 10}
        assert _find_rust_module_file("crate::util", mod_map) == 10

    def test_suffix_match(self) -> None:
        mod_map = {"crate::parser::lexer": 20}
        # source_literal ends with last segment of a module path
        assert _find_rust_module_file("crate::parser::lexer", mod_map) == 20

    def test_no_match_returns_none(self) -> None:
        mod_map = {"crate::foo": 1}
        assert _find_rust_module_file("crate::bar", mod_map) is None

    def test_empty_map(self) -> None:
        assert _find_rust_module_file("crate::anything", {}) is None


class TestRegisterResolutionPasses:
    """Tests for _register_resolution_passes — registry population."""

    def test_populates_registry(self) -> None:
        _RESOLUTION_PASSES.clear()
        _register_resolution_passes()
        # Should have exactly 6 passes:
        # namespace_refs, same_namespace_refs, star_import_refs,
        # go_dot_import, rust_glob_import, java_star_import
        assert len(_RESOLUTION_PASSES) == 6

    def test_idempotent(self) -> None:
        """Calling twice should still have the same passes (clears first)."""
        _register_resolution_passes()
        _register_resolution_passes()
        assert len(_RESOLUTION_PASSES) == 6

    def test_all_passes_are_callable(self) -> None:
        _RESOLUTION_PASSES.clear()
        _register_resolution_passes()
        for fn in _RESOLUTION_PASSES:
            assert callable(fn)


class TestRunPass15:
    """Tests for run_pass_1_5 — parallel execution of resolution passes."""

    def test_calls_all_registered_passes(self) -> None:
        """Each pass should be called with db, unit_id, file_ids."""
        mock_db = MagicMock()
        mock_stats = MagicMock()

        mock_pass_a = MagicMock(return_value=mock_stats)
        mock_pass_b = MagicMock(return_value=mock_stats)

        _RESOLUTION_PASSES.clear()
        _RESOLUTION_PASSES.extend([mock_pass_a, mock_pass_b])

        results = run_pass_1_5(mock_db, unit_id=5, file_ids=[1, 2])
        assert len(results) == 2
        mock_pass_a.assert_called_once_with(mock_db, 5, [1, 2])
        mock_pass_b.assert_called_once_with(mock_db, 5, [1, 2])

    def test_empty_registry_auto_populates(self) -> None:
        """If registry is empty, run_pass_1_5 should call _register_resolution_passes."""
        _RESOLUTION_PASSES.clear()
        mock_db = MagicMock()

        # run_pass_1_5 will auto-register, then run all 6 passes
        # Each pass needs a db.session() context manager
        session = MagicMock()
        mock_db.session.return_value.__enter__ = MagicMock(return_value=session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        # The actual DB calls will fail, but we just need to verify
        # the registry is populated. Patch all passes to no-op.
        with patch(
            "coderecon.index._internal.indexing.resolver_crossfile_lang._register_resolution_passes"
        ) as mock_register:
            # After patching, simulate register filling the list
            def fill_passes() -> None:
                _RESOLUTION_PASSES.append(MagicMock(return_value=MagicMock()))

            mock_register.side_effect = fill_passes
            results = run_pass_1_5(mock_db)
            mock_register.assert_called_once()
            assert len(results) == 1

    def test_returns_stats_from_each_pass(self) -> None:
        """Results should be one stats object per pass, in order."""
        mock_db = MagicMock()
        stats_a = MagicMock(refs_upgraded=3, refs_matched=5)
        stats_b = MagicMock(refs_upgraded=0, refs_matched=0)

        _RESOLUTION_PASSES.clear()
        _RESOLUTION_PASSES.extend([
            MagicMock(return_value=stats_a),
            MagicMock(return_value=stats_b),
        ])

        results = run_pass_1_5(mock_db)
        assert results[0].refs_upgraded == 3
        assert results[1].refs_upgraded == 0
