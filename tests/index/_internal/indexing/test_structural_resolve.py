"""Tests for structural_resolve — import/module resolution post-extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.index._internal.indexing.structural_helpers import ExtractionResult
from coderecon.index._internal.indexing.structural_resolve import (
    _augment_declared_modules,
    _resolve_import_paths,
    _resolve_xref_target,
    resolve_all_imports,
)


def _make_extraction(
    file_path: str,
    language: str | None = None,
    declared_module: str | None = None,
    imports: list[dict] | None = None,
    error: str | None = None,
    skipped_no_grammar: bool = False,
) -> ExtractionResult:
    ex = ExtractionResult(file_path=file_path)
    ex.language = language
    ex.declared_module = declared_module
    ex.imports = imports or []
    ex.error = error
    ex.skipped_no_grammar = skipped_no_grammar
    return ex


# ── _augment_declared_modules ─────────────────────────────────────


class TestAugmentDeclaredModules:
    def _mock_db(self, file_paths: list[str]) -> MagicMock:
        db = MagicMock()
        session = MagicMock()
        session.exec.return_value.all.return_value = file_paths
        db.session.return_value.__enter__ = MagicMock(return_value=session)
        db.session.return_value.__exit__ = MagicMock(return_value=False)
        return db

    @patch("coderecon.index._internal.indexing.config_resolver.ConfigResolver")
    def test_go_extractions_resolved(self, MockResolver: MagicMock, tmp_path: Path) -> None:
        db = self._mock_db(["go.mod", "pkg/handler.go"])
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = "github.com/user/repo/pkg"

        ex = _make_extraction("pkg/handler.go", language="go", declared_module="pkg")
        _augment_declared_modules(db, tmp_path, [ex])

        resolver_inst.resolve.assert_called_once()
        assert ex.declared_module == "github.com/user/repo/pkg"

    @patch("coderecon.index._internal.indexing.config_resolver.ConfigResolver")
    def test_rust_extractions_resolved(self, MockResolver: MagicMock, tmp_path: Path) -> None:
        db = self._mock_db(["Cargo.toml", "src/auth/token.rs"])
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = "my_crate::auth::token"

        ex = _make_extraction("src/auth/token.rs", language="rust")
        _augment_declared_modules(db, tmp_path, [ex])

        assert ex.declared_module == "my_crate::auth::token"

    @patch("coderecon.index._internal.indexing.config_resolver.ConfigResolver")
    def test_skips_error_extractions(self, MockResolver: MagicMock, tmp_path: Path) -> None:
        db = self._mock_db([])
        ex = _make_extraction("bad.go", language="go", error="parse error")
        _augment_declared_modules(db, tmp_path, [ex])
        MockResolver.return_value.resolve.assert_not_called()

    @patch("coderecon.index._internal.indexing.config_resolver.ConfigResolver")
    def test_skips_skipped_no_grammar(self, MockResolver: MagicMock, tmp_path: Path) -> None:
        db = self._mock_db([])
        ex = _make_extraction("data.json", skipped_no_grammar=True)
        _augment_declared_modules(db, tmp_path, [ex])
        MockResolver.return_value.resolve.assert_not_called()

    @patch("coderecon.index._internal.indexing.module_mapping.path_to_module")
    @patch("coderecon.index._internal.indexing.config_resolver.ConfigResolver")
    def test_fallback_path_to_module_for_python(
        self, MockResolver: MagicMock, mock_p2m: MagicMock, tmp_path: Path,
    ) -> None:
        """When declared_module is None after go/rust phase, path_to_module is called."""
        db = self._mock_db([])
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = None
        mock_p2m.return_value = "mypackage.utils"

        ex = _make_extraction("mypackage/utils.py", language="python", declared_module=None)
        _augment_declared_modules(db, tmp_path, [ex])

        mock_p2m.assert_called_once_with("mypackage/utils.py")
        assert ex.declared_module == "mypackage.utils"

    @patch("coderecon.index._internal.indexing.module_mapping.path_to_module")
    @patch("coderecon.index._internal.indexing.config_resolver.ConfigResolver")
    def test_no_fallback_when_module_already_set(
        self, MockResolver: MagicMock, mock_p2m: MagicMock, tmp_path: Path,
    ) -> None:
        db = self._mock_db([])
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = "resolved.module"

        ex = _make_extraction("pkg/main.go", language="go", declared_module="main")
        _augment_declared_modules(db, tmp_path, [ex])

        # path_to_module should not be called since declared_module was set
        mock_p2m.assert_not_called()

    @patch("coderecon.index._internal.indexing.config_resolver.ConfigResolver")
    def test_overlays_current_batch_paths(self, MockResolver: MagicMock, tmp_path: Path) -> None:
        """Current batch files are included in the resolver's path set."""
        db = self._mock_db(["existing.go"])
        ex = _make_extraction("new_file.go", language="go")
        _augment_declared_modules(db, tmp_path, [ex])
        # ConfigResolver should have been called with paths including both
        call_args = MockResolver.call_args
        all_paths = call_args[0][1]
        assert "existing.go" in all_paths
        assert "new_file.go" in all_paths


# ── _resolve_xref_target ─────────────────────────────────────────


class TestResolveXrefTarget:
    def _mock_writer(self, responses: list) -> MagicMock:
        writer = MagicMock()
        conn = MagicMock()
        conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=r)) for r in responses
        ]
        writer.conn = conn
        return writer

    def test_exact_def_uid_match(self) -> None:
        writer = self._mock_writer([("mymod.MyClass",)])  # first query matches
        result = _resolve_xref_target(writer, "mymod.MyClass")
        assert result == "mymod.MyClass"

    def test_name_only_match(self) -> None:
        writer = self._mock_writer([None, ("pkg.MyFunc",)])  # exact miss, name match
        result = _resolve_xref_target(writer, "MyFunc")
        assert result == "pkg.MyFunc"

    def test_suffix_match(self) -> None:
        writer = self._mock_writer([None, None, ("long.path.Helper",)])  # exact miss, name miss, suffix match
        result = _resolve_xref_target(writer, "path.Helper")
        assert result == "long.path.Helper"

    def test_no_match(self) -> None:
        writer = self._mock_writer([None, None, None])
        result = _resolve_xref_target(writer, "nonexistent")
        assert result is None

    def test_simple_name_extraction(self) -> None:
        """Verifies the rsplit logic for extracting simple name from dotted path."""
        writer = self._mock_writer([None, ("found",)])
        result = _resolve_xref_target(writer, "a.b.c.MyClass")
        assert result == "found"
        # Second query should use simple name "MyClass"
        call_args = writer.conn.execute.call_args_list[1]
        assert call_args[0][1] == {"name": "MyClass"}


# ── _resolve_import_paths ─────────────────────────────────────────


class TestResolveImportPaths:
    def _mock_db(self, rows: list[tuple[str, str | None]]) -> MagicMock:
        db = MagicMock()
        session = MagicMock()
        session.exec.return_value.all.return_value = rows
        db.session.return_value.__enter__ = MagicMock(return_value=session)
        db.session.return_value.__exit__ = MagicMock(return_value=False)
        return db

    @patch("coderecon.index._internal.indexing.config_resolver.ImportPathResolver")
    @patch("coderecon.index._internal.indexing.config_resolver.build_js_package_exports")
    def test_resolves_imports(self, mock_js_exports: MagicMock, MockResolver: MagicMock, tmp_path: Path) -> None:
        mock_js_exports.return_value = {}
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = "utils/helper.py"

        db = self._mock_db([("main.py", None)])
        imp = {"source_literal": "utils.helper", "import_kind": "python"}
        ex = _make_extraction("main.py", language="python", imports=[imp])

        _resolve_import_paths(db, tmp_path, [ex])

        assert imp["resolved_path"] == "utils/helper.py"

    @patch("coderecon.index._internal.indexing.config_resolver.ImportPathResolver")
    @patch("coderecon.index._internal.indexing.config_resolver.build_js_package_exports")
    def test_unresolvable_import_no_resolved_path(self, mock_js_exports: MagicMock, MockResolver: MagicMock, tmp_path: Path) -> None:
        mock_js_exports.return_value = {}
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = None

        db = self._mock_db([])
        imp = {"source_literal": "unknown.module", "import_kind": "python"}
        ex = _make_extraction("main.py", language="python", imports=[imp])

        _resolve_import_paths(db, tmp_path, [ex])

        assert "resolved_path" not in imp

    @patch("coderecon.index._internal.indexing.config_resolver.ImportPathResolver")
    @patch("coderecon.index._internal.indexing.config_resolver.build_js_package_exports")
    def test_skips_error_extractions(self, mock_js_exports: MagicMock, MockResolver: MagicMock, tmp_path: Path) -> None:
        mock_js_exports.return_value = {}
        db = self._mock_db([])
        ex = _make_extraction("bad.py", error="parse error", imports=[{"source_literal": "x"}])

        _resolve_import_paths(db, tmp_path, [ex])

        MockResolver.return_value.resolve.assert_not_called()

    @patch("coderecon.index._internal.indexing.config_resolver.ImportPathResolver")
    @patch("coderecon.index._internal.indexing.config_resolver.build_js_package_exports")
    def test_overlays_batch_declared_modules(self, mock_js_exports: MagicMock, MockResolver: MagicMock, tmp_path: Path) -> None:
        mock_js_exports.return_value = {}
        db = self._mock_db([("old.py", "old_module")])
        ex = _make_extraction("new.py", language="python", declared_module="new_module", imports=[])

        _resolve_import_paths(db, tmp_path, [ex])

        # ImportPathResolver should be created with both old and new modules
        call_args = MockResolver.call_args
        declared_modules = call_args[0][1]
        assert declared_modules["old.py"] == "old_module"
        assert declared_modules["new.py"] == "new_module"


# ── resolve_all_imports ───────────────────────────────────────────


class TestResolveAllImports:
    @patch("coderecon.index._internal.indexing.config_resolver.ImportPathResolver")
    @patch("coderecon.index._internal.indexing.config_resolver.build_js_package_exports")
    def test_resolves_unresolved_imports(self, mock_js_exports: MagicMock, MockResolver: MagicMock, tmp_path: Path) -> None:
        mock_js_exports.return_value = {}
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = "lib/target.py"

        # Mock DB with file rows and import facts
        db = MagicMock()
        session = MagicMock()

        # First call: select File.path, File.declared_module
        file_rows = [("main.py", None), ("lib/target.py", "lib.target")]
        # Second call: select ImportFact (unresolved)
        import_fact = MagicMock()
        import_fact.file_id = 1
        import_fact.source_literal = "lib.target"
        import_fact.import_kind = "python"
        import_fact.resolved_path = None
        # Third call: select File.id, File.path for file_ids
        file_id_rows = [(1, "main.py")]

        session.exec.return_value.all.side_effect = [file_rows, [import_fact], file_id_rows]
        db.session.return_value.__enter__ = MagicMock(return_value=session)
        db.session.return_value.__exit__ = MagicMock(return_value=False)

        count = resolve_all_imports(db, tmp_path)

        assert count == 1
        assert import_fact.resolved_path == "lib/target.py"
        session.commit.assert_called_once()

    @patch("coderecon.index._internal.indexing.config_resolver.ImportPathResolver")
    @patch("coderecon.index._internal.indexing.config_resolver.build_js_package_exports")
    def test_returns_zero_when_nothing_resolved(self, mock_js_exports: MagicMock, MockResolver: MagicMock, tmp_path: Path) -> None:
        mock_js_exports.return_value = {}
        resolver_inst = MockResolver.return_value
        resolver_inst.resolve.return_value = None

        db = MagicMock()
        session = MagicMock()
        import_fact = MagicMock()
        import_fact.file_id = 1
        import_fact.source_literal = "unknown"
        import_fact.import_kind = ""
        import_fact.resolved_path = None

        session.exec.return_value.all.side_effect = [[], [import_fact], [(1, "main.py")]]
        db.session.return_value.__enter__ = MagicMock(return_value=session)
        db.session.return_value.__exit__ = MagicMock(return_value=False)

        count = resolve_all_imports(db, tmp_path)
        assert count == 0
        session.commit.assert_not_called()

    @patch("coderecon.index._internal.indexing.config_resolver.ImportPathResolver")
    @patch("coderecon.index._internal.indexing.config_resolver.build_js_package_exports")
    def test_returns_zero_when_no_unresolved(self, mock_js_exports: MagicMock, MockResolver: MagicMock, tmp_path: Path) -> None:
        mock_js_exports.return_value = {}
        db = MagicMock()
        session = MagicMock()
        session.exec.return_value.all.side_effect = [[], []]  # no files, no unresolved imports
        db.session.return_value.__enter__ = MagicMock(return_value=session)
        db.session.return_value.__exit__ = MagicMock(return_value=False)

        count = resolve_all_imports(db, tmp_path)
        assert count == 0
