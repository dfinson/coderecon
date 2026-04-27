"""Unified LanguagePack — single source of truth for all tree-sitter config.

Every language that CodeRecon supports has exactly ONE LanguagePack that
consolidates ALL config:
- Grammar install metadata (package, module, version, loader function)
- File extension / filename detection
- Symbol extraction queries (S-expression patterns + SymbolPattern mappings)
- Scope types (data-driven scope walker config)
- SEM_FACTS queries (body-evidence)
- Type extraction config (type annotations, type members, member accesses,
  interface implementations)
- Module declaration handler name
- Dynamic access handler name

The PACKS registry is the canonical lookup: ``PACKS["python"]``.
"""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    LanguagePack,
)
from coderecon.index._internal.parsing.packs_config import (
    ADA_PACK,
    CSS_PACK,
    DOCKERFILE_PACK,
    FORTRAN_PACK,
    GRAPHQL_PACK,
    HCL_PACK,
    HTML_PACK,
    JSON_PACK,
    MAKEFILE_PACK,
    MARKDOWN_PACK,
    ODIN_PACK,
    REGEX_PACK,
    REQUIREMENTS_PACK,
    SQL_PACK,
    TOML_PACK,
    VERILOG_PACK,
    XML_PACK,
    YAML_PACK,
)
from coderecon.index._internal.parsing.packs_functional import (
    ELIXIR_PACK,
    HASKELL_PACK,
    OCAML_PACK,
    SCALA_PACK,
)
from coderecon.index._internal.parsing.packs_jvm import (
    CSHARP_PACK,
    JAVA_PACK,
    KOTLIN_PACK,
)
from coderecon.index._internal.parsing.packs_mainstream import (
    JAVASCRIPT_PACK,
    PYTHON_PACK,
    TSX_PACK,
    TYPESCRIPT_PACK,
)
from coderecon.index._internal.parsing.packs_misc import (
    BASH_PACK,
    JULIA_PACK,
    LUA_PACK,
    ZIG_PACK,
)
from coderecon.index._internal.parsing.packs_native import (
    C_PACK,
    CPP_PACK,
)
from coderecon.index._internal.parsing.packs_scripting import (
    PHP_PACK,
    RUBY_PACK,
    SWIFT_PACK,
)
from coderecon.index._internal.parsing.packs_systems import (
    GO_PACK,
    RUST_PACK,
)

# Canonical registries

_ALL_PACKS: tuple[LanguagePack, ...] = (
    PYTHON_PACK,
    JAVASCRIPT_PACK,
    TYPESCRIPT_PACK,
    TSX_PACK,
    GO_PACK,
    RUST_PACK,
    JAVA_PACK,
    KOTLIN_PACK,
    SCALA_PACK,
    CSHARP_PACK,
    CPP_PACK,
    C_PACK,
    RUBY_PACK,
    PHP_PACK,
    SWIFT_PACK,
    ELIXIR_PACK,
    HASKELL_PACK,
    OCAML_PACK,
    BASH_PACK,
    LUA_PACK,
    JULIA_PACK,
    ZIG_PACK,
    ADA_PACK,
    FORTRAN_PACK,
    ODIN_PACK,
    HTML_PACK,
    CSS_PACK,
    XML_PACK,
    VERILOG_PACK,
    JSON_PACK,
    YAML_PACK,
    TOML_PACK,
    HCL_PACK,
    SQL_PACK,
    GRAPHQL_PACK,
    MARKDOWN_PACK,
    MAKEFILE_PACK,
    DOCKERFILE_PACK,
    REGEX_PACK,
    REQUIREMENTS_PACK,
)

# name -> Pack
PACKS: dict[str, LanguagePack] = {pack.name: pack for pack in _ALL_PACKS}
PACKS["shell"] = BASH_PACK
PACKS["terraform"] = HCL_PACK
PACKS["make"] = MAKEFILE_PACK
PACKS["c_sharp"] = CSHARP_PACK

# Extension -> Pack
_EXT_TO_PACK: dict[str, LanguagePack] = {}
for _pack in _ALL_PACKS:
    for _ext in _pack.extensions:
        _EXT_TO_PACK[_ext] = _pack

# Filename -> Pack
_FILENAME_TO_PACK: dict[str, LanguagePack] = {}
for _pack in _ALL_PACKS:
    for _fn in _pack.filenames:
        _FILENAME_TO_PACK[_fn] = _pack

# Public API

def get_pack_for_ext(ext: str) -> LanguagePack | None:
    """Get a LanguagePack for a file extension (without leading dot)."""
    return _EXT_TO_PACK.get(ext.lower())

def get_pack_for_filename(filename: str) -> LanguagePack | None:
    """Get a LanguagePack for a filename (case-insensitive)."""
    name_lower = filename.lower()
    pack = _FILENAME_TO_PACK.get(name_lower)
    if pack is not None:
        return pack
    if name_lower.startswith("dockerfile"):
        return DOCKERFILE_PACK
    return None

def get_pack(name: str) -> LanguagePack | None:
    """Get a LanguagePack by language name."""
    return PACKS.get(name)

# Re-exports for backward compatibility — all classes and pack constants
# remain importable from this module.
from coderecon.index._internal.parsing.packs_base import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_config import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_functional import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_jvm import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_mainstream import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_misc import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_native import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_scripting import *  # noqa: E402, F401, F403
from coderecon.index._internal.parsing.packs_systems import *  # noqa: E402, F401, F403
