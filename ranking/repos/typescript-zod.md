# colinhacks/zod

| Field | Value |
|-------|-------|
| **URL** | https://github.com/colinhacks/zod |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Small (focused library) |
| **Category** | Schema validation library |

## Why this repo

- **Single-purpose**: TypeScript-first schema validation with static type
  inference. Core functionality is self-contained and graspable.
- **Well-structured**: Source under `src/` with clear type/validator split.
  Each schema type (string, number, object, array, union, etc.) is a distinct
  class with shared base patterns.
- **Rich history**: 3K+ commits, active PRs, widely adopted (30K+ GitHub stars).
  Real development patterns visible in commit/PR history.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── ZodError.ts          # Error types and formatting
├── types.ts             # Core schema types (ZodString, ZodNumber, ZodObject, etc.)
├── helpers/             # Utility types and functions
│   ├── parseUtil.ts     # Parse context and issue handling
│   ├── typeAliases.ts   # Shared type aliases
│   └── util.ts          # General utilities
├── locales/             # Error message localization
├── external.ts          # Public API re-exports
└── index.ts             # Entry point
```

## Scale indicators

- ~30 TypeScript source files
- ~12K lines of code
- Flat module structure, single conceptual domain
- Zero runtime dependencies
