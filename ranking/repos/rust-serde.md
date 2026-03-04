# serde-rs/serde

| Field | Value |
|-------|-------|
| **URL** | https://github.com/serde-rs/serde |
| **License** | MIT OR Apache-2.0 |
| **Language** | Rust |
| **Scale** | Small (focused library) |
| **Category** | Serialization/deserialization framework |

## Why this repo

- **Single-purpose**: The foundational serialization framework for Rust.
  Core trait system (Serialize, Deserialize, Serializer, Deserializer) is
  compact and graspable by one developer.
- **Well-structured**: Clear split between `serde/` (core traits + data model),
  `serde_derive/` (proc macros), and `serde_derive_internals/` (shared derive
  logic). Each crate has defined boundaries.
- **Rich history**: 4K+ commits, meticulous stewardship by dtolnay. Changes
  are careful and well-reviewed. Foundational to the Rust ecosystem.
- **Permissive**: Dual-licensed MIT OR Apache-2.0.

## Structure overview

```
serde/
├── src/
│   ├── lib.rs           # Re-exports, feature gates
│   ├── ser/             # Serialize trait, Serializer trait, impls
│   ├── de/              # Deserialize trait, Deserializer trait, impls
│   ├── private/         # Internal implementation details
│   └── macros.rs        # Helper macros
serde_derive/
├── src/
│   ├── lib.rs           # Proc macro entry points
│   ├── ser.rs           # Serialize derive codegen
│   ├── de.rs            # Deserialize derive codegen
│   └── internals/       # Attribute parsing, AST, validation
```

## Scale indicators

- ~40 Rust source files across crates
- ~25K lines of code
- Shallow module structure (2 levels)
- Zero runtime dependencies (core crate)
