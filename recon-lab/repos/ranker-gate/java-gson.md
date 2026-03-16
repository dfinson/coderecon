# google/gson

| Field | Value |
|-------|-------|
| **URL** | https://github.com/google/gson |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Small (focused library) |
| **Category** | JSON serialization library |
| **Set** | ranker-gate |
| **Commit** | `dbf0f0416596add88c9d38b3e80b4e44059863ca` |

## Why this repo

- **Single-purpose**: JSON serialization/deserialization for Java. Core
  functionality is compact — one developer can understand the full codebase.
- **Well-structured**: All source under `gson/src/main/java/com/google/gson/`
  with clear separation between the public API (Gson, JsonElement), internal
  streaming (JsonReader, JsonWriter), type adapters, and reflection-based
  binding.
- **Rich history**: 2K+ commits, maintained by Google for 15+ years. Stable
  but still receiving meaningful improvements and bug fixes.
- **Permissive**: Apache-2.0.

## Structure overview

```
gson/src/main/java/com/google/gson/
├── Gson.java              # Main entry point
├── GsonBuilder.java       # Builder pattern configuration
├── JsonElement.java       # JSON tree model (base)
├── JsonObject.java        # JSON object node
├── JsonArray.java         # JSON array node
├── JsonPrimitive.java     # JSON primitive node
├── TypeAdapter.java       # Type adapter base class
├── TypeAdapterFactory.java # Factory for creating type adapters
├── stream/
│   ├── JsonReader.java    # Streaming JSON parser
│   ├── JsonWriter.java    # Streaming JSON writer
│   └── JsonToken.java     # Token types
├── reflect/
│   └── TypeToken.java     # Generic type capture
├── internal/              # Internal implementation
│   ├── bind/              # Built-in type adapter bindings
│   ├── sql/               # SQL type adapters
│   └── ...
└── annotations/           # @SerializedName, @Expose, etc.
```

## Scale indicators

- ~60 Java source files
- ~15K lines of code
- Flat package structure (2 levels)
- Minimal dependencies (none at runtime)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `RecordAdapter.finalize()` swallowing constructor exceptions as `RuntimeException`

When Gson deserializes a Java record and the canonical constructor throws (for example, because a required component fails validation with `Objects.requireNonNull()`), `RecordAdapter.finalize()` in `ReflectiveTypeAdapterFactory` catches the `InvocationTargetException` and rethrows it as a generic `RuntimeException`. A `// TODO: JsonParseException ?` comment in the same catch block acknowledges this is wrong. Fix `finalize()` to rethrow the cause as a `JsonSyntaxException` (with a message that identifies the record class and the argument array), preserving the original exception as the cause so callers receive a Gson-typed exception with useful context instead of an opaque `RuntimeException`.

### N2: Add `@JsonAdapter` support for enum constants

The `@JsonAdapter` annotation works on classes and fields but not on
individual enum constants. A common need is to serialize specific enum
values differently. Add support for `@JsonAdapter` on enum constants
so each constant can specify its own serialization/deserialization
logic.

### N3: Fix `JsonReader` incorrect line number after `skipValue()`

After calling `skipValue()` to skip a large nested JSON object,
subsequent error messages produced by `JsonReader.locationString()` report
the wrong line number. The `lineStart` cursor is not updated when
`skipValue()` advances past newlines inside unquoted values via
`skipUnquotedValue()`, which returns at `'\n'` without incrementing
`lineNumber` or updating `lineStart`. Fix `skipUnquotedValue()` to
track newlines so that `locationString()` always reports accurate
line and column information after a skip. Also add an entry to `CHANGELOG.md` under "Unreleased" documenting the line-number tracking fix in `skipValue()`.

### N4: Fix `GsonTypes.getRawType()` ignoring `TypeVariable` bounds for adapter resolution

In `GsonTypes.getRawType(Type)`, the `TypeVariable` branch always returns `Object.class` regardless of the variable's declared bounds (the comment reads "we could use the variable's bounds, but that won't work if there are multiple"). This causes Gson to resolve type adapters against `Object` even when the variable has an explicit upper bound, such as `T extends Serializable`. Fix the `TypeVariable` branch in `GsonTypes.getRawType()` to call `getRawType(bounds[0])` on the variable's first declared bound (which is always at least `Object` per the JLS) instead of returning `Object.class` unconditionally.

### N5: Fix `JsonWriter` omitting trailing newline in pretty-print mode

When pretty-printing is enabled via `setIndent()`, the output of `JsonWriter` does not end with a trailing newline after the root value closes. Most JSON formatting tools produce a final newline. Fix `JsonWriter.close()` to append a newline character when indentation is active and the root value has been completed.

### N6: Fix `ConstructorConstructor` swallowing constructor exceptions as `RuntimeException`

When Gson instantiates a class via its no-arg constructor and that constructor throws, `ConstructorConstructor` catches the `InvocationTargetException` and rethrows it as a generic `RuntimeException` with a message like "Failed to invoke constructor ... with no args". A `// TODO: JsonParseException ?` comment in the same catch block acknowledges this is wrong. Fix the catch block to rethrow the wrapped cause as a `JsonSyntaxException` (with a message that identifies the class being constructed), preserving the original cause. This gives callers a Gson-typed exception with actionable context instead of an opaque `RuntimeException`.

### N7: Fix `JsonObject.getAsJsonObject()`, `getAsJsonArray()`, and `getAsJsonPrimitive()` throwing unhelpful `ClassCastException`

`JsonObject.getAsJsonPrimitive(memberName)`, `getAsJsonArray(memberName)`, and `getAsJsonObject(memberName)` perform an unchecked cast on the map value. When the stored type does not match, the JVM throws a `ClassCastException` whose message names the source and target classes but does not mention the property name or the actual `JsonElement` value. This makes it hard to diagnose which key in a deeply nested structure caused the error. Fix all three methods to detect the type mismatch before casting and throw a descriptive `JsonSyntaxException` (or `ClassCastException`) that includes the member name and the actual type of the stored value.

### N8: Fix `Gson.fromJson(Reader)` not closing the reader on parse error

When `Gson.fromJson(Reader, Type)` encounters a `JsonSyntaxException` partway through parsing, it propagates the exception without closing the provided `Reader`. Callers using try-with-resources on the `Gson` call itself have no opportunity to close the reader. Fix the method to close the reader in all exceptional paths.

### N9: Fix `LazilyParsedNumber.equals()` not comparing values numerically

`LazilyParsedNumber.equals()` compares the underlying string representation directly, so `new LazilyParsedNumber("1.0").equals(new LazilyParsedNumber("1.00"))` returns `false`. This is inconsistent with `JsonPrimitive.equals()`, which uses `BigDecimal.compareTo()` to treat scale-different decimals as equal. When Gson parses a JSON number, it stores it internally as a `LazilyParsedNumber`, so calling `getAsNumber().equals(other.getAsNumber())` on two primitives with the same logical value but different text can return `false`. Fix `LazilyParsedNumber.equals()` to compare numerically via `BigDecimal.compareTo()` and update `hashCode()` to use a scale-independent representation.

### N10: Fix `JsonPrimitive.getAsInt()` silently truncating out-of-range values

`JsonPrimitive.getAsInt()` delegates to `getAsNumber().intValue()`, which silently narrows `long` or `BigDecimal` values that exceed `Integer.MAX_VALUE` or fall below `Integer.MIN_VALUE`. For example, `new JsonPrimitive(3_000_000_000L).getAsInt()` returns a truncated negative number with no error. This contrasts with `JsonReader.nextInt()`, which validates the cast and throws `NumberFormatException` on overflow. Fix `getAsInt()` (and similarly `getAsShort()` and `getAsByte()`) in `JsonPrimitive` to detect overflow and throw `NumberFormatException` when the value cannot be represented in the target type.

## Medium

### M1: Implement JSON streaming with callback-based token consumption

Add a `JsonStreamer` class in `com.google.gson.stream` that wraps
`JsonReader` and `JsonWriter` to provide a higher-level streaming API.
On the read side, accept a `Reader` (matching `JsonReader`'s constructor)
and a configurable buffer size (currently hardcoded at 1024 in
`JsonReader`), and expose a `stream(Consumer<JsonToken>)` method that
drives the pull loop internally, calling the consumer for each token;
the consumer may throw a checked exception or return early to interrupt
processing. On the write side, add a `streamArray(JsonWriter,
Iterable<T>, TypeAdapter<T>)` utility method that writes each element
one at a time using `beginArray`/`endArray` so callers can serialize
large collections without holding the full list in memory.

### M2: Add null-safety annotations throughout the API

Annotate all public API methods with `@Nullable` and `@NonNull`
(from `org.jspecify.annotations`). Add annotations to `Gson`,
`JsonElement`, `JsonObject`, `JsonArray`, `TypeAdapter`, `TypeToken`,
and all public interfaces. This requires auditing each method's actual
null behavior and fixing any methods whose behavior contradicts the
annotation. Also update the "Requirements" section in `README.md` to document the new `org.jspecify:jspecify` compile-time dependency, add the versioned `<dependency>` entry for `org.jspecify:jspecify` to the root `pom.xml` `<dependencyManagement>` block, and add a `<dependency>` entry (without version) to the `gson/pom.xml` `<dependencies>` block.

### M3: Implement custom `TypeAdapter` composition

Add a `compose(TypeAdapter<T> next)` instance method to the public
`TypeAdapter<T>` class (in `com.google.gson.TypeAdapter`) following the
same pattern as the existing `nullSafe()` method. The method returns a new
`TypeAdapter<T>` that, on write, converts the value to a `JsonElement` via
`this` adapter and then passes that element as input to `next`; on read, it
delegates to `next` first and feeds its `JsonElement` output to `this`. This
enables reusable transformations (e.g., trim strings, clamp numbers) that
compose with any base type adapter without subclassing. Add corresponding
tests in `gson/src/test/java/com/google/gson/TypeAdapterTest.java` covering
string-trim and numeric-clamp examples, and verify null-safety interaction
with `nullSafe()`.

### M4: Add hierarchical `TypeAdapterFactory` registration with priority

Currently all factories registered via `GsonBuilder.registerTypeAdapterFactory()` are stored in a flat list and resolved in insertion order. Add a priority mechanism so factories can declare precedence levels. Factories at higher priority override lower ones for the same type, and the resolution logic in `Gson.getAdapter()` respects the ordering. Update `GsonBuilder` and the internal factory chain accordingly.

### M5: Implement lenient deserialization mode with fallback defaults

Add a `GsonBuilder.setLenientDeserialization()` mode where type mismatches during deserialization do not throw but instead use sensible defaults (zero for numbers, empty string for strings, empty collections for arrays, null for objects). Track each fallback in a `DeserializationWarnings` object accessible from a new `Gson.getLastWarnings()` method. Wire this through `ReflectiveTypeAdapterFactory` and all built-in adapters in `internal/bind/`.

### M6: Add `TypeAdapter` caching diagnostics and statistics

Add the ability to retrieve cache-hit statistics from Gson's internal `TypeAdapter` resolution. Expose a `Gson.getAdapterCacheStats()` method returning hit count, miss count, and the set of types currently cached. This requires instrumenting the `typeTokenCache` (`ConcurrentHashMap`) in `Gson` with atomic hit/miss counters, introducing a `CacheStats` value object, and ensuring the bookkeeping is thread-safe without impacting serialization throughput.

### M7: Implement configurable field naming strategies per class

Currently `FieldNamingStrategy` applies globally to all classes. Add support for per-class naming strategies via a new `@NamingStrategy` annotation on classes, or through `GsonBuilder.registerNamingStrategy(Class<?>, FieldNamingStrategy)`. The per-class strategy should override the global one. Wire the lookup through `ReflectiveTypeAdapterFactory` and the field-binding logic in `internal/bind/`.

### M8: Support optional fields with `java.util.Optional` unwrapping

Add first-class support for `Optional<T>` fields so that `Optional.empty()` serializes as JSON `null` (or is omitted entirely when `serializeNulls` is off) and JSON `null` deserializes as `Optional.empty()` rather than a bare `null` reference. Implement a dedicated `TypeAdapterFactory` in `internal/bind/`, register it by default, and ensure it composes correctly with `@JsonAdapter`, `@SerializedName`, and custom `TypeAdapterFactory` registrations.

### M9: Implement `JsonPath` query support for `JsonElement` trees

Add a `JsonPath` class that evaluates JSONPath expressions (dot notation, bracket notation, wildcards, recursive descent, and array slices) against a `JsonElement` tree. Return results as a `JsonArray` of matched elements. Integrate with `Gson` by adding `Gson.queryTree(JsonElement, String)` and ensure the parser, evaluator, and result collector are fully covered.

### M10: Add `GsonBuilder` validation for conflicting configuration

`GsonBuilder` silently accepts contradictory settings — for example, enabling `serializeNulls()` and then registering a custom serializer that skips nulls, or calling `setDateFormat(String)` and then `setDateFormat(int, int)` (silently discarding the first format). Add a `GsonBuilder.validate()` method that detects common conflicts and ambiguities, returning a list of warnings. Call this automatically during `create()` when `setStrictness(Strictness.STRICT)` has been set on the builder.

## Wide

### W1: Add constraint-based validation during deserialization

Implement a validation framework that runs during deserialization. Add
a `@JsonSchema` annotation that references a JSON schema resource on
the classpath; the schema supports a subset of JSON Schema keywords:
`type`, `required`, `properties`, `minLength`, `maxLength`, `minimum`,
`maximum`, `pattern`, and `enum`. Add a
`GsonBuilder.setSchemaValidation()` option that registers a
`SchemaValidationTypeAdapterFactory`; when enabled, the factory wraps
each adapter and validates the deserialized `JsonElement` against the
schema before returning the result. Schema violations are collected
(not fail-fast) and reported through a new
`SchemaViolationException` that exposes the full list of violation
messages. The schema is parsed from JSON using Gson itself into a
`SchemaNode` model class.

### W2: Implement Gson 3.0 API with sealed interfaces and pattern matching

Design and implement a modernized Gson API that takes advantage of
Java 17+ features: sealed interfaces for `JsonElement` (enabling
pattern matching with `switch`), records for configuration, a builder
API using method chaining with generics, and `Stream<JsonElement>`
support for lazy iteration over JSON arrays. Maintain backward
compatibility through a compatibility layer.

### W3: Add asynchronous serialization and deserialization API

Design and implement `Gson.toJsonAsync()` and `Gson.fromJsonAsync()` methods that return `CompletableFuture` results. Large object graphs should be serialized on a configurable `Executor`, with the streaming layer (`JsonWriter`, `JsonReader`) adapted to work with non-blocking I/O through `AsynchronousByteChannel`. Ensure thread-safety of all adapter resolution, support cancellation, and propagate exceptions correctly through the future chain.

### W4: Implement a Gson plugin system with lifecycle hooks

Add a plugin API allowing third-party libraries to hook into Gson's serialization and deserialization lifecycle. Plugins should be discoverable via `ServiceLoader`, configurable through `GsonBuilder.installPlugin()`, and receive callbacks at key points: before/after serialization of each object, before/after deserialization, on adapter resolution, and on error. The hook dispatch must be efficient and must compose correctly with existing `TypeAdapterFactory` and `ExclusionStrategy` mechanisms.

### W5: Add JSON merge-patch (RFC 7396) and JSON Patch (RFC 6902) support

Implement both JSON Merge Patch and JSON Patch as first-class operations on Gson's `JsonElement` tree. Add `JsonPatch.apply(JsonElement)`, `JsonMergePatch.apply(JsonElement)`, and `JsonDiff.diff(JsonElement, JsonElement)` that produces a JSON Patch. The diff algorithm should handle object key reordering, array element moves, and nested structures efficiently. Integrate with `Gson` via convenience methods and ensure the patch model itself is serializable by Gson.

### W6: Implement annotation-driven polymorphic deserialization

Add a `@JsonPolymorphic` annotation and `@JsonSubType` repeatable annotation that together enable polymorphic deserialization without requiring a `RuntimeTypeAdapterFactory`. The discriminator field name, position (property, wrapper-object, wrapper-array), and subtype mappings should all be declarative. Implement a `PolymorphicTypeAdapterFactory` that reads these annotations, registers itself during `Gson.getAdapter()` resolution, and handles missing or unknown discriminators gracefully with configurable fallback behavior.

### W7: Add ISO-8601 encoding mode to the existing `java.time` type adapter suite

The existing `JavaTimeTypeAdapters` serializes all `java.time` types using a field-based integer encoding that mirrors internal JDK representation (e.g., `Instant` serializes as `{"seconds":1234,"nanos":0}` rather than `"2009-02-13T23:31:30Z"`). This encoding is not human-readable and is incompatible with the ISO-8601 strings produced by other JSON libraries and consumed by most REST APIs. Implement an alternative ISO-8601 string encoding for all supported types (`Instant`, `LocalDate`, `LocalTime`, `LocalDateTime`, `ZonedDateTime`, `OffsetDateTime`, `OffsetTime`, `Duration`, `Period`, `Year`, `YearMonth`, `MonthDay`, `ZoneId`, `ZoneOffset`) using appropriate `DateTimeFormatter` constants. Make ISO-8601 the new default encoding, expose `GsonBuilder.useLegacyJavaTimeAdapters()` to opt back into the old field-based encoding for backward compatibility, and add `GsonBuilder.setTemporalFormat(Class<? extends TemporalAccessor>, DateTimeFormatter)` for per-type format overrides. Introduce a `TemporalAdapterConfig` value class to carry the per-type formatter map through `GsonBuilder` and `Gson`. Wire everything through `JavaTimeTypeAdapters`, `GsonBuilder`, `Gson`, and add comprehensive round-trip tests for every type in both encoding modes.

### W8: Implement structural logging and diagnostics for serialization

Add an opt-in diagnostics mode activated via `GsonBuilder.enableDiagnostics()` that captures a structured trace of every serialization and deserialization operation: which `TypeAdapter` was selected for each type, how long each adapter took, which fields were skipped by exclusion strategies, and any coercions that occurred. Emit traces as `JsonElement` trees consumable by monitoring tools. The tracing must thread through `Gson`, `ReflectiveTypeAdapterFactory`, all built-in adapters, and the streaming layer without affecting performance when disabled.

### W9: Add size-limited and depth-limited deserialization for untrusted input

Implement security-oriented deserialization limits configurable via `GsonBuilder`: maximum input size in bytes, maximum nesting depth, maximum number of object keys, maximum string length, and maximum array size. Enforce limits in `JsonReader` at the streaming level so they cannot be bypassed by custom adapters. Add a `SecurityViolationException` with details about which limit was exceeded, and ensure the limits compose correctly with lenient mode, streaming APIs, and tree-model parsing.

### W10: Implement round-trip fuzz testing infrastructure for Gson

Build an internal testing framework that generates random `JsonElement` trees (objects, arrays, primitives, nulls, nested to arbitrary depth), serializes them with `Gson.toJson()`, deserializes the output back with `Gson.fromJson()`, and asserts structural equality. Cover edge cases: Unicode surrogates, extremely large numbers, deeply nested structures, duplicate keys, and all `JsonPrimitive` numeric types. Integrate this as a reproducible test suite that can be run with a fixed seed and report minimal failing cases.

### N11: Extend root POM `<dependencyManagement>` to cover Guava test dependencies

The root `pom.xml` has a `<dependencyManagement>` section that already pins `junit:junit` and `com.google.truth:truth` centrally, but it does not include `com.google.guava:guava-testlib` or `com.google.guava:guava`. As a result, `gson/pom.xml` declares explicit versions for both guava dependencies (currently `33.5.0-jre`), while other submodules may drift independently. Add `com.google.guava:guava-testlib` and `com.google.guava:guava` entries to the existing `<dependencyManagement>` block in the root `pom.xml`, and remove the explicit `<version>` tags for those two dependencies from `gson/pom.xml`.

### M11: Extend `Troubleshooting.md` with missing problem sections and update cross-references

The current `Troubleshooting.md` covers `ClassCastException`, `InaccessibleObjectException`, `IllegalStateException` token-mismatch errors, and ProGuard/R8 issues, but is missing sections for two common runtime problems: `JsonSyntaxException` caused by malformed input (with symptoms, root causes, and recovery suggestions distinguishing recoverable parse errors from structural mismatches), and `StackOverflowError` from circular object-graph references during serialization (with symptoms, root cause explanation, and workaround patterns using custom `TypeAdapter` or `ExclusionStrategy`). Add these two new sections to `Troubleshooting.md` with `## <a id="...">` anchors and symptoms/reason/solution sub-headings matching the existing format. Also add a note in `GsonDesignDocument.md` under the "Supporting richer serialization semantics than deserialization semantics" section explaining why circular reference detection is not built into the core serializer.

### W11: Create a MkDocs-based documentation site consolidating all Markdown guides

The repository has documentation spread across top-level Markdown files (`README.md`, `UserGuide.md`, `GsonDesignDocument.md`, `Troubleshooting.md`, `ReleaseProcess.md`, `CHANGELOG.md`) with no unified navigation or search. Create a `mkdocs.yml` configuration at the repository root using the Material for MkDocs theme, and add a `docs/` directory that reorganizes the existing content into sections: Getting Started (from `README.md`), User Guide (from `UserGuide.md`), Design (from `GsonDesignDocument.md`), Troubleshooting (from `Troubleshooting.md`), Release Process (from `ReleaseProcess.md`), and Changelog (from `CHANGELOG.md`). Add a GitHub Actions workflow in `.github/workflows/docs.yml` that builds the MkDocs site and deploys it to GitHub Pages on pushes to `main`. Update the root `README.md` to link to the deployed documentation site URL. Ensure all internal cross-references between documents use relative MkDocs links.
