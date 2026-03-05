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

8 tasks (3 narrow, 3 medium, 2 wide) for the TypeScript schema validation library.

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

## Wide

### W1: Add schema versioning and migration

Implement a system for evolving schemas over time. Add `.version(n)` to
tag a schema version, and a `migrate(oldSchema, newSchema, transformer)`
function that converts data validated against an older schema version to
the new format. Support chaining migrations across multiple versions
(v1 → v2 → v3). Include a `parseWithMigration()` method that automatically
applies the migration chain.

### W2: Implement form integration layer

Add a `@zod/forms` subpackage that bridges Zod schemas to HTML form
handling. Generate form field metadata (labels, placeholders, input types,
validation rules) from Zod schemas. Support client-side validation that
mirrors server-side Zod validation. Provide adapters for React Hook Form
and Formik. Include support for multi-step forms where each step
validates a subset of the schema.

### N4: Fix `.default()` not working with `.transform()`

When chaining `.default(value).transform(fn)`, the default value is not
passed through the transform function on parse. If the input is
`undefined`, the default is set but the transform is skipped. Fix the
parse pipeline to run transforms on default values.

### N5: Fix `.describe()` metadata lost after `.optional()`

When calling `.describe("help text").optional()`, the description
metadata set by `.describe()` is not carried forward to the new
optional schema. The optional wrapper creates a fresh schema without
copying the description. Fix `.optional()` to preserve description
metadata.

### N6: Add `.regex()` method for `ZodString` with named captures

`z.string().regex()` validates against a pattern but doesn't expose
named capture groups. Add support for extracting named captures:
`.regex(/(?<year>\d{4})-(?<month>\d{2})/)` should produce a typed
output with `year` and `month` fields when using `.transform()`.

### N7: Fix `z.discriminatedUnion()` error messages showing wrong variant

When validation fails in a discriminated union and the discriminator
value matches a variant, but a field in that variant fails, the error
message includes candidate variants that don't match the discriminator.
Filter error messages to only show the matched discriminant branch.

### N8: Fix `.catch()` not working with async schemas

When using `.catch(fallback)` with a schema that contains async
refinements, the catch handler is not invoked on validation failure.
The async parse path doesn't check for catch handlers. Fix
`.safeParseAsync()` to respect `.catch()` handlers.

### N9: Add `.ip()` validator for both IPv4 and IPv6 addresses

Add `z.string().ip()` that validates IPv4 and IPv6 addresses. Support
`z.string().ip({ version: "v4" })` and `z.string().ip({ version: "v6" })`
for version-specific validation. Include CIDR notation support with
`z.string().cidr()`.

### N10: Fix `z.preprocess()` losing type inference with generic functions

When `z.preprocess()` receives a generic preprocessing function, the
TypeScript type inference falls back to `unknown` instead of preserving
the input type. The preprocess signature doesn't forward generic type
parameters. Fix the type signature to preserve generics through
preprocessing.

### M4: Implement schema diffing and migration

Add `z.diff(schemaA, schemaB)` that computes a structural diff between
two schemas: added fields, removed fields, changed types, changed
constraints. Return a structured diff object. Add `z.migrate(data,
diff)` that transforms data validated against `schemaA` to conform to
`schemaB` using the diff plus user-provided transforms for breaking
changes.

### M5: Add OpenAPI 3.1 schema generation

Implement `.toOpenAPI()` on all schema types that produces valid
OpenAPI 3.1 schema. Handle discriminated unions (using `discriminator`
keyword), nullable types, `format` annotations for strings (email,
uuid, uri, date-time), numeric constraints (`minimum`, `maximum`,
`multipleOf`), and recursive schemas via `$ref`.

### M6: Implement tree-shakeable modular imports

Split Zod into modular entry points so bundlers can tree-shake unused
schema types. `import { z } from "zod"` imports everything (backward
compatible); `import { string, object } from "zod/schemas"` imports
only those types. This requires restructuring the module graph, moving
each schema type to its own file, and updating the export structure.

### M7: Add runtime type narrowing with `z.is()` and `z.assert()`

Add `z.is<T>(schema, value): value is T` type guard that returns
`true`/`false` without throwing. Add `z.assert<T>(schema, value): asserts
value is T` for assertion-style checking. Both should work in
conditional expressions and narrow the TypeScript type without
`parse()`/`safeParse()` overhead for already-validated data.

### M8: Implement `z.discriminatedUnion()` auto-detection

Add `z.smartUnion()` that analyzes the member schemas and automatically
detects the discriminant field (the field with literal types that
distinguishes variants). If no discriminant exists, fall back to
regular union behavior. This removes the need to manually specify
the discriminator key.

### M9: Add serialization schema support

Add `.toJSON()` and `z.fromJSON()` that define how a schema's validated
output is serialized and deserialized. Support custom serializers for
dates (`Date` → ISO string), bigints, Maps, Sets, and other
non-JSON-native types. Add `.serialize(data)` and `.deserialize(json)`
methods that use the schema's serialization rules.

### M10: Implement form validation integration

Add `z.toFormRules(schema)` that generates validation rules compatible
with HTML5 constraint validation (pattern, minlength, maxlength, min,
max, required). Return a structured object that form libraries (React
Hook Form, Formik) can consume directly. Support per-field error
message extraction from Zod errors.

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

Add `z.mock(schema)` that generates random valid data matching any Zod
schema. Support seed-based deterministic generation, custom generators
per type, relationship-aware generation (foreign key references between
schemas), and edge-case generation (empty strings, boundary numbers,
deeply nested objects). This requires analyzing all schema types'
constraints, building generators for each type, and composing them.

### W4: Add streaming validation for large datasets

Implement `z.stream(schema)` that validates items in a streaming
fashion (e.g., JSONL files, API paginated results). Support per-item
validation with error collection, partial result emission, backpressure,
and configurable error thresholds (stop after N errors). Work with
Node.js Readable streams and async iterables. Changes span the parse
pipeline, error accumulation, and add a streaming module.

### W5: Implement schema-to-TypeScript code generation

Add a `zod-codegen` companion package that takes a Zod schema (defined
at runtime or from a JSON Schema input) and generates TypeScript source
code with the equivalent `z.*` definitions plus inferred types. Support
round-tripping: schema → codegen → import → equivalent schema. Handle
recursive types, discriminated unions, and custom refinements.

### W6: Add database schema inference and generation

Implement `zod-sql` that bridges Zod schemas to database tables. From
a Zod object schema, generate CREATE TABLE SQL, migration scripts, and
ORM model definitions (for Prisma, Drizzle, Kysely). Support column
types inferred from Zod types, constraints from refinements (min/max →
CHECK), and relationships from nested schemas. This requires type
mapping, SQL generation, and ORM-specific output formatting.

### W7: Implement protocol buffer interop

Add `zod-proto` that converts between Zod schemas and Protocol Buffer
definitions. Generate `.proto` files from Zod schemas with appropriate
proto3 types, and generate Zod schemas from existing `.proto` files.
Handle repeated fields, oneof (→ discriminated union), maps,
well-known types (Timestamp, Duration), and nested messages. This
requires proto file parsing, type mapping, and schema generation.

### W8: Add E2E contract testing framework

Implement a contract testing system where API producers define schemas
and consumers validate against them. Add `z.contract(name, reqSchema,
resSchema)` that creates a named contract. Provide test utilities that
verify both sides conform: producer tests validate responses match the
contract, consumer tests validate requests match. Support versioned
contracts and breaking change detection.

### W9: Implement schema-driven form builder for React

Add `@zod/react-form` that generates complete React form components
from Zod schemas. Infer input types from schema types (string → text
input, number → number input, enum → select, boolean → checkbox,
array → repeatable group). Support custom renderers, validation
integration, error display, conditional fields, and multi-step wizards.
Requires JSX generation, React hook integration, and Zod type analysis.

### W10: Add internationalization for error messages

Implement a comprehensive i18n system for Zod error messages. Support
locale-aware error formatters, pluralization rules, message templates
with interpolation (e.g., "Must be at least {min} characters"), and
per-schema custom messages with i18n keys. Include built-in translations
for common locales (en, es, fr, de, zh, ja). Changes span the error
system, issue creation, error formatting, and add a locale module.
