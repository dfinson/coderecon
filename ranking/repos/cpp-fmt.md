# fmtlib/fmt

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fmtlib/fmt |
| **License** | MIT |
| **Language** | C++ |
| **Scale** | Small (focused library) |
| **Category** | Text formatting library |

## Why this repo

- **Single-purpose**: Fast, safe text formatting library for C++. The basis
  for C++20's `std::format`. Core formatting engine, compile-time format
  string checking, and output adapters. One developer can hold the full
  codebase in their head.
- **Well-structured**: Header-only core under `include/fmt/` with clear
  per-concern headers. Implementation in `src/` is minimal. Separation
  between core formatting, output targets (printf, ostream, std), and
  compile-time checking is clean.
- **Rich history**: 3K+ commits, 20K+ stars. Adopted into the C++ standard.
  PRs cover performance optimization, format spec compliance, and compiler
  compatibility.
- **Permissive**: MIT license.

## Structure overview

```
include/fmt/
├── core.h               # Core formatting engine, format_to, format
├── format.h             # Extended formatting facilities
├── format-inl.h         # Implementation details (out-of-line)
├── compile.h            # Compile-time format string parsing
├── ranges.h             # Range/container formatting
├── chrono.h             # Date/time formatting
├── color.h              # Terminal color formatting
├── os.h                 # OS-specific output (file, pipe)
├── ostream.h            # std::ostream integration
├── printf.h             # printf-compatible interface
├── std.h                # std:: type formatters
├── xchar.h              # Wide character support
└── args.h               # Dynamic argument handling

src/
├── format.cc            # Out-of-line implementation
└── os.cc                # OS-specific implementation
```

## Scale indicators

- ~20 header files + ~5 source files
- ~25K lines of code (headers are substantial)
- Flat structure (1 level)
- Zero dependencies (header-only core)
