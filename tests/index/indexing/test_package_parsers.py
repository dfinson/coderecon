"""Tests for index/_internal/indexing/package_parsers.py module.

Covers:
- parse_go_mod()
- resolve_go_module()
- parse_cargo_toml()
- resolve_rust_module()
- _parse_export_target()
- build_js_package_exports()
- _resolve_export_target()
- _normalize_path()
"""

import json

from coderecon.index.resolution.package_parsers import (
    _normalize_path,
    _parse_export_target,
    _resolve_export_target,
    build_js_package_exports,
    parse_cargo_toml,
    parse_go_mod,
    resolve_go_module,
    resolve_rust_module,
)


# ===========================================================================
# parse_go_mod tests
# ===========================================================================

class TestParseGoMod:
    """Tests for parse_go_mod function."""

    def test_simple_module(self) -> None:
        text = "module github.com/user/repo\n\ngo 1.21\n"
        assert parse_go_mod(text) == "github.com/user/repo"

    def test_module_with_version(self) -> None:
        text = "module github.com/org/project\n\ngo 1.20\nrequire ...\n"
        assert parse_go_mod(text) == "github.com/org/project"

    def test_module_with_multiple_lines(self) -> None:
        text = "// go.mod file\nmodule github.com/example/myproject\n\ngo 1.21\n"
        assert parse_go_mod(text) == "github.com/example/myproject"

    def test_empty_go_mod(self) -> None:
        assert parse_go_mod("") is None
        assert parse_go_mod("go 1.21\nrequire ...") is None

    def test_module_at_different_positions(self) -> None:
        assert parse_go_mod("\n\nmodule foo/bar") == "foo/bar"
        assert parse_go_mod("comment\nmodule foo/bar\nmore") == "foo/bar"

    def test_module_path_with_special_characters(self) -> None:
        assert parse_go_mod("module bitbucket.org/team/repo-name") == "bitbucket.org/team/repo-name"
        assert parse_go_mod("module gitlab.com/group/sub-group/project") == "gitlab.com/group/sub-group/project"

    def test_only_extracts_first_module(self) -> None:
        text = "module first/module\nmodule second/module"
        assert parse_go_mod(text) == "first/module"


# ===========================================================================
# resolve_go_module tests
# ===========================================================================

class TestResolveGoModule:
    """Tests for resolve_go_module function."""

    def test_file_in_root_package(self) -> None:
        result = resolve_go_module(
            file_path="main.go",
            _short_package="main",
            go_mod_path="go.mod",
            go_mod_module="github.com/user/repo",
        )
        assert result == "github.com/user/repo"

    def test_file_in_subdirectory(self) -> None:
        result = resolve_go_module(
            file_path="pkg/auth/token.go",
            _short_package="auth",
            go_mod_path="go.mod",
            go_mod_module="github.com/user/repo",
        )
        assert result == "github.com/user/repo/pkg/auth"

    def test_nested_go_mod_same_module(self) -> None:
        result = resolve_go_module(
            file_path="tools/gen/main.go",
            _short_package="main",
            go_mod_path="tools/go.mod",
            go_mod_module="github.com/user/repo/tools",
        )
        assert result == "github.com/user/repo/tools/gen"

    def test_file_outside_go_mod_directory(self) -> None:
        result = resolve_go_module(
            file_path="other/file.go",
            _short_package="other",
            go_mod_path="subdir/go.mod",
            go_mod_module="github.com/user/repo",
        )
        assert result is None

    def test_short_package_none(self) -> None:
        result = resolve_go_module(
            file_path="pkg/utils/helper.go",
            _short_package=None,
            go_mod_path="go.mod",
            go_mod_module="github.com/user/repo",
        )
        assert result == "github.com/user/repo/pkg/utils"

    def test_deeply_nested_structure(self) -> None:
        result = resolve_go_module(
            file_path="internal/api/v1/handlers/users.go",
            _short_package="users",
            go_mod_path="go.mod",
            go_mod_module="app.internal",
        )
        assert result == "app.internal/internal/api/v1/handlers"


# ===========================================================================
# parse_cargo_toml tests
# ===========================================================================

class TestParseCargoToml:
    """Tests for parse_cargo_toml function."""

    def test_simple_cargo_toml(self) -> None:
        text = '[package]\nname = "my_crate"\nversion = "0.1.0"'
        assert parse_cargo_toml(text) == "my_crate"

    def test_cargo_toml_with_dependencies(self) -> None:
        text = '[package]\nname = "web_app"\nversion = "1.0.0"\n\n[dependencies]\nserde = "1.0"\n'
        assert parse_cargo_toml(text) == "web_app"

    def test_name_with_hyphens_underscores(self) -> None:
        assert parse_cargo_toml('[package]\nname = "my-pkg_v2"') == "my-pkg_v2"

    def test_empty_cargo_toml(self) -> None:
        assert parse_cargo_toml("") is None

    def test_no_package_section(self) -> None:
        assert parse_cargo_toml('[dependencies]\nserde = "1.0"') is None

    def test_no_name_field(self) -> None:
        assert parse_cargo_toml('[package]\nversion = "1.0"') is None

    def test_package_section_after_dependencies(self) -> None:
        text = '[dependencies]\n[package]\nname = "mylib"'
        assert parse_cargo_toml(text) == "mylib"

    def test_comments_and_whitespace(self) -> None:
        text = '# My package\n[package]\nname = "commented_pkg"\nversion = "0.1"\n'
        assert parse_cargo_toml(text) == "commented_pkg"


# ===========================================================================
# resolve_rust_module tests
# ===========================================================================

class TestResolveRustModule:
    """Tests for resolve_rust_module function."""

    def test_lib_rs_at_root(self) -> None:
        result = resolve_rust_module("src/lib.rs", "Cargo.toml", "my_crate")
        assert result == "my_crate"

    def test_main_rs_at_root(self) -> None:
        result = resolve_rust_module("src/main.rs", "Cargo.toml", "my_app")
        assert result == "my_app"

    def test_module_in_subdirectory(self) -> None:
        result = resolve_rust_module("src/auth/token.rs", "Cargo.toml", "my_crate")
        assert result == "my_crate::auth::token"

    def test_mod_rs_file(self) -> None:
        result = resolve_rust_module("src/auth/mod.rs", "Cargo.toml", "my_crate")
        assert result == "my_crate::auth"

    def test_deeply_nested_module(self) -> None:
        result = resolve_rust_module(
            "src/api/v1/handlers/users.rs", "Cargo.toml", "server"
        )
        assert result == "server::api::v1::handlers::users"

    def test_nested_cargo_toml(self) -> None:
        result = resolve_rust_module(
            "tools/generator/src/lib.rs", "tools/generator/Cargo.toml", "codegen"
        )
        assert result == "codegen"

    def test_file_outside_cargo_directory(self) -> None:
        result = resolve_rust_module(
            "other/src/lib.rs", "subdir/Cargo.toml", "mycrate"
        )
        assert result is None

    def test_src_directory_prefix_stripped(self) -> None:
        result = resolve_rust_module("src/models/user.rs", "Cargo.toml", "db")
        assert result == "db::models::user"
        assert "src" not in result


# ===========================================================================
# _parse_export_target tests
# ===========================================================================

class TestParseExportTarget:
    """Tests for _parse_export_target function."""

    def test_string_export(self) -> None:
        assert _parse_export_target("./src/index.ts") == "./src/index.ts"

    def test_dict_with_source_key(self) -> None:
        value = {
            "@zod/source": "./src/index.ts",
            "import": "./dist/index.mjs",
            "require": "./dist/index.cjs",
        }
        assert _parse_export_target(value) == "./src/index.ts"

    def test_dict_prefers_types_over_import(self) -> None:
        value = {
            "types": "./dist/types/index.d.ts",
            "import": "./dist/index.mjs",
        }
        assert _parse_export_target(value) == "./dist/types/index.d.ts"

    def test_dict_fallback_to_import(self) -> None:
        value = {"import": "./dist/esm/index.js", "require": "./dist/cjs/index.js"}
        assert _parse_export_target(value) == "./dist/esm/index.js"

    def test_dict_fallback_to_require(self) -> None:
        value = {"require": "./dist/cjs/index.js", "default": "./dist/browser.js"}
        assert _parse_export_target(value) == "./dist/cjs/index.js"

    def test_dict_fallback_to_default(self) -> None:
        value = {"default": "./dist/index.js"}
        assert _parse_export_target(value) == "./dist/index.js"

    def test_empty_dict_returns_none(self) -> None:
        assert _parse_export_target({}) is None

    def test_non_dict_non_string_returns_none(self) -> None:
        assert _parse_export_target(None) is None
        assert _parse_export_target(123) is None
        assert _parse_export_target([]) is None


# ===========================================================================
# _resolve_export_target tests
# ===========================================================================

class TestResolveExportTarget:
    """Tests for _resolve_export_target function."""

    def test_exact_match(self) -> None:
        result = _resolve_export_target("src/index.ts", {"src/index.ts", "src/utils.ts"})
        assert result == "src/index.ts"

    def test_js_to_ts_remapping(self) -> None:
        result = _resolve_export_target("src/index.js", {"src/index.ts"})
        assert result == "src/index.ts"

    def test_jsx_to_tsx_remapping(self) -> None:
        result = _resolve_export_target("components/Button.jsx", {"components/Button.tsx"})
        assert result == "components/Button.tsx"

    def test_mjs_to_mts_remapping(self) -> None:
        result = _resolve_export_target("lib/esm.mjs", {"lib/esm.mts"})
        assert result == "lib/esm.mts"

    def test_extension_probing_ts_first(self) -> None:
        result = _resolve_export_target("src/module", {"src/module.ts", "src/module.js"})
        assert result == "src/module.ts"

    def test_index_file_probing(self) -> None:
        result = _resolve_export_target("src/utils", {"src/utils/index.ts"})
        assert result == "src/utils/index.ts"

    def test_no_match_returns_none(self) -> None:
        result = _resolve_export_target("nonexistent/path.js", {"src/index.ts"})
        assert result is None

    def test_empty_paths_set_returns_none(self) -> None:
        result = _resolve_export_target("src/index.js", set())
        assert result is None


# ===========================================================================
# _normalize_path tests
# ===========================================================================

class TestNormalizePath:
    """Tests for _normalize_path function."""

    def test_simple_relative_path(self) -> None:
        assert _normalize_path("src/utils") == "src/utils"

    def test_remove_dot_segments(self) -> None:
        assert _normalize_path("./src") == "src"
        assert _normalize_path("src/./utils") == "src/utils"

    def test_resolve_parent_segments(self) -> None:
        assert _normalize_path("src/utils/../models") == "src/models"
        assert _normalize_path("a/b/c/../../d") == "a/d"

    def test_leading_parent_segments_dropped(self) -> None:
        assert _normalize_path("../../outside") == "outside"

    def test_empty_path(self) -> None:
        assert _normalize_path("") == ""

    def test_trailing_slashes_removed(self) -> None:
        assert _normalize_path("src/utils/") == "src/utils"
        assert _normalize_path("src//utils") == "src/utils"

    def test_complex_normalization(self) -> None:
        assert _normalize_path("src/./utils/../models/./user") == "src/models/user"

    def test_windows_path_converted_to_posix(self) -> None:
        assert _normalize_path("src\\utils\\models") == "src/utils/models"


# ===========================================================================
# build_js_package_exports integration tests
# ===========================================================================

class TestBuildJsPackageExports:
    """Tests for build_js_package_exports function."""

    def test_single_package_with_root_export(self) -> None:
        files = ["package.json", "src/index.ts"]
        pkg_content = json.dumps({
            "name": "mylib",
            "exports": {".": {"import": "./src/index.ts"}}
        })

        def read_file(path: str) -> str | None:
            return pkg_content if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result == {"mylib": "src/index.ts"}

    def test_package_with_multiple_exports(self) -> None:
        files = ["package.json", "src/index.ts", "src/utils.ts"]
        pkg_content = json.dumps({
            "name": "utils",
            "exports": {
                ".": {"import": "./src/index.ts"},
                "./helpers": {"import": "./src/utils.ts"},
            }
        })

        def read_file(path: str) -> str | None:
            return pkg_content if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result["utils"] == "src/index.ts"
        assert result["utils/helpers"] == "src/utils.ts"

    def test_scoped_package_exports(self) -> None:
        files = ["packages/web/package.json", "packages/web/src/index.ts"]
        pkg_content = json.dumps({
            "name": "@myorg/web",
            "exports": {".": {"import": "./src/index.ts"}}
        })

        def read_file(path: str) -> str | None:
            return pkg_content if path == "packages/web/package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result["@myorg/web"] == "packages/web/src/index.ts"

    def test_js_export_resolved_to_ts(self) -> None:
        files = ["package.json", "src/index.ts"]
        pkg_content = json.dumps({
            "name": "hybrid",
            "exports": {".": {"import": "./src/index.js"}}
        })

        def read_file(path: str) -> str | None:
            return pkg_content if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result["hybrid"] == "src/index.ts"

    def test_skips_wildcard_exports(self) -> None:
        files = ["package.json", "src/index.ts"]
        pkg_content = json.dumps({
            "name": "lib",
            "exports": {
                "./*": {"import": "./src/*.ts"},
                ".": {"import": "./src/index.ts"},
            }
        })

        def read_file(path: str) -> str | None:
            return pkg_content if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert "lib" in result
        assert len(result) == 1

    def test_skips_non_relative_exports(self) -> None:
        files = ["package.json"]
        pkg_content = json.dumps({
            "name": "pkg",
            "exports": {".": "some-external-module"}
        })

        def read_file(path: str) -> str | None:
            return pkg_content if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result == {}

    def test_skips_malformed_json(self) -> None:
        files = ["package.json", "src/index.ts"]

        def read_file(path: str) -> str | None:
            return "{invalid json" if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result == {}

    def test_skips_packages_without_exports(self) -> None:
        files = ["package.json", "src/index.ts"]
        pkg_content = json.dumps({"name": "noexports", "main": "./src/index.ts"})

        def read_file(path: str) -> str | None:
            return pkg_content if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result == {}

    def test_multiple_packages_in_monorepo(self) -> None:
        files = [
            "packages/web/package.json",
            "packages/web/src/index.ts",
            "packages/cli/package.json",
            "packages/cli/src/bin.ts",
        ]

        def read_file(path: str) -> str | None:
            if path == "packages/web/package.json":
                return json.dumps({
                    "name": "@org/web",
                    "exports": {".": {"import": "./src/index.ts"}}
                })
            elif path == "packages/cli/package.json":
                return json.dumps({
                    "name": "@org/cli",
                    "exports": {".": {"import": "./src/bin.ts"}}
                })
            return None

        result = build_js_package_exports(files, read_file)
        assert result["@org/web"] == "packages/web/src/index.ts"
        assert result["@org/cli"] == "packages/cli/src/bin.ts"

    def test_file_not_found_skipped(self) -> None:
        files = ["package.json"]
        pkg_content = json.dumps({
            "name": "pkg",
            "exports": {".": {"import": "./src/missing.ts"}}
        })

        def read_file(path: str) -> str | None:
            return pkg_content if path == "package.json" else None

        result = build_js_package_exports(files, read_file)
        assert result == {}
