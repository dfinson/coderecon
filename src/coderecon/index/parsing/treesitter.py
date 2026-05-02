"""Tree-sitter parsing for syntactic analysis.

This module provides Tree-sitter parsing for:
- Local symbol extraction (functions, classes, methods, variables)
- Identifier occurrence tracking (where identifiers appear)
- Scope extraction (lexical scopes for binding resolution)
- Import extraction (import statements for cross-file refs)
- Interface hash computation (for dependency change detection)
- Probe validation (does this file parse correctly?)

Note: "identifier_occurrences" != "references". At the syntactic layer,
we only know "an identifier named X appears at line Y". Semantic resolution
(which definition does this refer to?) requires additional analysis.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import tree_sitter

from coderecon.index.parsing.packs import (
    PACKS,
    LanguagePack,
    get_pack,
    get_pack_for_ext,
    get_pack_for_filename,
)
from coderecon.index.parsing.treesitter_imports import (
    _extract_dynamic_via_query,
    _extract_imports_declarative,
)
from coderecon.index.parsing.treesitter_lang import (
    _declared_module_csharp,
    _declared_module_ocaml,
    _declared_module_ruby,
    _extract_declared_module_via_query,
)
from coderecon.index.parsing.treesitter_lang import (
    extract_csharp_namespace_types as _extract_csharp_namespace_types,
)
from coderecon.index.parsing.treesitter_models import (
    DynamicAccess,
    IdentifierOccurrence,
    ParseResult,
    ProbeValidation,
    SyntacticImport,
    SyntacticScope,
    SyntacticSymbol,
)
from coderecon.index.parsing.treesitter_symbols import (
    _extract_generic_symbols,
    _extract_symbols_via_query,
)

# Derive LANGUAGE_MAP from packs — includes aliases like shell→bash
LANGUAGE_MAP: dict[str, str] = {key: pack.grammar_name for key, pack in PACKS.items()}

log = structlog.get_logger(__name__)

@dataclass
class TreeSitterParser:
    """
    Tree-sitter parser for syntactic analysis.
    Provides parsing and symbol extraction for multiple languages.
    Uses tree-sitter-languages for grammar bundles.
    Usage::
        parser = TreeSitterParser()
        # Parse a file
        result = parser.parse(Path("src/foo.py"), content)
        # Extract symbols
        symbols = parser.extract_symbols(result)
        # Extract identifier occurrences
        occurrences = parser.extract_identifier_occurrences(result)
        # Compute interface hash
        hash = parser.compute_interface_hash(symbols)
        # Validate for probing
        validation = parser.validate_code_file(result)
    """
    _parser: tree_sitter.Parser = field(default=None, repr=False)  # type: ignore[assignment]
    _languages: dict[str, tree_sitter.Language] = field(default_factory=dict, repr=False)
    def __post_init__(self) -> None:
        """Initialize the parser."""
        self._parser = tree_sitter.Parser()
        self._languages = {}
    def _get_language(self, lang_name: str) -> tree_sitter.Language | None:
        """Get or load a Tree-sitter language.
        Uses LanguagePack metadata for module/function resolution instead
        of hard-coded special-case blocks.
        """
        if lang_name in self._languages:
            return self._languages[lang_name]
        # Find the pack for this grammar name (lang_name is grammar_name here)
        pack = self._find_pack_by_grammar(lang_name)
        if pack is not None and pack.language_func:
            # Non-standard language function (typescript, tsx, php, xml, ocaml)
            try:
                mod = importlib.import_module(pack.grammar_module)
                lang_fn = getattr(mod, pack.language_func)
                lang = tree_sitter.Language(lang_fn())
                self._languages[lang_name] = lang
                return lang
            except (ImportError, AttributeError) as err:
                raise ValueError(f"Language not available: {lang_name}") from err
        # Standard loading: module.language()
        lang_module = self._load_language_module(lang_name)
        if lang_module is None:
            raise ValueError(f"Language not available: {lang_name}")
        lang = tree_sitter.Language(lang_module.language())
        self._languages[lang_name] = lang
        return lang
    @staticmethod
    def _find_pack_by_grammar(grammar_name: str) -> LanguagePack | None:
        """Find the pack whose grammar_name matches."""
        # Fast path: name == grammar_name for most languages
        pack = get_pack(grammar_name)
        if pack is not None and pack.grammar_name == grammar_name:
            return pack
        # Slow path: search all packs (e.g. csharp -> c_sharp)
        for p in PACKS.values():
            if p.grammar_name == grammar_name:
                return p
        return None
    def _load_language_module(self, lang_name: str) -> Any:
        """Load tree-sitter language module by name.
        Uses LanguagePack metadata for module resolution.
        """
        pack = self._find_pack_by_grammar(lang_name)
        module_name = pack.grammar_module if pack is not None else None
        if module_name is None:
            return None
        try:
            return importlib.import_module(module_name)
        except ImportError:
            log.debug("language_module_import_failed")
            return None
    def parse(self, path: Path, content: bytes | None = None) -> ParseResult:
        """
        Parse a file with Tree-sitter.
        Args:
            path: Path to file (used for language detection)
            content: File content as bytes. If None, reads from path.
        Returns:
            ParseResult with tree, language, and error info.
        """
        if content is None:
            content = path.read_bytes()
        # Detect language from extension
        ext = path.suffix.lower().lstrip(".")
        language = self._detect_language_from_ext(ext)
        # Fallback: detect from filename (Makefile, Dockerfile, etc.)
        if language is None:
            language = self._detect_language_from_filename(path.name)
        if language is None:
            raise ValueError(f"Unsupported file extension: {ext}")
        pack = get_pack(language)
        ts_lang_name = pack.grammar_name if pack is not None else language
        ts_lang = self._get_language(ts_lang_name)
        self._parser.language = ts_lang
        tree = self._parser.parse(content)
        # Count errors and total nodes
        error_count = 0
        total_nodes = 0
        def count_nodes(node: tree_sitter.Node) -> None:
            nonlocal error_count, total_nodes
            total_nodes += 1
            if node.type == "ERROR" or node.is_missing:
                error_count += 1
            for child in node.children:
                count_nodes(child)
        count_nodes(tree.root_node)
        return ParseResult(
            tree=tree,
            language=language,
            error_count=error_count,
            total_nodes=total_nodes,
            root_node=tree.root_node,
            ts_language=ts_lang,
        )
    def extract_symbols(self, result: ParseResult) -> list[SyntacticSymbol]:
        """
        Extract symbol definitions from a parse result.
        Uses tree-sitter queries for all supported languages.  Each language
        has a declarative ``SymbolQueryConfig`` (defined in
        ``packs.py``) that maps query patterns to symbol kinds.
        The unified executor processes query matches, resolves parent
        context, and extracts parameter signatures.
        Args:
            result: ParseResult from parse()
        Returns:
            List of SyntacticSymbol objects.
        """
        pack = get_pack(result.language)
        config = pack.symbol_config if pack is not None else None
        if config is not None:
            return _extract_symbols_via_query(result.tree, result.root_node, config)
        # Generic extraction via walking for unsupported languages
        return _extract_generic_symbols(result.root_node, result.language)
    def extract_identifier_occurrences(self, result: ParseResult) -> list[IdentifierOccurrence]:
        """
        Extract all identifier occurrences from a parse result.
        Note: These are NOT semantic references. We only know that an
        identifier with a given name appears at a given location.
        Args:
            result: ParseResult from parse()
        Returns:
            List of IdentifierOccurrence objects.
        """
        occurrences: list[IdentifierOccurrence] = []
        def walk(node: tree_sitter.Node) -> None:
            if node.type == "identifier" or node.type.endswith("_identifier"):
                name = node.text.decode("utf-8") if node.text else ""
                if name:
                    occurrences.append(
                        IdentifierOccurrence(
                            name=name,
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )
            for child in node.children:
                walk(child)
        walk(result.root_node)
        return occurrences
    def extract_scopes(self, result: ParseResult) -> list[SyntacticScope]:
        """Extract lexical scopes from a parse result.
        Uses pack-driven scope_types for a single generic walker instead of
        per-language methods.
        Args:
            result: ParseResult from parse()
        Returns:
            List of SyntacticScope objects representing lexical scopes.
        """
        from coderecon.index.parsing.packs import (
            _GENERIC_SCOPE_PATTERNS,
        )
        from coderecon.index.parsing.service import (
            _extract_scopes,
        )
        pack = get_pack(result.language)
        if pack is not None and pack.scope_types:
            return _extract_scopes(result.root_node, pack.scope_types)
        return _extract_scopes(result.root_node, _GENERIC_SCOPE_PATTERNS, substring_match=True)
    def extract_imports(self, result: ParseResult, file_path: str) -> list[SyntacticImport]:
        """Extract import statements from a parse result.
        Uses declarative ImportQueryConfig from the language pack.
        Args:
            result: ParseResult from parse()
            file_path: File path for UID generation
        Returns:
            List of SyntacticImport objects.
        """
        pack = get_pack(result.language)
        if pack is None or pack.import_query_config is None:
            return []
        return _extract_imports_declarative(
            result.tree, result.root_node, pack.import_query_config, file_path
        )
    def extract_declared_module(self, result: ParseResult, file_path: str) -> str | None:
        """Extract the language-level module/package/namespace declaration.
        Uses tree-sitter queries when available, falls back to per-language
        handlers for complex cases.
        """
        pack = get_pack(result.language)
        # Try query-based extraction first
        if pack and pack.declared_module_query:
            return _extract_declared_module_via_query(result.tree, result.root_node, pack)
        # Fall back to handler-based extraction
        lang = result.language
        root = result.root_node
        if lang == "csharp":
            return _declared_module_csharp(root)
        elif lang == "ruby":
            return _declared_module_ruby(root)
        elif lang == "ocaml":
            return _declared_module_ocaml(file_path)
        return None
    def extract_dynamic_accesses(self, result: ParseResult) -> list[DynamicAccess]:
        """Extract dynamic access patterns for telemetry.
        Args:
            result: ParseResult from parse()
        Returns:
            List of DynamicAccess objects.
        """
        pack = get_pack(result.language)
        if pack is not None and pack.dynamic_query is not None:
            return _extract_dynamic_via_query(result.tree, result.root_node, pack)
        return []
    def compute_interface_hash(self, symbols: list[SyntacticSymbol]) -> str:
        """
        Compute a hash of the public interface of symbols.
        Used for dependency change detection: if a file's interface hash
        changes, dependents may need to be reindexed.
        Args:
            symbols: List of symbols from extract_symbols()
        Returns:
            SHA-256 hash of the interface signature.
        """
        # Sort symbols by name for determinism
        sorted_symbols = sorted(symbols, key=lambda s: (s.kind, s.name, s.line))
        # Build interface string
        parts: list[str] = []
        for sym in sorted_symbols:
            sig = sym.signature or ""
            parts.append(f"{sym.kind}:{sym.name}:{sig}")
        interface_str = "\n".join(parts)
        return hashlib.sha256(interface_str.encode()).hexdigest()
    def validate_code_file(self, result: ParseResult) -> ProbeValidation:
        """
        Validate a code file for context probing.
        Code families require:
        - Error nodes < 10% of total nodes
        - Has meaningful named nodes (not just comments/whitespace)
        Args:
            result: ParseResult from parse()
        Returns:
            ProbeValidation indicating if file is valid.
        """
        if result.total_nodes == 0:
            return ProbeValidation(
                is_valid=False,
                error_count=0,
                total_nodes=0,
                has_meaningful_content=False,
                error_ratio=0.0,
            )
        error_ratio = result.error_count / result.total_nodes
        has_meaningful = self._has_meaningful_nodes(result.root_node)
        # Valid if: error ratio < 10% AND has meaningful content
        is_valid = error_ratio < 0.10 and has_meaningful
        return ProbeValidation(
            is_valid=is_valid,
            error_count=result.error_count,
            total_nodes=result.total_nodes,
            has_meaningful_content=has_meaningful,
            error_ratio=error_ratio,
        )
    def validate_data_file(self, result: ParseResult) -> ProbeValidation:
        """
        Validate a data file for context probing.
        Data families require:
        - Valid tree (root has children)
        - Zero ERROR nodes
        Args:
            result: ParseResult from parse()
        Returns:
            ProbeValidation indicating if file is valid.
        """
        has_content = result.root_node is not None and len(result.root_node.children) > 0
        is_valid = has_content and result.error_count == 0
        return ProbeValidation(
            is_valid=is_valid,
            error_count=result.error_count,
            total_nodes=result.total_nodes,
            has_meaningful_content=has_content,
            error_ratio=(result.error_count / result.total_nodes if result.total_nodes > 0 else 0),
        )
    def _detect_language_from_ext(self, ext: str) -> str | None:
        """Detect language from file extension -- delegates to packs."""
        pack = get_pack_for_ext(ext)
        return pack.name if pack is not None else None
    def _detect_language_from_filename(self, filename: str) -> str | None:
        """Detect language from filename -- delegates to packs."""
        pack = get_pack_for_filename(filename)
        return pack.name if pack is not None else None
    def _has_meaningful_nodes(self, node: tree_sitter.Node) -> bool:
        """Check if tree has meaningful (non-comment, non-whitespace) nodes."""
        meaningless_types = {
            "comment",
            "line_comment",
            "block_comment",
            "ERROR",
            "MISSING",
        }
        def check(n: tree_sitter.Node) -> bool:
            if n.is_named and n.type not in meaningless_types:
                # Has at least one meaningful named node
                return True
            return any(check(child) for child in n.children)
        return check(node)
    # Unified query-based symbol extraction
    def extract_csharp_namespace_types(self, root: tree_sitter.Node) -> dict[str, list[str]]:
        """Extract C# namespace → type name mappings."""
        return _extract_csharp_namespace_types(root)
