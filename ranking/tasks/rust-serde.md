# Tasks — serde-rs/serde

8 tasks (3 narrow, 3 medium, 2 wide) for the Rust serialization framework.

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
