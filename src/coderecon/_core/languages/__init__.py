"""Canonical language definitions and lookup tables.

See languages_util.py for detection functions, languages_testing.py
for test pairing, languages_niche.py for niche language data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coderecon.index.models import LanguageFamily
def _get_language_family() -> type[LanguageFamily]:
    from coderecon.index.models import LanguageFamily
    return LanguageFamily

@dataclass(frozen=True, slots=True)
class Language:
    """Canonical definition for a language name.
    Attributes:
        name: Unique identifier (lowercase, e.g., "python", "javascript")
        extensions: File extensions including dot (e.g., ".py", ".js")
        filenames: Special filenames to detect (lowercase, EXACT match only)
        markers_workspace: Files indicating workspace root (EXACT filenames, lowercase)
        markers_package: Files indicating package/module root (EXACT filenames, lowercase)
        grammar: Tree-sitter grammar name, or None if no grammar available
        test_patterns: Glob patterns for test files
        ambient: If True, index files even without project markers
        priority: Higher = preferred when extension is ambiguous (default 50)
    """
    name: str
    extensions: frozenset[str]
    filenames: frozenset[str] = field(default_factory=frozenset)
    markers_workspace: tuple[str, ...] = ()
    markers_package: tuple[str, ...] = ()
    grammar: str | None = None
    test_patterns: tuple[str, ...] = ()
    exportable_kinds: frozenset[str] = field(default_factory=lambda: frozenset({"function", "class"}))
    ambient: bool = False
    priority: int = 50

# Language Definitions
# RULES:
# 1. All filenames/markers MUST be lowercase (exact filenames, no globs/wildcards)
# 2. Extensions are case-insensitive (normalized to lowercase during lookup)
# 3. Priority determines winner for ambiguous extensions (higher wins)
# 4. Grammar must be a real tree-sitter grammar name or None

_COMMON_LANGUAGES: tuple[Language, ...] = (
    # Tier 1: Most common languages
    Language(
        name="python",
        extensions=frozenset({".py", ".pyi", ".pyw", ".pyx", ".pxd", ".pxi"}),
        filenames=frozenset({"pipfile", "setup.py", "pyproject.toml"}),
        markers_workspace=("uv.lock", "poetry.lock", "pipfile.lock", "pdm.lock"),
        markers_package=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile"),
        grammar="python",
        test_patterns=("test_*.py", "*_test.py"),
        priority=80,
    ),
    Language(
        name="javascript",
        extensions=frozenset(
            {
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
                ".mjs",
                ".cjs",
                ".mts",
                ".cts",
                ".vue",
                ".svelte",
                ".astro",
            }
        ),
        filenames=frozenset({"package.json", "deno.json", "tsconfig.json", "jsconfig.json"}),
        markers_workspace=("pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json"),
        markers_package=("package.json", "deno.json", "tsconfig.json"),
        grammar="typescript",  # TypeScript grammar handles JS/TS/JSX/TSX
        exportable_kinds=frozenset({"function", "class", "interface", "type_alias", "enum"}),
        test_patterns=(
            "*.test.js",
            "*.test.ts",
            "*.spec.js",
            "*.spec.ts",
            "*.test.jsx",
            "*.test.tsx",
            "*.spec.jsx",
            "*.spec.tsx",
            # Directory-based conventions (mocha, etc.)
            # fnmatch * matches across /, so test/*.js covers nested too.
            # Both prefixed (*/test/) and bare (test/) variants are needed
            # because fnmatch requires matching the entire string.
            "test/*.js",
            "*/test/*.js",
            "test/*.ts",
            "*/test/*.ts",
            "__tests__/*.js",
            "*/__tests__/*.js",
            "__tests__/*.ts",
            "*/__tests__/*.ts",
        ),
        priority=80,
    ),
    Language(
        name="go",
        extensions=frozenset({".go"}),
        filenames=frozenset({"go.mod", "go.sum"}),
        markers_workspace=("go.work",),
        markers_package=("go.mod",),
        grammar="go",
        test_patterns=("*_test.go",),
        exportable_kinds=frozenset({"function", "type", "interface"}),
        priority=80,
    ),
    Language(
        name="rust",
        extensions=frozenset({".rs"}),
        filenames=frozenset({"cargo.toml", "cargo.lock"}),
        # Note: Cargo.toml with [workspace] section is detected by scanner logic
        markers_workspace=(),
        markers_package=("cargo.toml",),
        grammar="rust",
        test_patterns=("test_*.rs", "*_test.rs", "tests/*.rs"),
        exportable_kinds=frozenset(
            {"function", "struct", "enum", "trait", "type_alias", "constant", "variable", "module"}
        ),
        priority=80,
    ),
    # JVM Languages (each has its own grammar)
    Language(
        name="java",
        extensions=frozenset({".java"}),
        filenames=frozenset({"pom.xml"}),
        markers_workspace=("settings.gradle", "settings.gradle.kts"),
        markers_package=("pom.xml", "build.gradle", "build.gradle.kts"),
        grammar="java",
        test_patterns=(
            "*Test.java",
            "Test*.java",
            # Maven/Gradle convention: anything under src/test/
            "src/test/*.java",
            "*/src/test/*.java",
        ),
        exportable_kinds=frozenset({"class", "interface", "enum", "record"}),
        priority=80,
    ),
    Language(
        name="kotlin",
        extensions=frozenset({".kt", ".kts"}),
        filenames=frozenset({"build.gradle.kts"}),
        markers_workspace=("settings.gradle.kts",),
        markers_package=("build.gradle.kts",),
        grammar="kotlin",
        test_patterns=(
            "*Test.kt",
            "Test*.kt",
            # Gradle convention: anything under src/test/ or src/*Test/
            "src/test/*.kt",
            "*/src/test/*.kt",
            "src/*Test/*.kt",
            "*/src/*Test/*.kt",
        ),
        exportable_kinds=frozenset({"function", "class", "object"}),
        priority=75,
    ),
    Language(
        name="scala",
        extensions=frozenset({".scala", ".sc"}),
        filenames=frozenset({"build.sbt"}),
        markers_workspace=(),
        markers_package=("build.sbt",),
        grammar="scala",
        test_patterns=(
            "*Spec.scala",
            "*Test.scala",
            "*Suite.scala",
            # SBT convention: anything under src/test/
            "src/test/*.scala",
            "*/src/test/*.scala",
        ),
        exportable_kinds=frozenset({"function", "class", "object", "trait", "val", "var"}),
        priority=75,
    ),
    Language(
        name="groovy",
        extensions=frozenset({".groovy", ".gradle"}),
        filenames=frozenset({"build.gradle", "settings.gradle"}),
        markers_workspace=("settings.gradle",),
        markers_package=("build.gradle",),
        grammar="groovy",
        test_patterns=("*Test.groovy", "*Spec.groovy"),
        priority=70,
    ),
    # .NET Languages (each has its own grammar)
    Language(
        name="csharp",
        extensions=frozenset({".cs"}),
        filenames=frozenset(),
        # Note: .sln and .csproj detection is done by scanner which knows file extensions
        markers_workspace=(),
        markers_package=(),
        grammar="c_sharp",
        test_patterns=("*Tests.cs", "*Test.cs"),
        exportable_kinds=frozenset({"class", "interface", "struct", "enum", "record", "delegate"}),
        priority=80,
    ),
    Language(
        name="fsharp",
        extensions=frozenset({".fs", ".fsx", ".fsi"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="fsharp",
        test_patterns=("*Tests.fs", "*Test.fs"),
        priority=75,
    ),
    Language(
        name="vbnet",
        extensions=frozenset({".vb"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="visual_basic",
        test_patterns=("*Tests.vb", "*Test.vb"),
        priority=70,
    ),
    # C/C++ (unified as c_cpp since .h is genuinely ambiguous)
    Language(
        name="c_cpp",
        extensions=frozenset({".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".hh", ".ino"}),
        filenames=frozenset({"cmakelists.txt", "meson.build"}),
        markers_workspace=(),
        markers_package=("cmakelists.txt", "meson.build", "compile_commands.json"),
        grammar="cpp",  # C++ grammar handles both C and C++
        test_patterns=(
            "test_*.cpp",
            "*_test.cpp",
            "test_*.c",
            "*_test.c",
            "tests/*.cpp",
            "tests/*.c",
        ),
        exportable_kinds=frozenset({"function", "class", "struct", "enum", "type_alias"}),
        priority=60,
    ),
    # Objective-C (separate; .m is AMBIGUOUS with MATLAB)
    Language(
        name="objc",
        extensions=frozenset({".m", ".mm"}),
        filenames=frozenset(),
        # Xcode project markers help disambiguate .m files
        markers_workspace=(),
        markers_package=("podfile", "podfile.lock", "package.swift"),
        grammar="objc",
        priority=60,  # Higher than MATLAB for .m
    ),
    # MATLAB/Octave (.m is AMBIGUOUS with Objective-C)
    Language(
        name="matlab",
        extensions=frozenset({".m", ".mlx"}),  # .m is ambiguous; .mlx is MATLAB-only
        filenames=frozenset({"startup.m"}),  # MATLAB-specific startup file
        markers_workspace=(),
        # MATLAB project markers help disambiguate .m files
        markers_package=("matlab.project",),
        grammar=None,  # No tree-sitter-matlab in common registries
        priority=50,  # Lower than objc for .m
    ),
    # Other mainstream languages
    Language(
        name="ruby",
        extensions=frozenset({".rb", ".rake", ".gemspec", ".erb"}),
        filenames=frozenset({"gemfile", "rakefile", "vagrantfile"}),
        markers_workspace=("gemfile.lock",),
        markers_package=("gemfile",),
        grammar="ruby",
        test_patterns=("*_spec.rb", "*_test.rb", "spec_*.rb"),
        exportable_kinds=frozenset({"function", "class", "module"}),
        priority=70,
    ),
    Language(
        name="php",
        extensions=frozenset({".php", ".phtml"}),
        filenames=frozenset(),
        markers_workspace=("composer.lock",),
        markers_package=("composer.json",),
        grammar="php",
        test_patterns=("*Test.php",),
        exportable_kinds=frozenset({"function", "class", "interface", "trait", "enum"}),
        priority=70,
    ),
    Language(
        name="swift",
        extensions=frozenset({".swift"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=("package.swift",),
        grammar="swift",
        test_patterns=("*Tests.swift",),
        exportable_kinds=frozenset({"class", "struct", "enum", "protocol", "function"}),
        priority=70,
    ),
    # Functional languages
    Language(
        name="elixir",
        extensions=frozenset({".ex", ".exs", ".eex", ".heex"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=("mix.exs",),
        grammar="elixir",
        test_patterns=("*_test.exs",),
        exportable_kinds=frozenset({"module", "function", "macro", "protocol", "struct"}),
        priority=70,
    ),
    Language(
        name="erlang",
        extensions=frozenset({".erl", ".hrl"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=("rebar.config",),
        grammar="erlang",
        priority=60,
    ),
    Language(
        name="haskell",
        extensions=frozenset({".hs", ".lhs", ".cabal"}),
        filenames=frozenset({"stack.yaml", "package.yaml"}),
        markers_workspace=(),
        markers_package=("stack.yaml", "package.yaml"),
        grammar="haskell",
        test_patterns=("*Spec.hs", "*Test.hs", "test/*.hs", "tests/*.hs"),
        exportable_kinds=frozenset({"function", "type_alias", "data", "newtype", "type_class"}),
        priority=70,
    ),
    Language(
        name="ocaml",
        extensions=frozenset({".ml", ".mli", ".mll", ".mly"}),
        filenames=frozenset({"dune-project", "dune"}),
        markers_workspace=(),
        markers_package=("dune-project", "dune"),
        grammar="ocaml",
        test_patterns=("test_*.ml", "*_test.ml", "test/*.ml", "tests/*.ml"),
        exportable_kinds=frozenset({"function", "variable", "type", "module", "module_type"}),
        priority=70,
    ),
    Language(
        name="reason",
        extensions=frozenset({".re", ".rei"}),
        filenames=frozenset({"bsconfig.json"}),
        markers_workspace=(),
        markers_package=("bsconfig.json",),
        grammar="reason",
        priority=60,
    ),
    Language(
        name="clojure",
        extensions=frozenset({".clj", ".cljs", ".cljc", ".edn"}),
        filenames=frozenset({"project.clj", "deps.edn"}),
        markers_workspace=(),
        markers_package=("project.clj", "deps.edn"),
        grammar="clojure",
        test_patterns=("*_test.clj",),
        priority=70,
    ),
    Language(
        name="elm",
        extensions=frozenset({".elm"}),
        filenames=frozenset({"elm.json"}),
        markers_workspace=(),
        markers_package=("elm.json",),
        grammar="elm",
        priority=70,
    ),
    # Scripting languages
    # Shell: bash/zsh/ksh (POSIX-compatible shells)
    Language(
        name="shell",
        extensions=frozenset({".sh", ".bash", ".zsh", ".ksh"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="bash",
        priority=50,
    ),
    # Fish shell is SEPARATE (incompatible syntax with bash)
    Language(
        name="fish",
        extensions=frozenset({".fish"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="fish",
        priority=50,
    ),
    # PowerShell is separate (completely different syntax)
    Language(
        name="powershell",
        extensions=frozenset({".ps1", ".psm1", ".psd1"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="powershell",
        priority=60,
    ),
    Language(
        name="lua",
        extensions=frozenset({".lua", ".luau"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="lua",
        test_patterns=("*_spec.lua", "*_test.lua", "spec/*.lua", "tests/*.lua", "test/*.lua"),
        exportable_kinds=frozenset({"function", "variable"}),
        priority=60,
    ),
    Language(
        name="perl",
        extensions=frozenset({".pl", ".pm", ".pod", ".t"}),
        filenames=frozenset({"makefile.pl", "build.pl", "cpanfile"}),
        markers_workspace=(),
        markers_package=("makefile.pl", "build.pl", "cpanfile"),
        grammar="perl",
        test_patterns=("*.t",),
        priority=50,
    ),
    Language(
        name="r",
        extensions=frozenset({".r", ".rmd"}),
        filenames=frozenset({"description"}),  # R package DESCRIPTION file
        markers_workspace=(),
        markers_package=("description",),
        grammar="r",
        priority=50,
    ),
    Language(
        name="julia",
        extensions=frozenset({".jl"}),
        filenames=frozenset({"project.toml"}),
        markers_workspace=(),
        markers_package=("project.toml",),
        grammar="julia",
        test_patterns=("runtests.jl", "test_*.jl", "*_test.jl", "test/*.jl", "tests/*.jl"),
        exportable_kinds=frozenset({"function", "macro", "struct", "module", "abstract_type", "constant"}),
        priority=70,
    ),
    # Systems languages
    Language(
        name="zig",
        extensions=frozenset({".zig"}),
        filenames=frozenset({"build.zig"}),
        markers_workspace=(),
        markers_package=("build.zig",),
        grammar="zig",
        exportable_kinds=frozenset({"function", "variable"}),
        priority=70,
    ),
)

from coderecon._core.languages._niche import _NICHE_LANGUAGES  # noqa: E402

ALL_LANGUAGES: tuple[Language, ...] = _COMMON_LANGUAGES + _NICHE_LANGUAGES

# ── Re-exports for backwards compatibility ──────────────────────
from coderecon._core.languages._util import (  # noqa: E402, F401
    AMBIGUOUS_EXTENSIONS,
    AMBIENT_NAMES,
    CONSTANT_KINDS,
    CONTAINER_KINDS,
    EXTENSION_TO_NAME,
    EXTENSION_TO_NAMES,
    FILENAME_TO_NAME,
    LANGUAGES_BY_NAME,
    TYPE_KINDS,
    _COMPOUND_SUFFIXES,
    build_include_specs,
    build_marker_definitions,
    detect_language_family,
    detect_language_family_enum,
    exportable_kinds_for_language,
    get_all_indexable_extensions,
    get_all_indexable_filenames,
    get_families_for_extension,
    get_grammar_name,
    get_include_globs,
    get_markers,
    has_grammar,
    is_ambiguous_extension,
    is_name_exported,
    validate_language_families,
    validate_markers_are_exact_filenames,
)
from coderecon._core.languages._testing import (  # noqa: E402, F401
    find_test_pairs,
    get_test_patterns,
    is_test_file,
)
