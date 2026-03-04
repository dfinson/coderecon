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
