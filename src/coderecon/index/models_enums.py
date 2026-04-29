"""Enum definitions for the index schema.

See SPEC.md §7 for architecture.
"""

from enum import StrEnum

# ENUMS

class LanguageFamily(StrEnum):
    """Canonical language name identifiers.
    All languages get lexical indexing (full-text search).
    Languages with grammar != None also get structural indexing (symbols, refs).
    NOTE: These values MUST match the 'name' strings in core/languages.py.
    Run validate_language_families() to check for mismatches.
    """
    JAVASCRIPT = "javascript"  # JS, TS, JSX, TSX, Vue, Svelte
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    # JVM languages (split for correct grammar selection)
    JAVA = "java"
    KOTLIN = "kotlin"
    SCALA = "scala"
    GROOVY = "groovy"
    # .NET languages (split for correct grammar selection)
    CSHARP = "csharp"
    FSHARP = "fsharp"
    VBNET = "vbnet"
    RUBY = "ruby"
    PHP = "php"
    SWIFT = "swift"
    C_CPP = "c_cpp"  # C and C++ unified (header files are ambiguous)
    OBJC = "objc"  # Objective-C, Objective-C++
    MATLAB = "matlab"  # MATLAB/Octave
    ELIXIR = "elixir"
    ERLANG = "erlang"
    HASKELL = "haskell"
    OCAML = "ocaml"
    REASON = "reason"  # ReasonML
    CLOJURE = "clojure"
    ELM = "elm"
    SHELL = "shell"  # Bash, Zsh, Ksh (POSIX-compatible)
    FISH = "fish"  # Fish shell (separate - incompatible syntax)
    POWERSHELL = "powershell"
    LUA = "lua"
    PERL = "perl"
    R = "r"
    JULIA = "julia"
    ZIG = "zig"
    NIM = "nim"
    D = "d"
    ADA = "ada"
    FORTRAN = "fortran"
    PASCAL = "pascal"
    DART = "dart"
    GLEAM = "gleam"
    CRYSTAL = "crystal"
    VLANG = "vlang"  # V language
    ODIN = "odin"
    VERILOG = "verilog"  # Verilog, SystemVerilog
    VHDL = "vhdl"
    HTML = "html"
    XML = "xml"
    CSS = "css"  # CSS, SCSS, Less
    TERRAFORM = "terraform"
    HCL = "hcl"
    SQL = "sql"
    DOCKER = "docker"
    MARKDOWN = "markdown"
    RST = "rst"  # reStructuredText
    ASCIIDOC = "asciidoc"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    PROTOBUF = "protobuf"
    GRAPHQL = "graphql"
    NIX = "nix"
    MAKE = "make"
    CMAKE = "cmake"
    MESON = "meson"
    BAZEL = "bazel"  # Starlark
    JUST = "just"
    ASSEMBLY = "assembly"
    UNKNOWN = "unknown"  # Root fallback for files not claimed by any project
    @classmethod
    def code_families(cls) -> "frozenset[LanguageFamily]":
        """Return code families (programming languages)."""
        return frozenset(
            {
                # Mainstream
                cls.JAVASCRIPT,
                cls.PYTHON,
                cls.GO,
                cls.RUST,
                # JVM languages
                cls.JAVA,
                cls.KOTLIN,
                cls.SCALA,
                cls.GROOVY,
                # .NET languages
                cls.CSHARP,
                cls.FSHARP,
                cls.VBNET,
                cls.RUBY,
                cls.PHP,
                cls.SWIFT,
                cls.C_CPP,
                cls.OBJC,
                cls.MATLAB,
                # Functional
                cls.ELIXIR,
                cls.ERLANG,
                cls.HASKELL,
                cls.OCAML,
                cls.REASON,
                cls.CLOJURE,
                cls.ELM,
                # Scripting
                cls.SHELL,
                cls.FISH,
                cls.POWERSHELL,
                cls.LUA,
                cls.PERL,
                cls.R,
                cls.JULIA,
                # Systems
                cls.ZIG,
                cls.NIM,
                cls.D,
                cls.ADA,
                cls.FORTRAN,
                cls.PASCAL,
                # Other
                cls.DART,
                cls.GLEAM,
                cls.CRYSTAL,
                cls.VLANG,
                cls.ODIN,
                # Hardware
                cls.VERILOG,
                cls.VHDL,
                cls.ASSEMBLY,
            }
        )
    @classmethod
    def data_families(cls) -> "frozenset[LanguageFamily]":
        """Return data/config families."""
        return frozenset(
            {
                cls.TERRAFORM,
                cls.HCL,
                cls.SQL,
                cls.DOCKER,
                cls.MARKDOWN,
                cls.RST,
                cls.ASCIIDOC,
                cls.JSON,
                cls.YAML,
                cls.TOML,
                cls.PROTOBUF,
                cls.GRAPHQL,
                cls.NIX,
                cls.HTML,
                cls.XML,
                cls.CSS,
                cls.MAKE,
                cls.CMAKE,
                cls.MESON,
                cls.BAZEL,
                cls.JUST,
            }
        )
    @property
    def is_code(self) -> bool:
        """True if this is a code name."""
        return self in self.code_families()
    @property
    def is_data(self) -> bool:
        """True if this is a data name."""
        return self in self.data_families()
class Freshness(StrEnum):
    """Index currency state."""
    CLEAN = "clean"
    DIRTY = "dirty"
    STALE = "stale"
    PENDING_CHECK = "pending_check"
    UNINDEXED = "unindexed"
class Certainty(StrEnum):
    """Confidence level for facts."""
    CERTAIN = "certain"
    UNCERTAIN = "uncertain"
class RefTier(StrEnum):
    """Reference tier classification (assigned at index time, never upgraded at query time)."""
    PROVEN = "proven"  # Same-file lexical bind with LocalBindFact certainty=CERTAIN
    STRONG = "strong"  # Cross-file with explicit ImportFact + ExportSurface trace
    ANCHORED = "anchored"  # Ambiguous but grouped in AnchorGroup
    SEMANTIC = "semantic"  # Resolved via SPLADE+CE semantic matching
    UNKNOWN = "unknown"  # Cannot classify
class Role(StrEnum):
    """Reference role in source code."""
    DEFINITION = "definition"
    REFERENCE = "reference"
    IMPORT = "import"
    EXPORT = "export"
class ScopeKind(StrEnum):
    """Lexical scope kind."""
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    BLOCK = "block"
    COMPREHENSION = "comprehension"
    LAMBDA = "lambda"
class BindTargetKind(StrEnum):
    """Target kind for LocalBindFact."""
    DEF = "def"  # Bound to a DefFact
    IMPORT = "import"  # Bound to an ImportFact
    UNKNOWN = "unknown"  # Cannot determine
class BindReasonCode(StrEnum):
    """Reason for binding classification."""
    PARAM = "param"  # Function parameter
    LOCAL_ASSIGN = "local_assign"  # Assignment target
    DEF_IN_SCOPE = "def_in_scope"  # Definition in enclosing scope
    IMPORT_ALIAS = "import_alias"  # Import alias
    FOR_TARGET = "for_target"  # For loop target
    WITH_AS = "with_as"  # With statement alias
    EXCEPT_AS = "except_as"  # Exception handler alias
class ImportKind(StrEnum):
    """Import statement kind."""
    PYTHON_IMPORT = "python_import"  # import foo
    PYTHON_FROM = "python_from"  # from foo import bar
    JS_IMPORT = "js_import"  # import { foo } from 'bar'
    JS_REQUIRE = "js_require"  # const foo = require('bar')
    TS_IMPORT_TYPE = "ts_import_type"  # import type { Foo } from 'bar'
    GO_IMPORT = "go_import"  # import "foo"
    RUST_USE = "rust_use"  # use foo::bar
    CSHARP_USING = "csharp_using"  # using Namespace;
    CSHARP_USING_STATIC = "csharp_using_static"  # using static Namespace.Type;
    # Tier 1 languages - high impact
    JAVA_IMPORT = "java_import"  # import com.foo.Bar;
    JAVA_IMPORT_STATIC = "java_import_static"  # import static com.foo.Bar.baz;
    # Tier 2 languages - commonly encountered
    KOTLIN_IMPORT = "kotlin_import"  # import com.foo.Bar
    RUBY_REQUIRE = "ruby_require"  # require 'foo'
    RUBY_REQUIRE_RELATIVE = "ruby_require_relative"  # require_relative 'foo'
    PHP_USE = "php_use"  # use Namespace\Class;
    SWIFT_IMPORT = "swift_import"  # import Foundation
    # Tier 3 languages - niche
    SCALA_IMPORT = "scala_import"  # import com.foo.{Bar, Baz => B}
    ELIXIR_IMPORT = "elixir_import"  # import Module / alias Module / use Module
    HASKELL_IMPORT = "haskell_import"  # import Module
    OCAML_OPEN = "ocaml_open"  # open Module
    LUA_REQUIRE = "lua_require"  # require("module")
    JULIA_USING = "julia_using"  # using Module / import Module
    # Special - textual inclusion
    C_INCLUDE = "c_include"  # #include <header> / #include "header"
class ExportThunkMode(StrEnum):
    """Re-export mode for ExportThunk."""
    REEXPORT_ALL = "reexport_all"  # export * from 'module'
    EXPLICIT_NAMES = "explicit_names"  # export { a, b } from 'module'
    ALIAS_MAP = "alias_map"  # export { a as x, b as y } from 'module'
class DynamicAccessPattern(StrEnum):
    """Dynamic access pattern types (telemetry only)."""
    BRACKET_ACCESS = "bracket_access"  # obj[key]
    GETATTR = "getattr"  # getattr(obj, name)
    REFLECT = "reflect"  # Reflect.get(obj, name)
    EVAL = "eval"  # eval(), exec()
    IMPORT_MODULE = "import_module"  # importlib.import_module(var)
class ProbeStatus(StrEnum):
    """Context probe status."""
    PENDING = "pending"
    VALID = "valid"
    FAILED = "failed"
    EMPTY = "empty"
    DETACHED = "detached"
class MarkerTier(StrEnum):
    """Marker tier for context discovery hierarchy."""
    WORKSPACE = "workspace"
    PACKAGE = "package"
class ResolutionMethod(StrEnum):
    """How a reference was resolved to its target."""
    TYPE_TRACED = "type_traced"  # Via type annotation chain
    IMPORT_TRACED = "import_traced"  # Via import resolution
    INTERFACE_MATCHED = "interface_matched"  # Via interface/trait impl
    SHAPE_MATCHED = "shape_matched"  # Via duck-type shape inference
    SEMANTIC = "semantic"  # Via SPLADE+CE semantic similarity
    LEXICAL = "lexical"  # Lexical search only (lowest confidence)
    UNRESOLVED = "unresolved"  # Could not resolve

# TIER 1 FACT TABLES (per SPEC.md §7.3)

