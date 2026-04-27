"""TreeSitterService -- singleton factory + generic extraction pipeline.

Replaces scattered ``TreeSitterParser()`` instantiations across the
codebase with a single shared instance that caches loaded grammars.

Provides a **data-driven** scope extractor (one function instead of three
near-identical methods) and pack-aware dispatch for all extraction
operations.

Usage::

    from coderecon.index._internal.parsing.service import tree_sitter_service

    # Parsing
    result = tree_sitter_service.parse(Path("main.py"), content)

    # Extraction (all pack-aware)
    symbols = tree_sitter_service.extract_symbols(result)
    scopes  = tree_sitter_service.extract_scopes(result)
    imports = tree_sitter_service.extract_imports(result, file_path)
    module  = tree_sitter_service.extract_declared_module(result, file_path)
    dynamic = tree_sitter_service.extract_dynamic_accesses(result)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import tree_sitter

from coderecon.index._internal.parsing.packs import (
    _GENERIC_SCOPE_PATTERNS,
    get_pack,
)
from coderecon.index._internal.parsing.treesitter import (
    DynamicAccess,
    ParseResult,
    SyntacticImport,
    SyntacticScope,
    SyntacticSymbol,
    TreeSitterParser,
)

class TreeSitterService:
    """Singleton wrapper around :class:`TreeSitterParser`.

    * Manages a single shared parser instance (with grammar caching).
    * Delegates extraction to pack-aware generic methods where possible.
    * Falls back to ``TreeSitterParser`` hand-written methods for imports,
      declared-module, and dynamic-accesses (those genuinely need method-based
      extraction for complex grammars like Rust use-trees and C# namespaces).
    """

    _instance: TreeSitterService | None = None

    def __init__(self) -> None:
        self._parser = TreeSitterParser()

    @classmethod
    def get(cls) -> TreeSitterService:
        """Return the singleton service instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (mainly for testing)."""
        cls._instance = None

    # Core parser access

    @property
    def parser(self) -> TreeSitterParser:
        """Direct access to the underlying parser (for cases not yet migrated)."""
        return self._parser

    def parse(self, path: Path, content: bytes | None = None) -> ParseResult:
        """Parse a file. Returns a :class:`ParseResult`."""
        return self._parser.parse(path, content)

    # Symbol extraction (delegates to TreeSitterParser -- already query-based)

    def extract_symbols(self, result: ParseResult) -> list[SyntacticSymbol]:
        """Extract symbol definitions from a parse result."""
        return self._parser.extract_symbols(result)

    def extract_identifier_occurrences(self, result: ParseResult) -> list[Any]:
        """Extract all identifier occurrences from a parse result."""
        return self._parser.extract_identifier_occurrences(result)

    def validate_code_file(self, result: ParseResult) -> Any:
        """Validate a code file parse result."""
        return self._parser.validate_code_file(result)

    def compute_interface_hash(self, symbols: list[SyntacticSymbol]) -> str:
        """Compute interface hash from symbols."""
        return self._parser.compute_interface_hash(symbols)

    # Scope extraction -- GENERIC (replaces 3 near-identical methods)

    def extract_scopes(self, result: ParseResult) -> list[SyntacticScope]:
        """Extract lexical scopes using pack-driven scope_types.

        Instead of ``_extract_python_scopes``, ``_extract_js_scopes``,
        ``_extract_generic_scopes`` with identical structure, this uses
        one walker parameterised by the ``scope_types`` dict from the pack.
        """
        pack = get_pack(result.language)
        if pack is not None and pack.scope_types:
            return _extract_scopes(result.root_node, pack.scope_types)
        # Fallback for unknown languages: pattern-match node types
        return _extract_scopes(result.root_node, _GENERIC_SCOPE_PATTERNS, substring_match=True)

    # Import extraction -- delegates to TreeSitterParser methods

    def extract_imports(self, result: ParseResult, file_path: str) -> list[SyntacticImport]:
        """Extract imports (delegates to hand-written per-language methods)."""
        return self._parser.extract_imports(result, file_path)

    # Declared module -- delegates to TreeSitterParser methods

    def extract_declared_module(self, result: ParseResult, file_path: str) -> str | None:
        """Extract language-level module/package/namespace declaration."""
        return self._parser.extract_declared_module(result, file_path)

    # Dynamic access -- delegates to TreeSitterParser methods

    def extract_dynamic_accesses(self, result: ParseResult) -> list[DynamicAccess]:
        """Extract dynamic access patterns."""
        return self._parser.extract_dynamic_accesses(result)

    # C# specific

    def extract_csharp_namespace_types(self, root_node: tree_sitter.Node) -> dict[str, list[str]]:
        """Extract C# namespace-to-types mapping."""
        return self._parser.extract_csharp_namespace_types(root_node)

# Generic scope walker -- replaces 3 near-identical methods (~170 lines)

def _extract_scopes(
    root: tree_sitter.Node,
    scope_types: dict[str, str],
    *,
    substring_match: bool = False,
) -> list[SyntacticScope]:
    """Walk tree and emit scopes driven by a ``scope_types`` dict.

    Args:
        root: Tree-sitter root node.
        scope_types: Mapping from node type (or substring) to scope kind.
        substring_match: If True, match ``scope_types`` keys as substrings
            of ``node.type`` instead of exact matches.

    Returns:
        Flat list of ``SyntacticScope`` with file scope at index 0.
    """
    scopes: list[SyntacticScope] = []
    scope_counter = 0

    # File scope (always id=0, parent=None)
    scopes.append(
        SyntacticScope(
            scope_id=scope_counter,
            parent_scope_id=None,
            kind="file",
            start_line=root.start_point[0] + 1,
            start_col=root.start_point[1],
            end_line=root.end_point[0] + 1,
            end_col=root.end_point[1],
        )
    )

    def _match_kind(node_type: str) -> str | None:
        if substring_match:
            for pattern, scope_kind in scope_types.items():
                if pattern in node_type:
                    return scope_kind
            return None
        return scope_types.get(node_type)

    def walk(node: tree_sitter.Node, parent_scope_id: int) -> None:
        nonlocal scope_counter

        kind = _match_kind(node.type)
        if kind is not None:
            scope_counter += 1
            scopes.append(
                SyntacticScope(
                    scope_id=scope_counter,
                    parent_scope_id=parent_scope_id,
                    kind=kind,
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
            )
            for child in node.children:
                walk(child, scope_counter)
        else:
            for child in node.children:
                walk(child, parent_scope_id)

    for child in root.children:
        walk(child, 0)

    return scopes

# Module-level singleton

tree_sitter_service = TreeSitterService.get()
