"""Cache-building helpers for ReferenceResolver.

Extracted from resolver.py to keep each file under 500 LOC.
These functions operate on a ReferenceResolver instance passed as the first
argument, populating its internal caches from DB data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel import col, select

from coderecon.core.languages import exportable_kinds_for_language, is_name_exported
from coderecon.index.models import (
    BindTargetKind,
    DefFact,
    File,
    ImportFact,
    LocalBindFact,
)

if TYPE_CHECKING:
    from coderecon.index._internal.indexing.resolver import ReferenceResolver


def build_scope_parents(resolver: ReferenceResolver, session: object) -> None:
    """Pre-cache scope_id -> parent_scope_id from scope_facts."""
    resolver._scope_parents = {}
    rows = session.execute(  # type: ignore[attr-defined]
        text("SELECT scope_id, parent_scope_id FROM scope_facts")
    ).fetchall()
    for scope_id, parent_scope_id in rows:
        resolver._scope_parents[scope_id] = parent_scope_id or 0


def build_bind_cache(
    resolver: ReferenceResolver,
    session: object,
    *,
    file_ids: list[int] | None = None,
) -> None:
    """Pre-cache (file_id, name) -> [(scope_id, target_kind, target_uid)]."""
    resolver._bind_cache = {}
    if file_ids:
        placeholders = ", ".join(str(fid) for fid in file_ids)
        rows = session.execute(  # type: ignore[attr-defined]
            text(
                f"SELECT file_id, name, scope_id, target_kind, target_uid "
                f"FROM local_bind_facts WHERE file_id IN ({placeholders})"
            )
        ).fetchall()
    else:
        rows = session.execute(  # type: ignore[attr-defined]
            text(
                "SELECT file_id, name, scope_id, target_kind, target_uid "
                "FROM local_bind_facts"
            )
        ).fetchall()
    for file_id, name, scope_id, target_kind, target_uid in rows:
        key = (file_id, name)
        resolver._bind_cache.setdefault(key, []).append(
            (scope_id or 0, target_kind, target_uid)
        )
        if name == "*" and target_kind == BindTargetKind.IMPORT.value:
            resolver._wildcard_imports.setdefault(file_id, []).append(target_uid)


def build_import_cache(resolver: ReferenceResolver, session: object) -> None:
    """Pre-cache import_uid -> (file_id, source_literal, imported_name, resolved_path)."""
    resolver._import_cache = {}
    rows = session.execute(  # type: ignore[attr-defined]
        text(
            "SELECT import_uid, file_id, source_literal, imported_name, resolved_path "
            "FROM import_facts"
        )
    ).fetchall()
    for import_uid, file_id, source_literal, imported_name, resolved_path in rows:
        resolver._import_cache[import_uid] = (
            file_id,
            source_literal or "",
            imported_name or "",
            resolved_path or "",
        )


def build_module_cache(resolver: ReferenceResolver, session: object) -> None:
    """Build mapping from module path to file_id, and file_id to path."""
    resolver._module_to_file = {}
    resolver._file_paths = {}
    resolver._path_to_file = {}
    stmt = select(File.id, File.path)
    if resolver._worktree_id is not None:
        stmt = stmt.where(File.worktree_id == resolver._worktree_id)
    files = session.exec(stmt).all()  # type: ignore[attr-defined]
    for file_id, path in files:
        if file_id is None:
            continue
        resolver._file_paths[file_id] = path
        resolver._path_to_file[path] = file_id
        module_path = path_to_module(path)
        if module_path:
            resolver._module_to_file[module_path] = file_id
    # Add Rust crate:: aliases for src:: paths
    crate_aliases: dict[str, int] = {}
    for mod_path, fid in resolver._module_to_file.items():
        if mod_path.startswith("src::"):
            crate_aliases[f"crate::{mod_path[5:]}"] = fid
    resolver._module_to_file.update(crate_aliases)


def build_export_cache(resolver: ReferenceResolver, session: object) -> None:
    """Build mapping from file_id to exported symbols.

    Includes:
    1. Direct definitions (DefFact) in the file
    2. Re-exports (import + expose at module level via LocalBindFact)
    """
    resolver._file_exports = {}
    # Build file_id -> language_family lookup for kind filtering
    file_lang: dict[int, str] = {}
    if resolver._worktree_id is not None:
        rows = session.execute(  # type: ignore[attr-defined]
            text("SELECT id, language_family FROM files WHERE worktree_id = :wt"),
            {"wt": resolver._worktree_id},
        ).fetchall()
    else:
        rows = session.execute(  # type: ignore[attr-defined]
            text("SELECT id, language_family FROM files")
        ).fetchall()
    for fid, lang in rows:
        if fid is not None and lang:
            file_lang[fid] = lang
    # Step 1: Get all top-level definitions, filtered by per-language
    # exportable kinds (defined in core.languages)
    stmt = select(DefFact)
    if resolver._worktree_id is not None:
        wt_file_ids = set(file_lang.keys())
        stmt = stmt.where(col(DefFact.file_id).in_(wt_file_ids))
    defs = session.exec(stmt).all()  # type: ignore[attr-defined]
    for d in defs:
        lang = file_lang.get(d.file_id, "")
        if d.kind not in exportable_kinds_for_language(lang):
            continue
        if d.file_id not in resolver._file_exports:
            resolver._file_exports[d.file_id] = {}
        # Per-language visibility check (replaces naive _ prefix heuristic)
        if is_name_exported(d.name, lang):
            resolver._file_exports[d.file_id][d.name] = d.def_uid
    # Step 2: Add re-exports (imports that are exposed at module level)
    # These are LocalBindFacts with target_kind='import' - common in __init__.py
    reexport_stmt = (
        select(LocalBindFact, ImportFact)
        .join(
            ImportFact,
            LocalBindFact.target_uid == ImportFact.import_uid,  # type: ignore[arg-type]
        )
        .where(
            LocalBindFact.target_kind == BindTargetKind.IMPORT.value,
        )
    )
    reexports = session.exec(reexport_stmt).all()  # type: ignore[attr-defined]
    for bind, imp in reexports:
        if not is_name_exported(bind.name, file_lang.get(bind.file_id, "")):
            continue
        # Find the actual definition in the source module
        # Pass importing file_id for relative import resolution
        source_file_id = (
            find_module_file(resolver, imp.source_literal, importing_file_id=imp.file_id)
            if imp.source_literal
            else None
        )
        if source_file_id is None:
            continue
        source_exports = resolver._file_exports.get(source_file_id, {})
        if bind.name == "*":
            # Wildcard re-export (from X import *) — merge all public
            # source exports into this file's exports.
            if not source_exports:
                continue
            if bind.file_id not in resolver._file_exports:
                resolver._file_exports[bind.file_id] = {}
            current = resolver._file_exports[bind.file_id]
            for name, def_uid in source_exports.items():
                if not name.startswith("_"):
                    current.setdefault(name, def_uid)
        else:
            # Named re-export: look up the def_uid from source module
            if imp.imported_name in source_exports:
                def_uid = source_exports[imp.imported_name]
                if bind.file_id not in resolver._file_exports:
                    resolver._file_exports[bind.file_id] = {}
                resolver._file_exports[bind.file_id][bind.name] = def_uid
    # Step 3: Propagate JS/TS re-exports (export * from, export { X } from).
    # Re-export ImportFacts with import_kind='js_reexport' forward exports
    # from one module through another (barrel files).  Iterate until stable
    # so chained barrel files propagate correctly.
    reexport_rows = session.execute(  # type: ignore[attr-defined]
        text(
            "SELECT file_id, imported_name, alias, source_literal, resolved_path "
            "FROM import_facts WHERE import_kind = 'js_reexport'"
        )
    ).fetchall()
    if reexport_rows:
        reexports_by_file: dict[int, list[tuple[str, str | None, str, str]]] = {}
        for fid, imp_name, alias, src_lit, res_path in reexport_rows:
            reexports_by_file.setdefault(fid, []).append(
                (imp_name or "", alias, src_lit or "", res_path or "")
            )
        # Converge iteratively — max depth = number of barrel files
        # (each iteration can propagate one more link in the chain).
        remaining_iters = len(reexports_by_file)
        changed = True
        while changed and remaining_iters > 0:
            changed = False
            remaining_iters -= 1
            for fid, reexport_list in reexports_by_file.items():
                if fid not in resolver._file_exports:
                    resolver._file_exports[fid] = {}
                current = resolver._file_exports[fid]
                for imp_name, alias, src_lit, res_path in reexport_list:
                    target_fid = (
                        resolver._path_to_file.get(res_path) if res_path else None
                    )
                    if target_fid is None:
                        target_fid = find_module_file(
                            resolver, src_lit, importing_file_id=fid
                        )
                    if target_fid is None:
                        continue
                    src_exports = resolver._file_exports.get(target_fid, {})
                    if imp_name == "*" and alias is None:
                        # export * from './foo' — forward all exports
                        for name, def_uid in src_exports.items():
                            if name not in current:
                                current[name] = def_uid
                                changed = True
                    elif imp_name == "*":
                        # export * as Y from './foo' — namespace binding,
                        # individual names not forwarded
                        pass
                    elif alias is not None:
                        # export { X as Y } from './foo'
                        if imp_name in src_exports and alias not in current:
                            current[alias] = src_exports[imp_name]
                            changed = True
                    else:
                        # export { X } from './foo'
                        if imp_name in src_exports and imp_name not in current:
                            current[imp_name] = src_exports[imp_name]
                            changed = True


def find_module_file(
    resolver: ReferenceResolver,
    source_literal: str,
    importing_file_id: int | None = None,
) -> int | None:
    """Find file_id for a module import path.

    Handles both absolute and relative imports across languages:
    - Python: ``from ..core.base_model import X`` (dot-relative)
    - JS/TS: ``import { X } from './foo'`` or ``'../foo'`` (path-relative)
    - Rust: ``use super::foo`` (super::-relative)
    """
    # Try resolving relative imports first (they can't match absolute cache)
    if importing_file_id is not None:
        resolved = _resolve_relative_source(resolver, source_literal, importing_file_id)
        if resolved is not None:
            return resolved
    # Direct match
    if source_literal in resolver._module_to_file:
        return resolver._module_to_file[source_literal]
    # Try common patterns:
    # 1. foo.bar -> foo/bar.py or foo/bar/__init__.py
    # 2. src.foo.bar -> also try for codebase with src/ prefix
    candidates = [
        source_literal,
        source_literal.replace(".", "/"),
        f"{source_literal.replace('.', '/')}/__init__",
        # Handle src/ prefix - imports like 'coderecon.foo' map to 'src/coderecon/foo.py'
        f"src.{source_literal}",
        f"src/{source_literal.replace('.', '/')}",
    ]
    for candidate in candidates:
        if candidate in resolver._module_to_file:
            return resolver._module_to_file[candidate]
    return None


def _resolve_relative_source(
    resolver: ReferenceResolver,
    source_literal: str,
    importing_file_id: int,
) -> int | None:
    """Resolve a relative import source_literal to a file_id.

    Supports:
    - Python dot-relative: ``.foo``, ``..core.bar``
    - JS/TS path-relative: ``./foo``, ``../bar/baz``
    - Rust super-relative: ``super::foo``, ``super::super::foo``
    """
    importing_path = resolver._file_paths.get(importing_file_id)
    if not importing_path:
        return None
    # ``import { X } from './foo'`` → source_literal = "./foo"
    # ``import { X } from '../bar/baz'`` → source_literal = "../bar/baz"
    # Must come first: "../foo" starts with "." but is JS, not Python.
    if source_literal.startswith("./") or source_literal.startswith("../"):
        return _resolve_js_relative(resolver, source_literal, importing_path)
    # ``from .foo import X`` → source_literal = ".foo"
    # ``from ..core.bar import X`` → source_literal = "..core.bar"
    if source_literal.startswith(".") and not source_literal.startswith("./"):
        return _resolve_python_relative(resolver, source_literal, importing_path)
    # ``use super::foo`` → source_literal = "super::foo"
    if source_literal.startswith("super::"):
        return _resolve_rust_relative(resolver, source_literal, importing_path)
    return None


def _resolve_python_relative(
    resolver: ReferenceResolver, source_literal: str, importing_path: str,
) -> int | None:
    """Resolve Python dot-relative import to file_id.

    ``from .foo import X`` in ``src/pkg/sub/mod.py`` →
    source_literal=".foo", base_dir="src/pkg/sub" →
    target module = "src.pkg.sub.foo"

    ``from ..core.bar import X`` in ``src/pkg/eval/mod.py`` →
    source_literal="..core.bar", base_dir="src/pkg/eval" →
    up 2 → "src/pkg" → target = "src.pkg.core.bar"
    """
    # Count leading dots and extract module suffix
    dots = 0
    for ch in source_literal:
        if ch == ".":
            dots += 1
        else:
            break
    module_suffix = source_literal[dots:]  # e.g., "core.bar" or ""
    # Get the package directory of the importing file
    # For src/pkg/eval/mod.py → "src/pkg/eval"
    base_dir = importing_path.rsplit("/", 1)[0] if "/" in importing_path else ""
    # Go up (dots - 1) additional directories. One dot = same package.
    levels_up = dots - 1
    for _ in range(levels_up):
        if "/" in base_dir:
            base_dir = base_dir.rsplit("/", 1)[0]
        else:
            # Can't go above root
            return None
    # Build absolute module path
    base_module = base_dir.replace("/", ".").replace("\\", ".")
    if module_suffix:
        abs_module = f"{base_module}.{module_suffix}" if base_module else module_suffix
    else:
        abs_module = base_module
    # Look up in module cache
    if abs_module in resolver._module_to_file:
        return resolver._module_to_file[abs_module]
    return None


def _resolve_js_relative(
    resolver: ReferenceResolver, source_literal: str, importing_path: str,
) -> int | None:
    """Resolve JS/TS path-relative import to file_id.

    ``import { X } from './foo'`` in ``src/components/bar.ts`` →
    target path = "src/components/foo" → try .ts, .js, /index.ts, etc.
    """
    base_dir = importing_path.rsplit("/", 1)[0] if "/" in importing_path else ""
    # Normalize the relative path
    parts = source_literal.split("/")
    resolved_parts = base_dir.split("/") if base_dir else []
    for part in parts:
        if part == ".":
            continue
        elif part == "..":
            if resolved_parts:
                resolved_parts.pop()
        else:
            resolved_parts.append(part)
    resolved_base = "/".join(resolved_parts)
    # Module cache uses dot-separated paths — convert
    resolved_module = resolved_base.replace("/", ".").replace("\\", ".")
    # Try direct match and common extensions
    for candidate in [
        resolved_module,
        f"{resolved_module}.index",  # ./foo → foo/index.ts
    ]:
        if candidate in resolver._module_to_file:
            return resolver._module_to_file[candidate]
    # JS/TS files may not be in the Python-centric module cache.
    # Also try path-based lookup against raw file paths.
    js_exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    for ext in js_exts:
        candidate_path = f"{resolved_base}{ext}"
        # Search _file_paths values
        for fid, fpath in resolver._file_paths.items():
            if fpath == candidate_path:
                return fid
    for ext in js_exts:
        candidate_path = f"{resolved_base}/index{ext}"
        for fid, fpath in resolver._file_paths.items():
            if fpath == candidate_path:
                return fid
    return None


def _resolve_rust_relative(
    resolver: ReferenceResolver, source_literal: str, importing_path: str,
) -> int | None:
    """Resolve Rust super:: relative import to file_id.

    ``use super::foo`` in ``src/bar/baz.rs`` →
    target module = "src.bar.foo"
    """
    # Split on :: and count super levels
    segments = source_literal.split("::")
    super_count = 0
    remainder: list[str] = []
    for seg in segments:
        if seg == "super":
            super_count += 1
        else:
            remainder.append(seg)
    # Get base directory (stripping the filename already moves us
    # to the parent module for normal files like baz.rs).
    base_dir = importing_path.rsplit("/", 1)[0] if "/" in importing_path else ""
    filename = importing_path.rsplit("/", 1)[-1] if "/" in importing_path else importing_path
    # For mod.rs / lib.rs / main.rs, the directory IS the module itself,
    # so super requires an additional level up.  For regular files like
    # baz.rs the directory is already the parent module, so the first
    # super is "free".
    stem = filename.rsplit(".", 1)[0]
    is_module_file = stem in ("mod", "lib", "main")
    ups = super_count if is_module_file else max(0, super_count - 1)
    for _ in range(ups):
        if "/" in base_dir:
            base_dir = base_dir.rsplit("/", 1)[0]
        else:
            return None
    # Build module path
    base_module = base_dir.replace("/", "::").replace("\\", "::")
    suffix = "::".join(remainder)
    if suffix:  # noqa: SIM108
        abs_module = f"{base_module}::{suffix}" if base_module else suffix
    else:
        abs_module = base_module
    if abs_module in resolver._module_to_file:
        return resolver._module_to_file[abs_module]
    return None


def path_to_module(path: str) -> str | None:
    """Convert file path to module path.

    Supports Python, JS/TS, and Rust files.
    """
    # Python: src/foo/bar.py -> src.foo.bar
    if path.endswith(".py"):
        module = path[:-3]
        if module.endswith("/__init__"):
            module = module[:-9]
        module = module.replace("/", ".").replace("\\", ".")
        module = module.lstrip(".")
        return module
    # JS/TS: src/foo/bar.ts -> src.foo.bar
    js_exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    for ext in js_exts:
        if path.endswith(ext):
            module = path[: -len(ext)]
            if module.endswith("/index"):
                module = module[:-6]
            module = module.replace("/", ".").replace("\\", ".")
            module = module.lstrip(".")
            return module
    # Rust: src/foo/bar.rs -> src::foo::bar (uses :: separator)
    if path.endswith(".rs"):
        module = path[:-3]
        if module.endswith("/mod") or module.endswith("/lib"):
            module = module[:-4]
        module = module.replace("/", "::").replace("\\", "::")
        module = module.lstrip(":")
        return module
    return None
