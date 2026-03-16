# colinhacks/zod

| Field | Value |
|-------|-------|
| **URL** | https://github.com/colinhacks/zod |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Small (focused library) |
| **Category** | Schema validation library |
| **Set** | ranker-gate |
| **Commit** | `c7805073fef5b6b8857307c3d4b3597a70613bc2` |

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
packages/zod/src/
├── v3/                        # Zod v3 (stable)
│   ├── ZodError.ts            # Error types and formatting
│   ├── types.ts               # Core schema types (ZodString, ZodNumber, ZodObject, etc.)
│   ├── errors.ts              # Default error map
│   ├── helpers/               # Utility types and functions
│   │   ├── parseUtil.ts       # Parse context and issue handling
│   │   ├── partialUtil.ts     # Deep partial type utilities
│   │   ├── typeAliases.ts     # Shared type aliases
│   │   └── util.ts            # General utilities
│   ├── external.ts            # Public API re-exports
│   ├── standard-schema.ts     # Standard schema interop
│   └── index.ts               # Entry point
├── v4/                        # Zod v4
│   ├── core/                  # Core schemas, parse, checks, errors, JSON schema, registries
│   ├── classic/               # Classic API layer (compat, coerce, from-json-schema)
│   ├── mini/                  # Minimal API layer
│   └── locales/               # Error message localization (~50 languages)
└── index.ts                   # Root entry point
```

## Scale indicators

- ~60 TypeScript source files (v3 + v4)
- ~15K lines of code
- Monorepo with v3 and v4 under `packages/zod/`
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

### N2: Fix `deepPartialify` not unwrapping `ZodDefault` before recursing

The `deepPartialify` helper in `types.ts` handles `ZodObject`, `ZodArray`,
`ZodOptional`, `ZodNullable`, and `ZodTuple` but has no branch for
`ZodDefault`. When a nested object field is wrapped with `.default({})`,
calling `.deepPartial()` on the parent stops recursion at the `ZodDefault`
layer and the inner object's properties remain required. Add a `ZodDefault`
branch to `deepPartialify` that calls `removeDefault()` and recurses into
the unwrapped inner schema.

### N3: Fix error path for discriminated union with nested objects

When validation fails inside a nested object within a discriminated union,
the error path does not include the discriminator key's value. The error
message says "Invalid input" without indicating which branch of the union
failed. Include the matched discriminator value in the error path so
users can identify which variant had the validation failure. Also add a
new section to `packages/docs/content/error-customization.mdx`
documenting the discriminator path behavior with a before/after
example showing the improved error path output for discriminated unions.

### N4: Fix `ZodCatch.create()` not accepting error-context callback

The chainable `.catch(fn)` method on any schema accepts a callback
with signature `(ctx: { error: ZodError; input: Input }) => Output`,
giving callers access to the validation error and original input when
computing the fallback. However, the static `ZodCatch.create()` factory
only accepts `catch: Output | (() => Output)` — a zero-argument function
— so callers using the factory directly cannot access the error context.
Fix `ZodCatch.create()` to accept the same full callback signature as
`.catch()`, updating the `RawCreateParams` intersection type and the
`catchValue` wrapper inside the factory.

### N5: Fix `.describe()` metadata dropped by `.array()` wrapper

Calling `.describe("label")` on a schema and then calling `.array()`
discards the description. The `ZodArray.create(this)` call inside the
`.array()` method does not pass `this._def` as a second argument, so
`processCreateParams` never sees the description. By contrast,
`.optional()`, `.nullable()`, and `.default()` all pass `this._def` and
correctly preserve the description. Fix `.array()` — and similarly
`.readonly()`, the only other `ZodType` instance wrapper method that
omits `this._def` (`ZodReadonly.create(this)`) — to forward `this._def`
so that descriptions propagate through these wrappers consistently.

### N6: Fix `ZodObject.merge()` silently discarding `description` and `errorMap`

When merging two `ZodObject` schemas where the incoming schema has a
`.describe()` annotation or a custom `errorMap`, the resulting merged
schema drops these properties. The `merge()` method in `types.ts`
constructs a new `ZodObject` with only `unknownKeys`, `catchall`,
`shape`, and `typeName` but omits `description` and `errorMap` from
either definition. Fix `merge()` to propagate the incoming schema's
metadata fields.

### N7: Fix `z.preprocess()` transform exceptions bypassing Zod error formatting

When the transform function passed to `z.preprocess()` throws a
JavaScript error (rather than using `ctx.addIssue()`), the exception
propagates raw out of `_parse()` and is never converted to a
`ZodError`. Callers who expect `safeParse` to always return a result
object instead receive an uncaught exception. Fix the preprocess branch
in `ZodEffects._parse()` in `types.ts` to wrap `effect.transform()` in
a try/catch and, on caught errors, add a `custom` issue via
`addIssueToContext` so the exception surfaces as a structured validation
failure. Apply the same fix to both the synchronous and async
preprocess code paths.

### N8: Fix sparse array holes treated identically to explicit `undefined` values

`ZodArray._parse()` spreads the input via `[...ctx.data]` before
iterating, which converts sparse holes to `undefined`. This loses the
distinction between an intentional `undefined` element and a missing
sparse-array index. A schema like `z.array(z.number().optional())`
silently accepts `[1,,3]` treating the hole as valid optional input,
with no indication that a hole existed. Add an explicit pre-iteration
check using `!Object.hasOwn(ctx.data, i)` for each index so that holes
are detected before element validation runs and emit a dedicated
`invalid_type` issue with `received: "hole"` that identifies the
missing sparse index by its path position.

### N9: Fix `ZodLiteral` equality check failing for `NaN` literal values

`ZodLiteral._parse()` uses strict equality (`!==`) to compare the input
against the stored literal value. Since `NaN !== NaN` in JavaScript,
`z.literal(NaN)` always rejects `NaN` inputs even though the value
matches the schema intent. Fix the comparison in `ZodLiteral._parse()`
in `types.ts` to use `Object.is()` so that `NaN` literal schemas
correctly accept `NaN` inputs.

### N10: Fix `.pipe()` in v3 accepting incompatible schemas without a type error

In v3, the `.pipe()` method on `ZodType` is declared as
`pipe<T extends ZodTypeAny>(target: T): ZodPipeline<this, T>`. The
constraint `ZodTypeAny` (i.e. `ZodType<any, any, any>`) places no
restriction on `T`'s input type, so expressions like
`z.string().pipe(z.number())` compile without error even though the
output of `z.string()` (`string`) is incompatible with the input of
`z.number()` (`number`). Tighten the constraint in
`packages/zod/src/v3/types.ts` so that `T` must be a
`ZodType<any, any, Output>` where `Output` is the current schema's
output type (i.e. `this["_output"]`), and update the `ZodPipeline.create`
overload accordingly. Add a compile-time test in the v3 pipeline test
file asserting that chaining incompatible schemas is a TypeScript error.

## Medium

### M1: Add closest-match error surfacing to `ZodUnion` validation failures

When none of a `ZodUnion`'s branches match, it currently collects errors
from every member schema into a single `invalid_union` issue, which
overwhelms users when the union has many branches. Implement a scoring
heuristic in `ZodUnion._parse()` that identifies the branch whose
validation progressed furthest (fewest or latest-path issues) and
surfaces only that branch's errors. Add a `closest_match` option to the
union definition, a new `invalid_union_closest` code in `ZodIssueCode`
in `ZodError.ts`, and a default message in `errors.ts`. Update
`packages/docs/content/error-formatting.mdx` to add a "Union errors"
subsection demonstrating how the new `invalid_union_closest` issue code
appears in formatted output and how to use `.format()` and `.flatten()`
with closest-match union errors.

### M2: Add size constraint methods to `ZodMap`

`ZodSet` supports `.min()`, `.max()`, `.size()`, and `.nonempty()` with
`minSize`/`maxSize` fields in its definition, but `ZodMap` has no size
constraints at all. Add `minSize` and `maxSize` optional fields to
`ZodMapDef`, implement size checks in `ZodMap._parse()` using the
existing `too_small`/`too_big` issue codes, and add chainable `.min()`,
`.max()`, `.size()`, and `.nonempty()` methods mirroring the `ZodSet`
API. Update the `map` type in the size-related issue type unions in
`ZodError.ts` and add default error messages in `locales/en.ts`.

### M3: Add multi-key discriminator support to `ZodDiscriminatedUnion`

The current `ZodDiscriminatedUnion` accepts only a single `discriminator`
string key. Add support for an array of discriminator keys so that
multi-field discrimination works (e.g., discriminating on both `type`
and `version`). Update the `getDiscriminator()` helper to resolve
compound keys, modify the discriminator map construction in
`ZodDiscriminatedUnion.create()` to build a nested lookup, and adjust
`_parse()` to match on all keys. Add an error for partial discriminator
matches in `ZodError.ts` and a default message in `locales/en.ts`.

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

### M6: Implement a z.recursive() builder for self-referential schemas

`z.lazy()` requires an awkward double annotation for recursive schemas:
a separate interface declaration and an explicit `z.ZodType<T>` variable
annotation. Implement a `z.recursive<T>()` builder that uses the
existing `createTransparentProxy` infrastructure to pass a typed
self-reference into the callback, so callers only need a single type
parameter. The builder signature should be
`z.recursive<T>(fn: (self: ZodType<T>) => ZodType<T>): ZodType<T>`,
accepting a function that receives a proxy to the schema being defined.
Add the implementation to `packages/zod/src/v4/classic/schemas.ts`
following the pattern of existing builders, export it from
`external.ts`, and add tests in the `classic/tests/` directory covering
simple self-reference and mutually-recursive patterns.

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

### M9: Add constraint methods to `ZodSymbol`

`ZodSymbol` currently only validates that the input has type `symbol`
with no further constraints. Add `.hasDescription(desc)` that validates
`Symbol.description` matches a given string, `.oneOf(symbols)` that
restricts to a specific set of well-known symbols, and `.global(key)`
that validates the input equals `Symbol.for(key)`. Update the
`ZodSymbolDef` interface in `types.ts` with a checks array, add parse
logic mirroring the check-iteration pattern used by `ZodString`, and
add a new `invalid_symbol` issue code in `ZodError.ts` with a default
message in `locales/en.ts`.

### M10: Add coercion failure detail tracking to `ZodError`

When a coerced schema like `z.coerce.number()` receives a non-numeric
string, the error says "Expected number, received NaN" — the original
pre-coercion value is lost. Add an `invalid_coercion` variant to
`$ZodIssueCode` in `packages/zod/src/v4/core/errors.ts` that carries
the original raw value and the target coercion type. Update the coercion
branches in `$ZodString`, `$ZodNumber`, `$ZodBigInt`, `$ZodBoolean`, and
`$ZodDate` in `packages/zod/src/v4/core/schemas.ts` to emit this issue
code instead of `invalid_type` when coercion fails. Add a default
message in `packages/zod/src/v4/core/errors.ts` and update
`packages/zod/src/v4/core/parse.ts` to include the raw value in the
issue context. Also update the "Coercion" subsection of
`packages/docs/content/api.mdx` to document the new `invalid_coercion`
error code, explain when it is emitted instead of `invalid_type`, and
add a code example showing how to access the original raw value from the
issue context.

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

### W5: Extend the locale system with fallback chains, pluralization helpers, and per-schema i18n keys

The existing locale system in `packages/zod/src/v4/locales/` provides
51 language files and a `$ZodErrorMap` interface, but lacks several
advanced i18n capabilities. Extend it as follows. Add locale fallback
chains to `packages/zod/src/v4/core/config.ts` so that `config()` can
accept an ordered array of `$ZodErrorMap` functions and the
`finalizeIssue` helper in `packages/zod/src/v4/core/util.ts` walks
the chain until a non-null message is produced. Add a shared
pluralization utility (e.g. `plural(n, one, other)`) exported from
`packages/zod/src/v4/locales/index.ts` and adopt it in at least the
`en.ts`, `de.ts`, `fr.ts`, `es.ts`, `pt.ts`, `zh-CN.ts`, `ja.ts`,
and `ko.ts` locale files where count-sensitive phrasing such as "1
character" vs "2 characters" currently uses ad-hoc ternaries. Add an
optional `i18nKey` field to the `$ZodRawIssue` base type in
`packages/zod/src/v4/core/errors.ts` so that schema definitions can
attach a stable lookup key; update the `finalizeIssue` flow to pass
this key to locale map functions, and update the `$ZodErrorMap`
signature to receive it. Add tests covering fallback-chain resolution,
pluralization edge cases (n=0, n=1, n=2), and i18n key propagation.

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
defaults, and branded types. Requires schema introspection, source
string construction, and import resolution.

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

## Non-code focused

### N11: Improve `vitest.config.ts` coverage configuration and extend `biome.jsonc` file inclusion to cover JSON files

The root `vitest.config.ts` already has a `coverage` block with the `v8`
provider and reporters `["text", "json", "html"]`, but it is missing two
improvements: the `lcov` reporter (required for tools that consume LCOV
format, such as Codecov) is not included in the `reporter` array, and there
is no `thresholds` configuration so contributors receive no feedback when
coverage drops below an acceptable level. Update the `coverage` block to add
`"lcov"` to the `reporter` array and add a `thresholds` block setting a
minimum of 80% for `branches`. Additionally, the `biome.jsonc` file
configures a `json.formatter` block (setting `trailingCommas: "none"`) but
the `files.include` array only specifies TypeScript patterns
(`**/*.ts`, `**/*.mts`, `**/*.cts`), so JSON and JSONC files are never
processed by biome and the JSON formatter settings are dead configuration.
Extend `files.include` to add `"**/*.json"` and `"**/*.jsonc"` entries so
that biome actually formats JSON files in the project according to the
configured settings.

### M11: Add TypeScript minimum version declaration and CI drift guard to the CI workflow

The `.github/workflows/test.yml` CI workflow tests against TypeScript
`"5.5"` and `"latest"` in its matrix, but `packages/zod/package.json`
has no `peerDependencies` entry declaring the minimum supported TypeScript
version. Add a `peerDependencies` field to `packages/zod/package.json`
declaring `"typescript": ">=5.5"` so the minimum version is explicit
and machine-readable. Then add a new job to `.github/workflows/test.yml`
that reads the declared minimum TypeScript version from
`packages/zod/package.json` using `jq` and asserts that the `test-node`
matrix always includes that version as one of its tested values, failing
the build if the declared minimum ever drifts from the tested versions.
The new job should depend on `test-node` completing successfully before
it runs its assertion.

### W11: Overhaul project documentation to accurately reflect the v3/v4 dual-version architecture

The `README.md` at the repository root directs users to `zod.dev/api`
for documentation but does not mention the v3/v4 split, the monorepo
structure under `packages/`, or the different import paths (`zod` vs
`zod/v4` vs `zod/mini`). Update `README.md` to add a "Packages" section
explaining the monorepo layout, and a version migration callout. Note
that `check-circular` and `lint` are jobs within the single `test.yml`
workflow, not separate workflows, so no additional CI badges are needed. Update `CONTRIBUTING.md` to document the pnpm workspace
setup, the `vitest` test runner (replacing references to older tooling),
and add a "Docs development" section explaining the `packages/docs/`
Next.js site. Review and update the `packages/docs/content/api.mdx` and
`packages/docs/content/error-customization.mdx` doc pages to ensure all
code examples reference the correct import paths for both v3 and v4 and
that the `z.coerce` section in `api.mdx` accurately documents the current
coercion behavior including what error codes are emitted when coercion fails.
