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

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `#[serde(flatten)]` ignoring `#[serde(deny_unknown_fields)]`

When a struct uses `#[serde(flatten)]` on a field and
`#[serde(deny_unknown_fields)]` on the container, unknown fields are
silently accepted instead of causing a deserialization error. The
flattened field's `MapAccess` implementation consumes all keys, preventing
the deny-unknown-fields check from seeing them. Fix the flattened
deserialization to forward unrecognized keys to the container's unknown
field handler.

### N2: Add `#[serde(skip_serializing_if_default)]` attribute

Currently users must write `#[serde(skip_serializing_if = "is_default")]`
and define a helper function. Add a built-in `skip_serializing_if_default`
attribute that skips serialization when the field value equals its
`Default::default()`. The field type must impl both `Default` and
`PartialEq`.

### N3: Fix error span pointing to wrong field with `#[serde(rename_all)]`

When deserialization fails on a renamed field (via `rename_all = "camelCase"`),
the error message shows the original Rust field name instead of the
renamed (JSON) key name. Users see "unknown field `userData`" when the
error should say "missing field `userData`". Fix the error reporting to
use the serialized name.

### N4: Fix `#[serde(default)]` not called when field is `null` in JSON

When a JSON field is explicitly `null` and the struct field has
`#[serde(default)]`, the default function is not called — instead,
`null` is deserialized as `None` wrapping the default. The default
should be used when the field is either absent or `null`. Fix the
deserialize implementation for defaulted fields.

### N5: Add `#[serde(alias)]` support for enum variant names

`#[serde(alias = "name")]` works on struct fields but not on enum
variants. Add alias support for externally-tagged, internally-tagged,
and adjacently-tagged enum variants so multiple names can deserialize
to the same variant.

### N6: Fix `#[serde(bound = "")]` not clearing derive-inferred bounds

When explicitly setting `#[serde(bound = "")]` to clear all trait
bounds from the derived impl, the derive macro still adds `T: Serialize`
bounds for generic type parameters. Fix the bound clearing to fully
remove inferred bounds when an explicit empty bound is provided.

### N7: Fix `serde_derive` producing ambiguous associated type in certain generics

When deriving `Serialize` on a struct with a generic field `T::Output`
where `T` has multiple traits defining `Output`, the derived code
produces ambiguous associated type errors. Fix the derive codegen to
fully qualify associated types.

### N8: Add `#[serde(try_from = "Type")]` error type propagation

When using `#[serde(try_from = "String")]`, conversion errors lose
their original error type and are wrapped in a generic serde error
string. Propagate the `TryFrom::Error` type details through the
deserialization error so users can match on the original error.

### N9: Fix `#[serde(with = "module")]` not working with Option fields

Using `#[serde(with = "custom_module")]` on an `Option<T>` field
requires the module to handle the `Option` wrapper itself. Add
automatic `Option` unwrapping so the `with` module only needs to
handle `T`, and `None` → skip / `null` is handled by serde.

### N10: Add `#[serde(rename_all_fields)]` for enum variants with named fields

`rename_all` applies to variant names but not to the fields within
variants. Add `rename_all_fields = "camelCase"` at the enum level
that applies the rename rule to all fields in all variants.

## Medium

### M1: Implement `#[serde(tag = "...", content = "...")]` for enums with newtype variants

The adjacently-tagged representation (`#[serde(tag = "t", content = "c")]`)
currently requires all variants to have named fields or be
unit variants. Add support for newtype variants (e.g., `Variant(String)`)
in adjacently-tagged enums. The content should be serialized as the
inner type directly under the content key.

### M2: Add compile-time validation of serde attributes

Currently, many serde attribute errors are only caught at runtime (e.g.,
`rename_all` with an invalid case convention, conflicting attributes).
Add compile-time validation in `serde_derive` that checks for: invalid
`rename_all` values, conflicting attributes (`skip_serializing` with
`serialize_with`), `flatten` on non-map types, and `tag` on non-enum
types. Emit clear `compile_error!` messages with suggested fixes.

### M3: Implement `#[serde(transparent)]` for enums

The `#[serde(transparent)]` attribute works on structs with a single
field, delegating to that field's Serialize/Deserialize. Extend support
to enums where each variant has exactly one field — serialization
delegates to the variant's field, and deserialization tries each
variant's inner type in order.

### M4: Implement zero-copy deserialization for nested structures

Currently `#[serde(borrow)]` enables zero-copy `&str` deserialization
for top-level fields but fails for fields in nested structs or enum
variants. Extend the borrow mechanism to propagate lifetime constraints
through nested structures, allowing borrowed references at any depth.

### M5: Add human-readable vs compact serialization format selection

Implement `Serializer::is_human_readable()` support throughout serde.
When serializing to a human-readable format (JSON, TOML), use
user-friendly representations (e.g., `Duration` as `"2h30m"`). When
serializing to a compact format (bincode, CBOR), use efficient
representations. The format detection should be automatic.

### M6: Implement `#[serde(validate)]` for post-deserialization validation

Add `#[serde(validate = "validate_fn")]` that calls a validation
function after all fields are deserialized. If validation fails, return
a deserialization error with the validation message. Support field-level
validation (`#[serde(validate = "check_range")]` on individual fields)
that runs after the field is deserialized but before the struct is
constructed.

### M7: Add derive support for `Serialize`/`Deserialize` on foreign types

Implement `#[serde(remote)]` improvements: support remote types with
private fields (via accessor functions), remote enum types, and remote
types with generics. Add `#[serde(transparent)]` support for newtypes
wrapping remote types. Currently `remote` requires the remote type to
have all public fields.

### M8: Implement content-type-aware serialization

Add a mechanism for serializers to declare their content type (JSON,
XML, TOML, binary) so that `Serialize` implementations can branch on
the output format. Add `Serializer::content_type() -> ContentType`
with a default implementation returning `Unknown`. Document patterns
for format-specific serialization without runtime overhead for the
common case.

### M9: Add `#[serde(flatten)]` performance optimization

`#[serde(flatten)]` currently has O(n²) performance with multiple
flattened fields because each flattened field deserializes the entire
remaining map. Implement a buffered flattening strategy that reads the
map once into an intermediate buffer, then distributes keys to the
appropriate flattened fields.

### M10: Implement exhaustive enum deserialization errors

When deserializing an enum fails because the tag value doesn't match
any variant, the error message shows "unknown variant X" but doesn't
list the valid variants. Add a `expected_variants` method to the
`Visitor` for enums that includes all valid variant names in the error
message. Support `#[serde(rename)]` names in the expected list.

## Wide

### W1: Add first-class support for `serde` with `no_std` + `no_alloc`

Currently `serde` without `std` still requires `alloc` for `String`,
`Vec`, and `BTreeMap`. Implement a `no_alloc` feature that replaces
heap-allocated types with bounded stack alternatives. Provide
`Serialize` impls for `heapless::String`, `heapless::Vec`, etc. Adjust
the data model, derive macros, and built-in impls to work without an
allocator. This affects the core trait definitions, derive codegen,
and the built-in impl set.

### W2: Implement schema generation from serde-annotated types

Add a `serde_schema` crate that generates data schemas from types
implementing `Serialize`/`Deserialize`. The schema should capture field
names (including renames), types, optionality, default values, tag
representations, and flattening structure. Output format should be
JSON Schema. The derive macro should generate a `Schema` impl alongside
`Serialize`/`Deserialize`. Support recursive types via `$ref`.

### W3: Implement async serialization and deserialization

Add `AsyncSerializer` and `AsyncDeserializer` traits for
streaming serialization/deserialization over async I/O. Support
serializing large collections without buffering the entire output,
and deserializing from async byte streams (e.g., `tokio::io::AsyncRead`).
Add derive macro support for async traits. This crosses the core
traits, derive macros, and data model.

### W4: Add serde data model introspection API

Implement a `Describe` trait (derivable alongside `Serialize`/
`Deserialize`) that returns a structural schema of the type: field
names, types, optional/required status, default values, and container
attributes. The schema should be queryable at runtime without needing
actual data. This enables JSON Schema generation, documentation
generation, and migration tooling. Crosses derive macros, core
traits, and the data model.

### W5: Implement backwards-compatible deserialization with version tags

Add `#[serde(version = 2)]` that embeds a version tag in serialized
output. On deserialization, detect the version and dispatch to the
appropriate `Deserialize` implementation. Support migration functions
between versions: `#[serde(migrate_from(v1 = "V1Type", with = "migrate_fn"))]`.
This requires derive macro changes, core trait extensions, and a
version resolution protocol.

### W6: Add first-class support for self-describing formats

Implement a `SelfDescribing` serializer trait that embeds type information
alongside values. On deserialization, `serde_any::from_reader()` can
detect the format (JSON, TOML, YAML, MessagePack) from the content
and deserialize automatically. Includes format detection heuristics,
a registry for format handlers, and an envelope format for explicit
format tagging.

### W7: Implement streaming collection serialization with backpressure

Add `SerializeStreamingSeq` and `SerializeStreamingMap` traits that
support serializing collections element-by-element with flow control.
When the output buffer is full, the serializer pauses production
(backpressure). Support for `Iterator`-based serialization without
collecting into a `Vec` first. Crosses the core serialization traits,
derive macros (for `#[serde(streaming)]`), and data model.

### W8: Add compile-time format-specific optimization

Implement a compile-time specialization mechanism where the derive
macro generates format-specific fast paths for common serialization
formats. For example, `Serialize` on a struct would generate both the
generic trait implementation and a direct-to-JSON function (bypassing
the `Serializer` trait) when the output format is known at compile
time. Requires proc macro architecture changes and conditional
compilation support.

### W9: Implement serde testing utilities

Add a `serde_test` crate with comprehensive testing tools: assert
round-trip serialization (`assert_ser_deser_eq`), test against specific
token streams (`assert_tokens`), property-based testing for
`Serialize`/`Deserialize` pairs, format compatibility testing (verify
output is valid JSON/TOML/YAML), and golden file testing. Include a
test derive macro that generates tests automatically. Crosses test
infrastructure, token representation, and format validation.

### W10: Add serde ecosystem interop layer

Implement a compatibility layer between serde and other serialization
ecosystems: protobuf (`prost`), Cap'n Proto, FlatBuffers, and
`rkyv` (zero-copy). For each format, provide bidirectional conversion
between the format's native types and serde's data model. Support
deriving both serde and the native traits from a single struct
definition. This crosses derive macros, data model mapping, and adds
per-format bridge modules.
