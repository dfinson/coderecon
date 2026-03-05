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

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide) for the TypeScript schema validation library.

## Narrow

### N1: Fix `.transform()` losing optional status in `.partial()`

When an object schema has an optional field with a `.transform()`, calling
`.partial()` on the parent object incorrectly marks the transformed field
as required. The transform wrapper loses the `isOptional()` flag during
the partial conversion. Fix `.partial()` to preserve optionality through
transform chains.

### N2: Add `.readonly()` modifier for object schemas

Add a `.readonly()` method to `ZodObject` that produces a TypeScript type
with all properties marked as `readonly`. The runtime behavior should be
identical (no Object.freeze), but the inferred type should use
`Readonly<T>`. Support nesting: `.readonly()` on an object with nested
objects should make all levels readonly.

### N3: Fix error path for discriminated union with nested objects

When validation fails inside a nested object within a discriminated union,
the error path does not include the discriminator key's value. The error
message says "Invalid input" without indicating which branch of the union
failed. Include the matched discriminator value in the error path so
users can identify which variant had the validation failure.

### N4: Fix `.catch()` fallback not invoked during async parsing

When using `.catch(fallback)` on a schema that contains async
refinements, the catch handler is not triggered on validation failure.
The async parse path in `ZodCatch` does not check for the catch wrapper
after awaiting inner validation. Fix the async branch to apply the
fallback value when the inner schema rejects.

### N5: Fix `.describe()` metadata dropped by `.optional()` wrapper

Calling `.describe("label")` on a schema and then wrapping it with
`.optional()` discards the description. The `ZodOptional` constructor
creates a fresh internal definition without copying the description
property from the inner schema. The description should propagate
through optional, nullable, and default wrappers.

### N6: Accept `readonly` tuple arrays in `z.enum()`

`z.enum()` requires a mutable tuple type but TypeScript `as const`
assertions produce `readonly` tuples. Passing a `readonly` array
causes a type error at the call site. Widen the type signature to
accept `Readonly` tuple types so that `z.enum(["a", "b"] as const)`
compiles without a manual cast.

### N7: Fix `z.preprocess()` not forwarding preprocessed value to refinements

When `.superRefine()` is chained after `z.preprocess()`, the refinement
callback receives the original raw input instead of the preprocessed
value. The preprocess step stores the transformed value but the
refinement context still references the raw input. Fix the parse
pipeline to thread the preprocessed value through subsequent
refinement steps.

### N8: Fix sparse array holes silently passing `ZodArray` validation

When validating a sparse JavaScript array like `[1, , 3]`, `ZodArray`
iterates holes as `undefined` without raising an issue. If the element
schema does not accept `undefined`, validation should fail for each
sparse index with a clear path indicating the missing position rather
than silently coercing the hole.

### N9: Add `.duration()` string validator for ISO 8601 durations

Add `z.string().duration()` that validates ISO 8601 duration strings
such as `P1Y2M3DT4H5M6S`. Reject malformed patterns including missing
`P` prefix, time components without `T` separator, and negative values.
Add the corresponding error code to the `ZodIssueCode` enum and a
default error message in the locale file.

### N10: Fix `.pipe()` producing incorrect inferred output type with transforms

When using `.pipe()` to chain two schemas where both apply transforms,
`z.infer<>` resolves to the input type of the second schema rather than
its output type. The `ZodPipeline` generic parameters do not propagate
the second schema's output correctly. Fix the type-level plumbing so
the inferred type matches the final transform output.

## Medium

### M1: Implement async refinements with `.superRefine()`

The current `.refine()` and `.superRefine()` methods only support synchronous
validation functions. Implement async variants (`.refineAsync()` already
exists but `.superRefine()` doesn't have an async path). Async refinements
should run in parallel where possible, collect all errors before returning,
and work with `.safeParseAsync()`. Update the parse pipeline to detect
when async refinements are present and require `.parseAsync()`.

### M2: Add JSON Schema generation

Implement a `.toJsonSchema()` method on all Zod schema types that produces
a valid JSON Schema (draft-2020-12) representation. Handle string formats,
numeric constraints, object properties, arrays (tuple and variable-length),
unions (including discriminated unions), enums, and recursive schemas
(using `$ref`). Add configuration for custom format mappings and
additional keywords.

### M3: Implement branded types with runtime validation

Add a `.brand<B>()` method that creates a branded type — a type that is
assignable only from validated values, not from plain literals. The
branded type should carry a phantom brand in the TypeScript type system.
Add `.isBranded()` type guard and `.unbrand()` to strip the brand.
Document the difference between `.brand()` (type-level only) and
`.refine()` (runtime checks).

### M4: Implement conditional schema selection with `z.switch()`

Add `z.switch(discriminatorKey, { value1: schema1, value2: schema2,
default: fallbackSchema })` as an ergonomic alternative to
`z.discriminatedUnion()`. Support a `default` branch for unmatched
discriminator values, type-safe exhaustiveness checking when no default
is provided, and nested switches. Infer the output type as the union of
all branch output types. Implement a `ZodSwitch` class with parse logic
that indexes branches by discriminator value for constant-time lookup.

### M5: Add dependent field validation on object schemas

Implement `.refineFields()` on `ZodObject` that accepts validation
functions referencing multiple properties simultaneously. Support use
cases like ensuring `endDate` is after `startDate`, or `confirmPassword`
matches `password`. The method should attach errors to specific field
paths using the existing issue context. Support both synchronous and
async validators, and compose with per-field refinements already on the
schema.

### M6: Implement recursive schema type inference without manual annotation

`z.lazy()` requires a manual type annotation because TypeScript cannot
infer recursive types from callbacks. Implement a `z.recursive()`
builder that uses a proxy-based pattern to achieve automatic type
inference for self-referential schemas. The builder should accept a
function receiving a reference to the schema being defined and return a
fully typed schema without requiring an explicit interface declaration
or a type parameter.

### M7: Add per-schema custom error maps

Currently error maps are global via `z.setErrorMap()` or per-parse-call.
Add `.errorMap(map)` as a chainable method on any schema that overrides
error messages for that schema's issues. Instance-level maps should
take precedence over the global map, compose when schemas are nested
inside objects or unions, and merge when multiple error maps are applied
at different levels of the same schema chain.

### M8: Implement schema introspection API

Add an `.introspect()` method on all schema types returning a normalized
descriptor object. For `ZodString`, include active checks (min, max,
regex, email, url, etc.) with their parameters. For `ZodObject`, include
property names mapped to their schema descriptors. For `ZodUnion` and
`ZodDiscriminatedUnion`, include member schemas. Define a
`SchemaDescriptor` type hierarchy that allows programmatic analysis of
any schema's structure without relying on internal properties.

### M9: Add `z.templateLiteral()` for template string validation

Implement a new schema type that validates strings against TypeScript
template literal patterns. `z.templateLiteral([z.literal("user-"),
z.number()])` should validate strings like `"user-42"` and infer the
type as `` `user-${number}` ``. Support embedding string, number, and
literal schemas as template segments. Parse by matching segments
left-to-right and validating each extracted substring against its
corresponding schema.

### M10: Extend the `z.coerce` namespace with missing primitive types

Add `z.coerce.bigint()` that coerces string and number inputs to
`BigInt`, failing on non-integer numbers and non-numeric strings. Add
configurable coercion options to existing schemas: `z.coerce.number({
nanToZero: true })` that maps `NaN` to `0`, and `z.coerce.string({
nullToEmpty: true })` that maps `null` to the empty string. Update the
type definitions, add coercion-specific error codes, and add default
messages in the locale file.

## Wide

### W1: Add schema versioning and migration system

Implement a versioned schema registry. `z.versioned(1, schemaV1)` tags
a schema version. `z.migrate(v1Schema, v2Schema, transformFn)` defines
a migration. `z.parseLatest(data, registry)` detects the version from
the data and applies the migration chain. Support forward and backward
compatibility modes. This requires a registry module, version detection,
migration chaining, and error handling for version gaps.

### W2: Implement effect system for schema side effects

Add `.effect()` that attaches side effects to schema validation without
affecting the parsed output. Support `.effect("validate", ctx => {})`
for validation-time side effects (logging, metrics), `.effect("transform",
ctx => {})` for transform-time side effects, and `.effect("error",
ctx => {})` for error-time side effects. Effects should compose, be
orderable, and support async. This crosses the parse pipeline, error
handling, and type inference systems.

### W3: Implement schema-driven mock data generation

Add `z.mock(schema)` that generates random valid data for any Zod
schema. Respect constraints: min/max for strings and numbers, regex
patterns for string checks, enum value sets, array length bounds, and
object shapes including optional fields. Support seeded deterministic
output, edge-case modes that produce boundary values and maximum
nesting depths, and custom generators per schema type. Requires
analyzing internal constraint representations across all schema classes
in the types module and building composable generator functions.

### W4: Add streaming validation for large datasets

Implement `z.stream(schema)` that validates elements from async
iterables and Node.js Readable streams one at a time. Support
configurable error thresholds allowing fail-fast or continue-after-N
modes, partial result emission via async generator, backpressure
handling, and progress callbacks. Introduce a `ZodStream` class that
integrates with the existing parse pipeline. Output a typed async
iterable of validated items alongside an accumulated error report.

### W5: Add comprehensive internationalization for error messages

Replace the single-locale error map with a full internationalization
system. Support pluralization rules, message templates with
interpolation placeholders like `{minimum}` and `{type}`, per-schema
message overrides via i18n keys, and locale fallback chains. Bundle
translations for major locales including English, Spanish, French,
German, Chinese, Japanese, Korean, and Portuguese. Changes span error
creation in the parse utilities, the error map system, `ZodError`
formatting, and the locales module.

### W6: Implement OpenAPI 3.1 bidirectional conversion

Add `z.toOpenAPI(schema)` that produces valid OpenAPI 3.1 schema
objects with support for discriminated unions mapped to the
`discriminator` keyword, nullable types, string formats like email and
uuid, numeric constraints, and `$ref` for recursive schemas. Add
`z.fromOpenAPI(spec)` that parses an OpenAPI 3.1 schema and produces
equivalent Zod schemas. Handle `allOf`/`oneOf`/`anyOf` composition,
reference resolution, and OpenAPI-specific extensions.

### W7: Implement protocol buffer interoperability

Add bidirectional conversion between Zod schemas and Protocol Buffer
definitions. Generate `.proto` files from Zod schemas with proto3
scalar types, message nesting, repeated fields from arrays, oneof from
discriminated unions, and map types from records. Parse existing
`.proto` files and produce equivalent Zod schemas. Handle well-known
types such as `google.protobuf.Timestamp` mapped to date schemas and
`Duration` mapped to number schemas. Requires a proto definition
parser, a type mapping layer, and code generators in both directions.

### W8: Add schema-driven TypeScript code generation

Implement a code generator that takes a runtime Zod schema and emits
TypeScript source with equivalent `z.*` definitions and exported
inferred types. Support round-tripping so that generated code imports
and produces a schema structurally equivalent to the original. Handle
recursive types via `z.lazy()`, discriminated unions, refinements
emitted as comments preserving the original function source, transforms,
defaults, and branded types. Requires schema introspection, TypeScript
AST construction, and import resolution.

### W9: Implement end-to-end API contract testing framework

Build a contract testing system where API endpoints are described by
Zod request and response schemas. `z.contract("createUser", { request:
reqSchema, response: resSchema })` defines a named contract. Provide
test utilities for producers that validate actual responses against the
contract and consumers that validate outgoing requests. Support
versioned contracts with breaking-change detection, a contract
registry, and diff reporting. Integrate with the error formatting
system for clear mismatch diagnostics.

### W10: Add schema-aware deep equality and structural diff

Implement `z.equal(schema, a, b)` that compares two values according
to the schema's type semantics: strip extra properties the schema would
discard, compare `Date` objects by value, handle `NaN` equality for
number schemas, compare `Set` and `Map` by contents, and recurse into
nested object schemas. Add `z.diff(schema, a, b)` returning a
structured list of differences with paths. This crosses every schema
type in the types module, adds comparison logic per type class, and
introduces new utility functions in the helpers module.
