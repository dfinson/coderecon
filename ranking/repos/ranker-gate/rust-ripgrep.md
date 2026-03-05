# BurntSushi/ripgrep

| Field | Value |
|-------|-------|
| **URL** | https://github.com/BurntSushi/ripgrep |
| **License** | MIT OR UNLICENSE |
| **Language** | Rust |
| **Scale** | Medium (multi-module project) |
| **Category** | Recursive search tool |

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
├── src/
│   ├── main.rs          # CLI entry point
│   ├── app.rs           # Argument parsing and config
│   ├── args.rs          # Argument processing
│   └── search.rs        # Search coordinator
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

### N2: Add `--max-columns-preview` to show truncated line prefix

When `--max-columns` truncates a long line, the output shows
`[Omitted long matching line]` with no content preview. Add a
`--max-columns-preview` flag that shows the first N characters of the
truncated line followed by `...` instead of the generic omission
message.

### N3: Fix `.rgignore` not loaded from `$HOME` on Windows

On Windows, ripgrep looks for the global ignore file at
`$HOME/.rgignore` but `$HOME` is not standard on Windows. The lookup
should also check `%USERPROFILE%\.rgignore` and
`%APPDATA%\ripgrep\ignore`. Fix the global ignore file discovery in
the `ignore` crate to use platform-appropriate paths.

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

### N6: Add `--stats` flag to show match statistics summary

Add a `--stats` flag that prints a summary after search: total files
searched, files with matches, total matches, total lines matched,
bytes processed, and elapsed time. Similar to grep's `--count` but
aggregate across all files.

### N7: Fix PCRE2 JIT stack overflow on complex patterns with large files

When using the PCRE2 regex engine (`-P`) with complex patterns
(many alternations, nested quantifiers) on large files, the JIT
compiled code overflows its default stack. Add configurable JIT stack
size via `--pcre2-jit-stack` and increase the default size.

### N8: Add `--type-add` for defining custom file types at runtime

Currently custom file types can only be defined in config files. Add
`--type-add 'proto:*.proto'` command-line flag for defining custom
file types in a single invocation without modifying config files.

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

### M2: Add JSON output for structured consumption

Implement a `--json` output format that emits one JSON object per
match with fields: file path, line number, column range, matched text,
context lines (before/after), and byte offset. The JSON printer should
handle binary file detection, encoding issues, and streaming output
(one JSON object per line, not a single array). Add tests for
round-tripping JSON output.

### M3: Implement parallel directory traversal with work stealing

The current directory traversal uses a thread pool but the walk is
single-threaded with a channel to workers. Implement a work-stealing
directory walker where each thread independently walks its portion
of the directory tree and steals work from other threads' queues when
idle. This should improve performance on network filesystems and
repositories with many directories at the same depth.

### M4: Implement search within compressed files

Add `--search-zip` support for searching inside compressed files:
gzip, bzip2, xz, and zstd. Decompress on-the-fly without extracting
to disk. Show results with the archive path prefix. Support
`--search-tar` for tar archives (with or without compression). Handle
memory limits for decompressed content.

### M5: Add `--pre` processor for custom file transformations

Implement `--pre <command>` that pipes each file through an external
command before searching. The command receives the file content on
stdin and outputs transformed content on stdout. Useful for searching
in binary formats (PDF, docx) via external converters. Support
configurable timeout and process pool for parallelism.

### M6: Implement incremental search with file modification tracking

Add `--since <timestamp>` that searches only files modified after the
given timestamp. Use filesystem mtime for filtering. Support
`--since-last-search` that records the search timestamp and searches
only files modified since the previous invocation. Store timestamps in
a `.rgcache` file.

### M7: Add structured output with match context metadata

Implement `--output-format <fmt>` supporting formats: json-lines (one
JSON object per match with full metadata), csv (columns: file, line,
column, match, context), and sarif (Static Analysis Results Interchange
Format for IDE integration). Each format includes file path, line
number, column range, match text, and surrounding context.

### M8: Implement search-and-replace with preview mode

Extend `--replace` with a `--preview` mode that shows the would-be
replacement diff without modifying files. Add `--replace-write` that
performs the replacement on disk. Support interactive mode where each
match is shown and the user confirms (y/n/a/q). Handle file encoding
preservation and line ending consistency.

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

### W4: Add distributed search across multiple machines

Implement `rg --distributed <host-list>` that distributes the search
across multiple machines via SSH. Each machine searches its local
filesystem and streams results back. Merge and deduplicate results.
Support progress aggregation, error handling per host, and automatic
load balancing based on file count. Changes span the CLI, add a
network transport layer, result merging, and progress tracking.

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
