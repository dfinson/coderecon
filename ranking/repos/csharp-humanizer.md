# Humanizr/Humanizer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Humanizr/Humanizer |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Small (focused library) |
| **Category** | String manipulation and formatting library |

## Why this repo

- **Single-purpose**: Manipulates and displays strings, enums, dates, times,
  quantities, and numbers in human-friendly formats. One developer can hold
  the entire codebase in their head.
- **Well-structured**: Source under `src/Humanizer/` with clear per-concern
  directories (inflections, truncation, date/time humanization, bytes, etc.).
  Consistent patterns across all humanization types.
- **Rich history**: 1.5K+ commits, community contributors for localization
  (30+ languages). Varied PR types from new features to localization to
  performance.
- **Permissive**: MIT license.

## Structure overview

```
src/Humanizer/
├── StringHumanizeExtensions.cs    # Core string humanization
├── EnumHumanizeExtensions.cs      # Enum display names
├── NumberToWordsExtension.cs      # Number-to-words conversion
├── DateHumanizeExtensions.cs      # "2 hours ago" style
├── TimeSpanHumanizeExtensions.cs  # Duration formatting
├── Inflections/                   # Pluralize, singularize
├── Truncation/                    # String truncation strategies
├── Bytes/                         # Byte size formatting
├── Localisation/                  # Localization resources
│   ├── NumberToWords/             # Per-language number-to-words
│   └── DateToOrdinalWords/        # Per-language ordinal dates
└── FluentDate/                    # Fluent date construction
```

## Scale indicators

- ~100 C# source files
- ~15K lines of code (excluding localization resources)
- Flat structure (1-2 levels)
- Zero dependencies
