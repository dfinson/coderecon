# BurntSushi/ripgrep

| Field | Value |
|-------|-------|
| **URL** | https://github.com/BurntSushi/ripgrep |
| **License** | MIT OR UNLICENSE |
| **Language** | Rust |
| **Scale** | Medium (multi-module project) |
| **Category** | Recursive search tool |
| **Set** | ranker-gate |
| **Commit** | `4519153e5e461527f4bca45b042fff45c4ec6fb9` |

## Why this repo

- **Multi-module with clear boundaries**: Regex engine integration, glob
  matching, directory traversal, printer/output formatting, CLI argument
  handling — each is a separate crate in the workspace with well-defined
  interfaces between them.
- **Well-structured**: Workspace of focused crates (`grep-regex`, `grep-searcher`,
  `grep-matcher`, `grep-printer`, `grep-cli`, `ignore`). Each crate has a
  clear single responsibility.
- **Rich history**: 2K+ commits by BurntSushi with extremely high code quality.
  PRs and issues show real decisions about performance, correctness, and API
  design.
- **Permissive**: Dual-licensed MIT OR UNLICENSE.

## Structure overview

```
.
├── crates/
│   ├── matcher/         # Trait for regex matching backends
│   ├── regex/           # Regex matcher implementation (wraps regex crate)
│   ├── searcher/        # Core search logic (line-by-line, multiline)
│   ├── printer/         # Output formatting (standard, JSON, summary)
│   ├── cli/             # CLI utilities (colors, human-readable output)
│   ├── ignore/          # .gitignore-style file filtering + directory walking
│   └── globset/         # Glob pattern matching
├── crates/core/
│   ├── main.rs          # CLI entry point
│   ├── flags/           # Argument parsing, config, and completion
│   ├── search.rs        # Search coordinator
│   └── haystack.rs      # File target abstraction
└── tests/               # Integration tests
```

## Scale indicators

- ~100 Rust source files across crates
- ~40K lines of code
- Clear crate boundaries with trait-based interfaces
- 2-3 levels of module nesting within crates

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `--count-matches` incorrect with `--multiline`

When using `--count-matches` together with `--multiline`, the count is
wrong for patterns that match across line boundaries. The counter
increments per line touched by the match instead of per match occurrence.
Fix the multiline match counter in the searcher crate to count actual
match occurrences.

### N2: Fix `ban::check` false negative for multi-range character classes

The `ban::check` function in `crates/regex/src/ban.rs` detects when a
regex pattern can match a banned byte (typically `\n` for line-by-line
search). However, it only checks character classes whose total range
length is exactly 1, skipping multi-element classes. A pattern like
`[\x00-\x01]` passes the ban check even though `\x00` is matchable.
Fix the check to detect banned bytes in any character class range,
regardless of total class size.

### N3: Add global user-level `.rgignore` file support

Ripgrep currently processes `.rgignore` files found in traversed
directories but does not load a global user-level ignore file.
Add global ignore file discovery to the `ignore` crate so ripgrep
reads a per-user ignore file at a platform-appropriate path:
`$HOME/.rgignore` on Unix/macOS, and `%USERPROFILE%\.rgignore` or
`%APPDATA%\ripgrep\ignore` on Windows (using `#[cfg(windows)]` for
the platform conditional). Also update `FAQ.md` to expand the "Does
ripgrep support configuration files?" entry with the global ignore
file paths for each platform.

### N4: Fix `--hidden` flag not searching inside `.git/hooks/`

Even with `--hidden` enabled, files inside `.git/hooks/` are skipped
because the `.git` directory is unconditionally excluded. The `--hidden`
flag should apply to dotfiles and dotdirs, but `.git` has a separate
hardcoded exclusion. Add `--search-dot-git` flag that overrides the
`.git` exclusion when combined with `--hidden`.

### N5: Fix `--vimgrep` output missing column number for multiline matches

When using `--vimgrep` with `--multiline`, the column number in the
output is always 0 for matches that span multiple lines. The column
calculator only handles single-line matches. Fix column reporting for
multiline match starts.

### N6: Fix `normalize_path` in globset to handle UNC path prefixes

The `normalize_path` function in `crates/globset/src/pathutil.rs`
replaces all backslash separators with forward slashes on Windows.
However, UNC paths (`\\server\share`) become `//server/share` after
normalization. The resulting double-slash prefix causes glob patterns
with absolute paths to fail matching. Fix `normalize_path` to detect
UNC path prefixes and preserve them correctly during separator
normalization.

### N7: Fix PCRE2 JIT stack overflow on complex patterns with large files

When using the PCRE2 regex engine (`-P`) with complex patterns
(many alternations, nested quantifiers) on large files, the JIT
compiled code overflows its default stack. Add configurable JIT stack
size via `--pcre2-jit-stack` and increase the default size.

### N8: Fix hyperlink URLs not percent-encoding non-ASCII bytes in paths

The `encode()` function in `crates/printer/src/hyperlink/mod.rs`
correctly percent-encodes ASCII special characters but passes bytes
with value ≥ 128 through unencoded (the `128.. => result.push(byte)`
arm). RFC 3986 requires all non-ASCII bytes to be percent-encoded in
URIs. Paths containing non-ASCII characters (accented letters, CJK
characters, emoji, or other multibyte UTF-8 sequences in filenames)
produce technically invalid `file://` hyperlink URLs that some
terminal emulators fail to open. Fix the `encode()` function to
percent-encode each byte with value ≥ 128 as a `%XX` sequence,
preserving `/` and `:` as unencoded path separators.

### N9: Fix binary file detection false positive on UTF-16 files

Files encoded as UTF-16 (common on Windows) are detected as binary
because they contain null bytes. The binary detection heuristic treats
any null byte as binary content. Add UTF-16 BOM detection to the
binary checker and skip null-byte checking for UTF-16 files.

### N10: Fix `--glob` pattern not matching symlinked directories

When searching with `--follow` (follow symlinks) and `--glob '!vendor/'`,
the glob exclusion doesn't apply to directories reached via symlinks
because the glob matcher uses the symlink path, not the resolved
canonical path. Fix glob matching to check both the symlink and
canonical paths.

## Medium

### M1: Implement search-and-replace with `--replace` improvements

Expand the `--replace` functionality to support capture group
transformations: `\U$1` (uppercase), `\L$1` (lowercase), `\E` (end
case change). Currently `--replace` only supports literal substitution
and numbered capture groups. This requires changes to the regex crate
integration, the printer crate (for preview output), and CLI argument
validation.

### M2: Implement file type detection by shebang line

Extend the file type matching in `crates/ignore/src/types.rs` to detect
file types by inspecting the shebang (`#!`) line of files. A file
without a `.py` extension but with `#!/usr/bin/env python3` should
match the `python` file type when `--type python` is used. This
requires adding shebang-based matching to the `Types` matcher alongside
extension matching, reading the first line of each file during
traversal in `crates/ignore/src/walk.rs`, and adding a
`--type-shebang` toggle flag in the CLI. Also update `GUIDE.md` to
add a "Shebang-based file type detection" subsection under the existing
"Manual filtering: file types" section explaining the new behavior.

### M3: Improve parallel directory traversal with lock-free work queues

The parallel directory walker in `crates/ignore/src/walk.rs` already
implements work-stealing using per-thread `VecDeque` stacks protected
by `Mutex`. Replace these Mutex-protected queues with lock-free
work-stealing deques using the `crossbeam-deque` crate, which
provides a proper Chase-Lev deque and eliminates lock contention when
multiple threads simultaneously steal work from each other's queues.
Additionally replace the current sleep-based polling for new work
with a `Condvar`-based wake mechanism so idle threads sleep rather
than spin. Update the thread-pool setup in `WalkParallel`, replace
the internal stack implementation, and add benchmarks via `hyperfine`
comparing the lock-free implementation against the baseline on a
large repository.

### M4: Implement per-file search timeout

Add a `--timeout <duration>` flag that limits the time spent searching
each individual file. If a file's search exceeds the timeout (due to
a pathological regex or very large file), skip the file and report it
via stderr. This requires adding timeout tracking in
`crates/searcher/src/searcher/core.rs` (check elapsed time in the
search loop), propagating the timeout through the line-by-line and
multiline search paths in `crates/searcher/src/searcher/glue.rs`,
adding a timeout error variant to `crates/searcher/src/sink.rs`, and
registering the CLI flag.

### M5: Implement result grouping by directory or file type

Add `--group-by <mode>` with values `none`, `directory`, and `type`
that groups search results under shared headers. In `directory` mode,
results are collected and printed under their parent directory path.
In `type` mode, results are grouped by the matched file type
(e.g., `rust`, `python`). This requires buffering results in
`crates/core/search.rs`, adding group header rendering in
`crates/printer/src/standard.rs`, and integrating with the file type
definitions from `crates/ignore/src/types.rs`.

### M6: Implement incremental search with file modification tracking

Add `--since <timestamp>` that searches only files modified after the
given timestamp. Use filesystem mtime for filtering. Support
`--since-last-search` that records the search timestamp and searches
only files modified since the previous invocation. Store timestamps in
a `.rgcache` file.

### M7: Add CSV and SARIF output formats

Ripgrep already supports `--json` for JSON-lines machine-readable
output. Extend the structured output support by adding an
`--output-format <fmt>` flag with two new formats: `csv`
(comma-separated columns: file, line, column, match text, context
lines) and `sarif` (Static Analysis Results Interchange Format
v2.1.0 for IDE and CI tool integration). Each format includes file
path, line number, column range, match text, and surrounding context.
Add new printer implementations in `crates/printer/src/` for each
format (e.g., `crates/printer/src/csv.rs`,
`crates/printer/src/sarif.rs`), expose them through the printer
crate's public API, register the `--output-format` CLI flag in
`crates/core/flags/defs.rs`, wire it through
`crates/core/flags/hiargs.rs` and `crates/core/search.rs`, and add
integration tests.

### M8: Add diff-style replacement preview with `--replace-diff`

The existing `--replace` flag substitutes match text in printed
output. Add a `--replace-diff` flag that renders replacements as a
unified diff: each matching file shows a `--- original` / `+++ replaced`
header, with `-` lines showing the original content and `+` lines
showing the replacement. Non-matching lines are omitted (like `grep
context` output). This lets users verify replacement patterns before
committing to an external `sed` pipeline. Implement the diff-output
sink path in `crates/printer/src/standard.rs`, add the flag to
`crates/core/flags/defs.rs`, propagate the option through
`crates/core/flags/hiargs.rs` and `crates/core/search.rs`, and add
integration tests in `tests/`.

### M9: Add multi-pattern search with labeled results

Implement `--pattern-file <path>` that reads multiple search patterns
from a file (one per line) and labels each match with its pattern ID.
Support `--pattern 'name:pattern'` for inline named patterns. Output
includes the pattern label alongside each match. Enable AND-matching
(all patterns must match in the same file) with `--pattern-require-all`.

### M10: Implement ripgrep as a library (rg-core)

Extract ripgrep's search logic into a reusable library crate
(`rg-core`) with a clean API: `SearchBuilder` for configuring
searches, `SearchResult` iterator for results, and `Filter` trait
for custom file filtering. The `rg` binary becomes a thin CLI wrapper
around `rg-core`. Support embedding ripgrep search in other Rust
applications.

## Wide

### W1: Add Language-aware search

Implement `--lang` filtering that goes beyond file extension matching
to understand language-specific structures. Support searching only
within comments (`--lang-scope comment`), string literals
(`--lang-scope string`), or code (`--lang-scope code`) for supported
languages. This requires integrating a fast tokenizer (not full
parser) for major languages. The tokenizer should run in the searcher
crate before regex matching.

### W2: Implement search indexing for repeated searches

Add an optional `--index` mode that builds a trigram index of the
file content on first run and uses it for subsequent searches to
skip files that cannot contain the search pattern. The index should
be stored alongside the directory, updated incrementally on file
changes (using mtime checks), and ignored when the user's pattern
uses features that can't be trigram-filtered (e.g., `.` or `\w`).
This affects the ignore crate (index storage), searcher crate
(index lookup), and CLI (index management commands).

### W3: Implement language-aware structural search

Add `--lang-search` that understands language constructs. Support
searching within specific code structures: `--lang-search 'fn:pattern'`
(search only in function bodies), `--lang-search 'comment:pattern'`
(search only in comments), `--lang-search 'string:pattern'` (string
literals only). Requires integrating a multi-language tokenizer
(tree-sitter based) in the searcher crate. Changes span the regex
crate integration, searcher, printer, and CLI.

### W4: Implement watch mode for continuous search (`rg --watch`)

Add `rg --watch` that monitors the search paths for filesystem
changes and re-runs the search automatically when files are created,
modified, or deleted. Use the `notify` crate for cross-platform
filesystem event monitoring (inotify on Linux, FSEvents on macOS,
ReadDirectoryChangesW on Windows). On each change event, re-run the
search for affected files only and refresh the output in-place using
ANSI cursor control, adding new matches and removing stale ones.
Implement the watcher coordinator in a new `crates/core/watch.rs`,
integrate file-change notifications with the directory walker in
`crates/ignore/src/walk.rs`, add watch-mode output handling in
`crates/printer/src/standard.rs`, register `--watch` and
`--watch-debounce <ms>` flags in `crates/core/flags/defs.rs`,
propagate through `crates/core/flags/hiargs.rs` and
`crates/core/search.rs`, update `crates/core/main.rs` with the
watch event loop, and add integration tests.

### W5: Implement code search index server

Add `rg serve` that starts a persistent HTTP server with a pre-built
trigram index for fast repeated searches. The server indexes specified
directories on startup, watches for file changes (incremental updates),
and handles search queries over HTTP with JSON responses. Support
authentication, concurrent queries, and query result caching.
Changes span the ignore crate (index storage), searcher (index-
accelerated search), and add an HTTP server module.

### W6: Add search result visualization in the terminal

Implement `--ui` mode that shows search results in an interactive
terminal UI (using crossterm/termion). Features: result list with
keyboard navigation, preview pane showing file content with match
highlighted, filter/refine results interactively, open selected
result in `$EDITOR`, and result grouping by directory. Requires a TUI
framework integration, result buffering, and interactive input handling.

### W7: Implement `.rgconfig` project-level configuration

Add project-level configuration via `.rgconfig` files. Support project-
specific settings: default flags, custom type definitions, ignore
patterns, pre-processors, and search aliases. Config files cascade
(project → user → system) with precedence rules. Support conditional
config based on file patterns. Changes span the CLI argument parsing,
ignore crate (config discovery), and add a config parser module.

### W8: Add search analytics and performance profiling

Implement `--profile` that outputs detailed performance data: time
spent per phase (directory walk, file filtering, regex compilation,
searching, output), per-file timing breakdown, regex engine statistics
(match count, backtrack count, cache hit rate), and I/O statistics
(bytes read, syscall count). Output as JSON or render a flame chart
in the terminal. Changes span all crates with instrumentation points.

### W9: Implement cross-repository search aggregation

Add `rg --repos <dir>` that searches across multiple Git repositories
under a directory. Group results by repository, support per-repo branch
selection, exclude dirty working tree changes, and aggregate statistics
per repo. Support searching a specific commit range across repos.
Changes span the ignore crate (repo discovery), searcher (per-repo
context), printer (grouped output), and CLI.

### W10: Add content-aware search with semantic fallback

Implement a two-phase search: first, try regex matching (fast path).
If no results, fall back to semantic search using a local embedding
model to find code that's conceptually similar to the query, even if
no exact text match exists. Add `--semantic` flag to force semantic
mode. Show both regex matches and semantic matches when both are
available. Requires embedding model integration, similarity search,
and result ranking.

## Non-code focused

### N11: Update `RELEASE-CHECKLIST.md` to add PCRE2 verification step

The `RELEASE-CHECKLIST.md` documents the full release process including
dependency updates, crate ordering, tag creation, and Homebrew formula
updates, but does not include a step to verify that the `pcre2` feature
compiles and passes tests before publishing. The PCRE2 feature is
optional and has caused release issues in the past (e.g., static
compilation on macOS aarch64 per CHANGELOG entry BUG #3155). Add a
checklist item after the "Run `cargo package`" step to run
`cargo test --features pcre2` and verify PCRE2 builds on all release
targets.

### M11: Add developer code style documentation to `GUIDE.md` and `CONTRIBUTING.md`

The project enforces formatting via `rustfmt.toml` and CI, but there
is no contributor documentation explaining the code style conventions.
Add a "Code style" section to `GUIDE.md` after the existing "Common
options" section, documenting the `rustfmt.toml` settings
(`max_width = 79`, `use_small_heuristics = "max"`), how to run
`cargo fmt --all` before submitting PRs, and how to run
`cargo clippy --all --all-features` to check for lint warnings.
Create a `CONTRIBUTING.md` at the repository root that consolidates
contributor guidance: building from source, running the test suite
with `cargo test --workspace`, the code style expectations (linking
to the GUIDE.md section), and the PR review process. Update `README.md`
to add a "Contributing" pointer to `CONTRIBUTING.md`.

### W11: Add cross-platform release artifact verification with CI and documentation

The release process spans `RELEASE-CHECKLIST.md` (manual steps),
`.github/workflows/release.yml` (automated build matrix),
`Cargo.toml` (version and feature definitions), `CHANGELOG.md`
(release notes), and the `ci/sha256-releases` script (checksum
computation). Currently there is no automated verification that
release artifacts are functional after build. Add an integration test
job to `.github/workflows/release.yml` that downloads each built
artifact, runs `rg --version` and a basic search test, and verifies
the SHA256 checksum matches. Update `RELEASE-CHECKLIST.md` to
reference this new verification step. Add a "Release verification"
section to `FAQ.md` explaining how users can verify downloaded
binaries. Update `Cargo.toml`'s `exclude` list to ensure the new
test fixtures are not included in the published crate.
