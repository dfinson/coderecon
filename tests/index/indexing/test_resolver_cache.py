"""Tests for resolver_cache helper functions."""

from coderecon.index.resolution.resolver_cache import path_to_module


class TestPathToModule:
    def test_python_module(self):
        assert path_to_module("src/foo/bar.py") == "src.foo.bar"

    def test_python_init(self):
        assert path_to_module("src/foo/__init__.py") == "src.foo"

    def test_js_module(self):
        assert path_to_module("src/foo/bar.ts") == "src.foo.bar"

    def test_js_index(self):
        assert path_to_module("src/foo/index.ts") == "src.foo"

    def test_jsx(self):
        assert path_to_module("components/App.jsx") == "components.App"

    def test_rust_module(self):
        assert path_to_module("src/foo/bar.rs") == "src::foo::bar"

    def test_rust_mod(self):
        assert path_to_module("src/foo/mod.rs") == "src::foo"

    def test_rust_lib(self):
        assert path_to_module("src/lib.rs") == "src"

    def test_unknown_extension(self):
        assert path_to_module("Makefile") is None

    def test_mjs(self):
        assert path_to_module("lib/utils.mjs") == "lib.utils"

    def test_cjs(self):
        assert path_to_module("lib/utils.cjs") == "lib.utils"
