"""Import path resolution — resolves source_literal to repo-relative file paths.

This module provides deterministic resolution of import source literals
to the file paths they reference, for ALL languages with import systems.

Resolution strategies by language:

**Declaration-based** (Java, Kotlin, Scala, C#, Go, Haskell, Elixir,
Julia, Ruby, PHP):
  source_literal is matched against ``File.declared_module`` values.
  e.g. ``import cats.effect.IO`` → source_literal ``cats.effect.IO``
  matches file with ``declared_module = 'cats.effect'``.

**Path-based** (Python):
  source_literal is converted via ``module_to_candidate_paths()``
  and matched against ``path_to_module()`` output.

**Relative-path-based** (JS/TS, C/C++):
  source_literal is a relative path (e.g. ``./utils``, ``../models/user``)
  resolved from the importing file's directory with extension probing.

**Config-augmented** (Go → go.mod, Rust → Cargo.toml):
  Package declaration from tree-sitter is augmented with config file
  context to produce the full declared_module.

All resolution runs at **index time** and the result is stored in
``ImportFact.resolved_path``, making query-time matching trivial.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

import structlog

from coderecon.index.resolution.package_parsers import (
    _normalize_path,
    build_js_package_exports,
    parse_cargo_toml,
    parse_go_mod,
    resolve_go_module,
    resolve_rust_module,
)

log = structlog.get_logger(__name__)

# Type alias for the file reader callable
_ReadFileFn = Callable[[str], "str | None"]

# Config file discovery cache (Go, Rust declared_module augmentation)

class ConfigResolver:
    """Caches parsed config files for a repo and resolves module identities.
    Used during indexing to augment ``declared_module`` for Go and Rust files,
    and to build JS/TS package.json export maps.
    """
    def __init__(self, repo_root: str, file_paths: list[str]) -> None:
        self._repo_root = repo_root
        self._go_mods: dict[str, str] | None = None
        self._cargo_tomls: dict[str, str] | None = None
        self._js_exports: dict[str, str] | None = None
        self._file_paths = file_paths
    def _discover_go_mods(self, read_file: _ReadFileFn) -> dict[str, str]:
        """Find and parse all go.mod files."""
        if self._go_mods is not None:
            return self._go_mods
        self._go_mods = {}
        for fp in self._file_paths:
            if PurePosixPath(fp).name == "go.mod":
                text = read_file(fp)
                if text is not None:
                    mod = parse_go_mod(text)
                    if mod:
                        self._go_mods[fp] = mod
                        log.debug("go.mod: %s -> %s", fp, mod)
        return self._go_mods
    def _discover_cargo_tomls(self, read_file: _ReadFileFn) -> dict[str, str]:
        """Find and parse all Cargo.toml files."""
        if self._cargo_tomls is not None:
            return self._cargo_tomls
        self._cargo_tomls = {}
        for fp in self._file_paths:
            if PurePosixPath(fp).name == "Cargo.toml":
                text = read_file(fp)
                if text is not None:
                    crate = parse_cargo_toml(text)
                    if crate:
                        self._cargo_tomls[fp] = crate
                        log.debug("Cargo.toml: %s -> %s", fp, crate)
        return self._cargo_tomls
    def _find_nearest_config(
        self, file_path: str, configs: dict[str, str]
    ) -> tuple[str, str] | None:
        """Find the nearest config file by directory nesting."""
        file_dir = str(PurePosixPath(file_path).parent)
        best: tuple[str, str] | None = None
        best_depth = -1
        for cfg_path, value in configs.items():
            cfg_dir = str(PurePosixPath(cfg_path).parent)
            if cfg_dir == ".":
                cfg_dir = ""
            if not cfg_dir or file_dir == cfg_dir or file_dir.startswith(cfg_dir + "/"):
                depth = cfg_dir.count("/") + (1 if cfg_dir else 0)
                if depth > best_depth:
                    best = (cfg_path, value)
                    best_depth = depth
        return best
    def resolve(
        self,
        file_path: str,
        language: str | None,
        short_package: str | None,
        read_file: _ReadFileFn | None = None,
    ) -> str | None:
        """Resolve declared_module for Go and Rust files."""
        if language == "go" and read_file is not None:
            go_mods = self._discover_go_mods(read_file)
            nearest = self._find_nearest_config(file_path, go_mods)
            if nearest:
                cfg_path, module_root = nearest
                return resolve_go_module(file_path, short_package, cfg_path, module_root)
        elif language == "rust" and read_file is not None:
            cargo_tomls = self._discover_cargo_tomls(read_file)
            nearest = self._find_nearest_config(file_path, cargo_tomls)
            if nearest:
                cfg_path, crate_name = nearest
                return resolve_rust_module(file_path, cfg_path, crate_name)
        return None
    def resolve_js_exports(
        self,
        read_file: _ReadFileFn,
    ) -> dict[str, str]:
        """Build JS/TS bare-specifier → file-path map from package.json exports.
        Cached after first call.
        """
        if self._js_exports is not None:
            return self._js_exports
        self._js_exports = build_js_package_exports(
            self._file_paths,
            read_file,
        )
        return self._js_exports

# Import path resolver — resolves source_literal → file path

# JS/TS extensions to probe when resolving relative imports
_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts")
_JS_INDEX_NAMES = tuple(f"/index{ext}" for ext in _JS_EXTENSIONS)

# C/C++ extensions to probe
_C_EXTENSIONS = (".h", ".hpp", ".hxx", ".c", ".cpp", ".cxx", ".cc")

class ImportPathResolver:
    """Resolves import source_literal values to repo-relative file paths.
    Operates at index time on the full set of ExtractionResults, using:
    - File path index for extension probing (JS/TS, C/C++)
    - declared_module values for declaration-based matching
    - path_to_module output for Python
    Usage::
        resolver = ImportPathResolver(all_file_paths, declared_modules)
        resolved = resolver.resolve(source_literal, import_kind, importer_path)
    """
    def __init__(
        self,
        all_file_paths: list[str],
        declared_modules: dict[str, str],  # file_path -> declared_module
        js_package_exports: dict[str, str] | None = None,  # specifier -> file_path
    ) -> None:
        # Set of all known file paths for existence checks
        self._all_paths: set[str] = set(all_file_paths)
        # JS/TS bare specifier -> file path (from package.json exports)
        self._js_package_exports: dict[str, str] = js_package_exports or {}
        # declared_module -> list of file_paths (multiple files can share a module)
        self._module_to_paths: dict[str, list[str]] = {}
        for fp, mod in declared_modules.items():
            self._module_to_paths.setdefault(mod, []).append(fp)
        # Reverse mapping: file_path -> declared_module (for Rust self::/super::)
        self._path_to_module: dict[str, str] = declared_modules
        # Detect Rust crate prefix from declared_modules (first :: segment)
        self._rust_crate_prefix: str | None = None
        for mod in declared_modules.values():
            if "::" in mod:
                self._rust_crate_prefix = mod.split("::")[0]
                break
        # Python: path_to_module output -> file_path
        from coderecon.index.resolution.module_mapping import (
            path_to_module,
        )
        self._python_module_to_path: dict[str, str] = {}
        for fp in all_file_paths:
            py_mod = path_to_module(fp)
            if py_mod:
                self._python_module_to_path[py_mod] = fp
    def resolve(
        self,
        source_literal: str | None,
        import_kind: str,
        importer_path: str,
    ) -> str | None:
        """Resolve a single import's source_literal to a file path.
        Args:
            source_literal: The import source string (e.g. 'cats.effect.IO',
                './utils', 'os.path').
            import_kind: The import classification (e.g. 'python_import',
                'js_import', 'java_import', 'c_include').
            importer_path: The file that contains this import statement.
        Returns:
            Repo-relative file path, or None if unresolvable.
        """
        if not source_literal:
            return None
        # Dispatch by import_kind
        if import_kind in ("python_import", "python_from"):
            return self._resolve_python(source_literal, importer_path)
        elif import_kind in ("js_import", "js_require", "js_dynamic_import", "js_reexport"):
            return self._resolve_js(source_literal, importer_path)
        elif import_kind == "c_include":
            return self._resolve_c(source_literal, importer_path)
        elif import_kind == "lua_require":
            return self._resolve_lua(source_literal, importer_path)
        else:
            # All other languages: declaration-based resolution
            return self._resolve_declaration_based(source_literal, import_kind, importer_path)
    def _resolve_python(self, source_literal: str, importer_path: str) -> str | None:
        """Resolve Python dotted import to file path.
        Handles both absolute imports (e.g. 'attr._make') and relative
        imports (e.g. '._make', '..utils').  Relative imports start with
        one or more dots; we resolve them against the importer's package.
        """
        from coderecon.index.resolution.module_mapping import (
            module_to_candidate_paths,
            path_to_module,
        )
        resolved_literal = source_literal
        # Handle relative imports: leading dots indicate parent packages
        if source_literal.startswith("."):
            importer_module = path_to_module(importer_path)
            if importer_module:
                # Count the dots to determine how many levels up
                stripped = source_literal.lstrip(".")
                dot_count = len(source_literal) - len(stripped)
                # Split importer's module into parts.
                # For __init__.py, path_to_module returns the package name
                # (e.g. 'src.attr'), so the module IS the package.
                # For regular files, we drop the last part to get the package.
                parts = importer_module.split(".")
                is_init = importer_path.endswith("__init__.py")
                # __init__.py IS the package; regular files drop last part
                package_parts = parts if is_init else parts[:-1]
                # Go up (dot_count - 1) additional levels
                levels_up = dot_count - 1
                if levels_up > 0:
                    package_parts = (
                        package_parts[:-levels_up] if levels_up < len(package_parts) else []
                    )
                if package_parts:
                    if stripped:
                        resolved_literal = ".".join(package_parts) + "." + stripped
                    else:
                        resolved_literal = ".".join(package_parts)
                elif stripped:
                    resolved_literal = stripped
                else:
                    return None
            else:
                return None
        for candidate in module_to_candidate_paths(resolved_literal):
            if candidate in self._python_module_to_path:
                return self._python_module_to_path[candidate]
        return None
    def _resolve_js(self, source_literal: str, importer_path: str) -> str | None:
        """Resolve JS/TS import source to file path.
        Handles:
        - Relative: './utils' → probe extensions + /index variants
        - Bare specifiers with package.json exports: 'zod/v4' → mapped file
        - Bare specifiers (npm external): 'react' → skip
        - Extension remapping: './foo.js' → './foo.ts' (TypeScript convention)
        """
        if not source_literal.startswith("."):
            # Bare specifier — check package.json exports first
            if source_literal in self._js_package_exports:
                return self._js_package_exports[source_literal]
            # External package — cannot resolve to repo file
            return None
        importer_dir = str(PurePosixPath(importer_path).parent)
        raw = importer_dir + "/" + source_literal
        resolved = _normalize_path(raw)
        # 1. Exact match (already has extension)
        if resolved in self._all_paths:
            return resolved
        # 2. Extension remapping: TypeScript conventionally imports .ts files
        #    with .js extension (e.g. import './foo.js' → file is ./foo.ts).
        #    Strip known JS extensions and re-probe with all extensions.
        stem = resolved
        for js_ext in (".js", ".jsx", ".mjs"):
            if resolved.endswith(js_ext):
                stem = resolved[: -len(js_ext)]
                break
        # 3. Probe extensions (on extensionless or stripped stem)
        for ext in _JS_EXTENSIONS:
            candidate = stem + ext
            if candidate in self._all_paths:
                return candidate
        # 4. Probe as directory with index file
        for idx in _JS_INDEX_NAMES:
            candidate = resolved + idx
            if candidate in self._all_paths:
                return candidate
        return None
    def _resolve_lua(self, source_literal: str, importer_path: str) -> str | None:  # noqa: ARG002
        """Resolve Lua require() module to file path.
        Lua's require("foo.bar.baz") replaces dots with path separators
        and searches package.path for a matching .lua file or init.lua.
        Strategy:
        1. Replace dots with '/' to get a relative path
        2. Probe for path.lua and path/init.lua
        3. Also probe under common source directories (src/, lib/)
        """
        # Convert dot-separated module name to path
        rel_path = source_literal.replace(".", "/")
        # Probe candidates: direct, then under common source directories
        prefixes = ("", "src/", "lib/", "lua/")
        for prefix in prefixes:
            # Try as .lua file
            candidate = prefix + rel_path + ".lua"
            if candidate in self._all_paths:
                return candidate
            # Try as directory with init.lua
            candidate = prefix + rel_path + "/init.lua"
            if candidate in self._all_paths:
                return candidate
        return None
    def _resolve_c(self, source_literal: str, importer_path: str) -> str | None:
        """Resolve C/C++ #include to file path.
        Handles:
        - Relative to importer directory
        - Repo-root-relative
        - Common include directories: include/, src/, third_party/
        """
        importer_dir = str(PurePosixPath(importer_path).parent)
        resolved = _normalize_path(importer_dir + "/" + source_literal)
        # Exact match relative to importer
        if resolved in self._all_paths:
            return resolved
        # Try from repo root (for project-root-relative includes)
        if source_literal in self._all_paths:
            return source_literal
        # Probe common include directories
        for prefix in ("include", "src", "lib", "third_party"):
            candidate = prefix + "/" + source_literal
            if candidate in self._all_paths:
                return candidate
        return None
    def _resolve_declaration_based(
        self,
        source_literal: str,
        import_kind: str,
        importer_path: str,
    ) -> str | None:
        """Resolve declaration-based imports by matching against declared_module.
        Strategy:
        1. Normalize Rust relative paths (crate::, super::, self::)
        2. Exact match: source_literal == declared_module
        3. Prefix match: source_literal starts with declared_module + separator
           (import of a symbol within a declared module)
        4. For Ruby require_relative: resolve as relative path
        When multiple files share a declared_module, disambiguate by matching
        the remaining suffix of the source_literal against filename stems.
        The separator depends on the language:
        - Java/Kotlin/Scala/C#/PHP/Elixir/Haskell/Julia: '.'
        - Rust: '::'
        - Go: '/'
        - Ruby: '::' or '/'
        """
        # Ruby require_relative uses path resolution
        if import_kind == "ruby_require_relative":
            return self._resolve_ruby_relative(source_literal, importer_path)
        # Normalize Rust relative paths before resolution
        if import_kind == "rust_use":
            source_literal = self._normalize_rust_source(source_literal, importer_path)
        # Normalize PHP backslash separators to dots for declared_module matching
        if import_kind == "php_use":
            source_literal = source_literal.replace("\\", ".")
        # Exact declared_module match
        if source_literal in self._module_to_paths:
            paths = self._module_to_paths[source_literal]
            return self._pick_best_path(paths)
        # Determine separator for prefix matching
        sep = self._separator_for_kind(import_kind)
        # Prefix match: 'cats.effect.IO' should match declared_module 'cats.effect'
        # Walk from longest prefix to shortest
        parts = source_literal.split("::") if sep == "::" else source_literal.split(sep)
        for i in range(len(parts) - 1, 0, -1):
            prefix = "::".join(parts[:i]) if sep == "::" else sep.join(parts[:i])
            if prefix in self._module_to_paths:
                paths = self._module_to_paths[prefix]
                suffix_parts = parts[i:]
                return self._pick_best_path(paths, suffix_parts)
        return None
    def _pick_best_path(
        self,
        paths: list[str],
        suffix_parts: list[str] | None = None,
    ) -> str | None:
        """Pick the best file path from candidates.
        When no suffix_parts are given (exact module match), return the
        first candidate.  When suffix_parts ARE given, require a stem or
        subpath match — returning None if no match is found so that the
        post-batch resolve_all_imports pass can retry with the full DB.
        """
        if not paths:
            return None
        if not suffix_parts:
            return paths[0]
        # Try matching the last suffix part against the filename stem
        target = suffix_parts[-1].lower()
        for p in paths:
            stem = PurePosixPath(p).stem.lower()
            if stem == target:
                return p
        # Try matching all suffix parts joined as a subpath
        if len(suffix_parts) > 1:
            sub = "/".join(s.lower() for s in suffix_parts)
            for p in paths:
                if sub in p.lower():
                    return p
        # No stem/subpath match found — return None rather than a random
        # file.  During batch indexing the target file may not yet be known;
        # returning None lets resolve_all_imports fix it in the post pass.
        return None
    def _normalize_rust_source(self, source_literal: str, importer_path: str) -> str:
        """Normalize Rust relative paths to absolute crate-qualified paths.
        - ``crate::module`` -> ``my_crate::module``
        - ``self::item``    -> ``<current_module>::item``
        - ``super::item``   -> ``<parent_module>::item``
        """
        if source_literal.startswith("crate::"):
            if self._rust_crate_prefix:
                return self._rust_crate_prefix + source_literal[5:]  # crate -> prefix
            return source_literal
        if source_literal.startswith("self::") or source_literal.startswith("super::"):
            # Derive the importer's module from its declared_module or path
            importer_mod = self._path_to_module.get(importer_path)
            if not importer_mod:
                return source_literal
            current_parts = importer_mod.split("::")
            if source_literal.startswith("self::"):
                # self::X -> current_module::X
                return "::".join(current_parts) + source_literal[4:]  # self -> current
            else:
                # super::X -> parent_module::X
                if len(current_parts) > 1:
                    return "::".join(current_parts[:-1]) + source_literal[5:]  # super -> parent
                return source_literal
        return source_literal
    def _resolve_ruby_relative(self, source_literal: str, importer_path: str) -> str | None:
        """Resolve Ruby require_relative as a path."""
        importer_dir = str(PurePosixPath(importer_path).parent)
        resolved = _normalize_path(importer_dir + "/" + source_literal)
        if resolved in self._all_paths:
            return resolved
        candidate = resolved + ".rb"
        if candidate in self._all_paths:
            return candidate
        return None
    @staticmethod
    def _separator_for_kind(import_kind: str) -> str:
        """Return the module path separator for an import kind."""
        if import_kind in ("rust_use",):
            return "::"
        elif import_kind in ("go_import",) or import_kind in ("ruby_require",):
            return "/"
        else:
            # Java, Kotlin, Scala, C#, PHP, Elixir, Haskell, Julia, etc.
            return "."
