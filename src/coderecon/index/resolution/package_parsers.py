"""Standalone parsers for Go, Rust and JS/TS package config files.

Extracted from config_resolver to keep that module focused on the
``ConfigResolver`` and ``ImportPathResolver`` classes.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import PurePosixPath

import structlog

log = structlog.get_logger(__name__)

# Go: go.mod resolution

_GO_MOD_MODULE_RE = re.compile(r"^module\s+(\S+)", re.MULTILINE)

def parse_go_mod(go_mod_text: str) -> str | None:
    """Extract the module path from a go.mod file.
    >>> parse_go_mod('module github.com/user/repo\\n\\ngo 1.21\\n')
    'github.com/user/repo'
    """
    m = _GO_MOD_MODULE_RE.search(go_mod_text)
    return m.group(1) if m else None

def resolve_go_module(
    file_path: str,
    _short_package: str | None,
    go_mod_path: str,
    go_mod_module: str,
) -> str | None:
    """Resolve a Go file's full import path.
    Args:
        file_path: Relative path of the .go file (e.g. 'pkg/auth/token.go').
        _short_package: The ``package`` declaration (e.g. 'auth'). May be None.
        go_mod_path: Relative path to the go.mod file.
        go_mod_module: Module path from go.mod (e.g. 'github.com/user/repo').
    Returns:
        Full import path (e.g. 'github.com/user/repo/pkg/auth').
    """
    go_mod_dir = str(PurePosixPath(go_mod_path).parent)
    if go_mod_dir == ".":
        go_mod_dir = ""
    file_dir = str(PurePosixPath(file_path).parent)
    if go_mod_dir and file_dir.startswith(go_mod_dir + "/"):
        rel_dir = file_dir[len(go_mod_dir) + 1 :]
    elif go_mod_dir:
        return None
    else:
        rel_dir = file_dir
    if rel_dir and rel_dir != ".":
        return f"{go_mod_module}/{rel_dir}"
    return go_mod_module

# Rust: Cargo.toml resolution

_CARGO_NAME_RE = re.compile(r'^\[package\].*?^name\s*=\s*"([^"]+)"', re.MULTILINE | re.DOTALL)

def parse_cargo_toml(cargo_text: str) -> str | None:
    """Extract the crate name from a Cargo.toml file.
    >>> parse_cargo_toml('[package]\\nname = "my_crate"\\nversion = "0.1.0"')
    'my_crate'
    """
    m = _CARGO_NAME_RE.search(cargo_text)
    return m.group(1) if m else None

def resolve_rust_module(
    file_path: str,
    cargo_toml_path: str,
    crate_name: str,
) -> str | None:
    """Resolve a Rust file's module path.
    Returns:
        Crate-qualified module path (e.g. 'my_crate::auth::token').
    """
    cargo_dir = str(PurePosixPath(cargo_toml_path).parent)
    if cargo_dir == ".":
        cargo_dir = ""
    fp = PurePosixPath(file_path)
    file_dir = str(fp.parent)
    file_stem = fp.stem
    if cargo_dir and file_dir.startswith(cargo_dir + "/"):
        rel = file_dir[len(cargo_dir) + 1 :]
    elif cargo_dir:
        return None
    else:
        rel = file_dir
    if rel.startswith("src/"):
        rel = rel[4:]
    elif rel == "src":
        rel = ""
    parts = [crate_name]
    if rel:
        parts.extend(rel.split("/"))
    if file_stem not in ("lib", "main", "mod"):
        parts.append(file_stem)
    return "::".join(parts)

# JS/TS: package.json exports resolution

def _parse_export_target(value: object) -> str | None:
    """Extract the source file path from a package.json exports value.
    Handles:
    - String: ``"./src/index.ts"``
    - Conditional object: ``{"@zod/source": "./src/index.ts", "import": ...}``
      Prefers ``@*/source`` > ``types`` > ``import`` > ``require`` > ``default``
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in list(value.keys()):
            if key.endswith("/source") or key.startswith("@") and "source" in key:
                v = value[key]
                if isinstance(v, str):
                    return v
        for key in ("types", "import", "require", "default"):
            v = value.get(key)
            if isinstance(v, str):
                return v
    return None

def build_js_package_exports(
    file_paths: list[str],
    read_file: Callable[[str], str | None],
) -> dict[str, str]:
    """Build a bare-specifier → repo-relative file path map from package.json exports.
    Scans all ``package.json`` files in the repo with ``name`` and ``exports``
    fields.  For each export entry, maps ``<name>/<subpath>`` to the resolved
    repo-relative file path.
    Example::
        # packages/zod/package.json: name="zod", exports: {"./v4": {"import": "./src/v4/index.ts"}}
        # → {"zod/v4": "packages/zod/src/v4/index.ts"}
    Returns:
        Dict mapping bare import specifiers to repo-relative file paths.
    """
    result: dict[str, str] = {}
    for fp in file_paths:
        if PurePosixPath(fp).name != "package.json":
            continue
        text = read_file(fp)
        if not text:
            continue
        try:
            pkg = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            log.debug("package_json_parse_skip", path=fp, exc_info=True)
            continue
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        exports = pkg.get("exports")
        if not isinstance(name, str) or not isinstance(exports, dict):
            continue
        pkg_dir = str(PurePosixPath(fp).parent)
        if pkg_dir == ".":
            pkg_dir = ""
        for subpath, value in exports.items():
            if "*" in subpath:
                continue
            target = _parse_export_target(value)
            if not target or not isinstance(target, str):
                continue
            if not target.startswith("."):
                continue
            if subpath == ".":
                specifier = name
            elif subpath.startswith("./"):
                specifier = name + "/" + subpath[2:]
            else:
                specifier = name + "/" + subpath
            if pkg_dir:
                resolved = _normalize_path(pkg_dir + "/" + target[2:])
            else:
                resolved = _normalize_path(target[2:] if target.startswith("./") else target)
            actual = _resolve_export_target(resolved, set(file_paths))
            if actual:
                result[specifier] = actual
                log.debug("package.json export: %s -> %s", specifier, actual)
    return result

def _resolve_export_target(target: str, all_paths: set[str]) -> str | None:
    """Resolve a package.json export target to an actual file path.
    Handles .js → .ts remapping and extension probing.
    """
    if target in all_paths:
        return target
    for js_ext, ts_ext in ((".js", ".ts"), (".jsx", ".tsx"), (".mjs", ".mts")):
        if target.endswith(js_ext):
            ts_target = target[: -len(js_ext)] + ts_ext
            if ts_target in all_paths:
                return ts_target
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts"):
        candidate = target + ext
        if candidate in all_paths:
            return candidate
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        candidate = target + "/index" + ext
        if candidate in all_paths:
            return candidate
    return None

def _normalize_path(path: str) -> str:
    """Normalize a relative path (resolve . and ..).
    This function is designed for paths within a repository. Leading ".."
    segments that would escape the repo root are silently dropped.
    >>> _normalize_path('src/utils/../models/user')
    'src/models/user'
    >>> _normalize_path('src/./utils')
    'src/utils'
    >>> _normalize_path('../../outside')  # Drops leading ..
    'outside'
    """
    parts: list[str] = []
    for segment in path.replace("\\", "/").split("/"):
        if segment == "." or segment == "":
            continue
        elif segment == "..":
            if parts:
                parts.pop()
        else:
            parts.append(segment)
    return "/".join(parts)
