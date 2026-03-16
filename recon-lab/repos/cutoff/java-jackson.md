# FasterXML/jackson-databind

| Field | Value |
|-------|-------|
| **URL** | https://github.com/FasterXML/jackson-databind |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | JSON data-binding |
| **Set** | Cutoff |
| **Commit** | `d6c58d9fd459afaca037767e8705b2373848e5b1` |

## Why this repo

- **Well-structured**: Organized under
  `src/main/java/com/fasterxml/jackson/databind/` with clear
  per-concern packages — `deser/` (deserializers and context),
  `ser/` (serializers and provider), `type/` (Java type resolution),
  `introspect/` (class/annotation introspection), `annotation/`
  (Jackson annotations), `cfg/` (configuration and features),
  `module/` (Module SPI for extensions), `node/` (JsonNode tree model),
  `exc/` (exceptions), and `jsontype/` (polymorphic type handling).
  The central `ObjectMapper` orchestrates all subsystems.
- **Rich history**: 3K+ stars, 7K+ commits. The de-facto standard for
  Java JSON processing. Issues cover deserialization edge cases, type
  resolution, polymorphism, and performance.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
src/main/java/com/fasterxml/jackson/databind/
├── ObjectMapper.java           # Central API: readValue(), writeValue(),
│                               #   convertValue(), treeToValue()
├── ObjectReader.java           # Immutable reader with per-call config
├── ObjectWriter.java           # Immutable writer with per-call config
├── DeserializationContext.java # Runtime context during deserialization
├── SerializerProvider.java     # Runtime context during serialization
├── deser/                      # BeanDeserializer, StdDeserializer,
│                               #   CollectionDeserializer, MapDeserializer,
│                               #   DeserializerCache, ValueInstantiator
├── ser/                        # BeanSerializer, StdSerializer,
│                               #   CollectionSerializer, MapSerializer,
│                               #   SerializerCache, PropertyWriter
├── type/                       # JavaType, TypeFactory, TypeBindings,
│                               #   CollectionType, MapType, ArrayType
├── introspect/                 # BeanDescription, AnnotatedClass,
│                               #   AnnotatedMethod, AnnotatedField,
│                               #   JacksonAnnotationIntrospector
├── annotation/                 # JsonProperty, JsonCreator, JsonIgnore,
│                               #   JsonTypeInfo, JsonSubTypes
├── cfg/                        # MapperConfig, DeserializationConfig,
│                               #   SerializationConfig, MapperFeature,
│                               #   DeserializationFeature, SerializationFeature
├── module/                     # Module, SimpleModule, SetupContext
├── node/                       # ObjectNode, ArrayNode, TextNode,
│                               #   NumericNode, BooleanNode, NullNode
├── jsontype/                   # TypeResolverBuilder, TypeIdResolver,
│                               #   AsPropertyTypeDeserializer
├── exc/                        # MismatchedInputException,
│                               #   InvalidDefinitionException,
│                               #   UnrecognizedPropertyException
└── util/                       # StdConverter, Named, ClassUtil,
                                #   TokenBuffer, LRUMap
```

## Scale indicators

- ~600 Java source files under src/main/java/
- ~150K lines of code
- Deep package hierarchy (4–5 levels)
- Dependencies: jackson-core, jackson-annotations

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix ObjectMapper.readValue not propagating DeserializationFeature overrides to nested contexts

When calling `ObjectReader.readValue()` with per-read feature overrides
(e.g., `reader.without(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)`),
the `DeserializationConfig` carrying those overrides must be passed to
`DeserializationContexts.DefaultImpl.createContext()`. If the wrong
config is supplied, nested objects deserialized during the same read
revert to mapper-level defaults. Fix the context construction in
`ObjectReader._deserializationContext()` and the `DeserializationContexts`
factory to carry forward per-read feature overrides into the created
`DeserializationContextExt` instance.

### N2: Fix TypeFactory caching partially-resolved types for self-referential generics

`TypeFactory` uses `ResolvedRecursiveType` as a placeholder when it
detects a type that references itself (e.g., `Comparable<Foo>` where
`Foo implements Comparable<Foo>`). After resolution, `resolveSelfReferences()`
patches the placeholder, but the code comment at the caching step
acknowledges that types containing a partially-resolved
`ResolvedRecursiveType` are skipped from the cache. This causes repeated
expensive resolution on every access. Fix the caching logic in
`TypeFactory._fromClass()` to correctly cache fully-resolved
self-referential types once `resolveSelfReferences()` has completed,
verifying that no unresolved `ResolvedRecursiveType` references remain.

### N3: Fix ObjectNode.equals not comparing field order for ordered nodes

`ObjectNode.equals()` delegates entirely to the underlying `_children`
map's `equals()`, which treats two maps with the same entries as equal
regardless of insertion order. When nodes have been constructed or
sorted intentionally (e.g., after `SORT_PROPERTIES_ALPHABETICALLY`
serialization round-trips), users cannot detect order differences via
`equals()`. Fix `ObjectNode.equals()` and the protected
`_childrenEqual()` helper to compare entries in iteration order so
that two `ObjectNode` instances with the same keys and values but
different field ordering are considered unequal. Provide a separate
`equalsIgnoreOrder(ObjectNode)` helper that preserves the old
unordered semantics.

### N4: Fix @JsonCreator static factory method ignored when constructor also present

When a class has both a `@JsonCreator` annotated static factory method
and a `@JsonCreator` annotated constructor, the introspection logic in
`BasicBeanDescription` silently picks the constructor and ignores the
factory method without warning. Fix the creator detection in
`introspect/` to throw `InvalidDefinitionException` on ambiguous
`@JsonCreator` annotations.

### N5: Fix DeserializationContext not including field path in MismatchedInputException

When deserialization fails on a deeply nested field, the thrown
`MismatchedInputException` only reports the immediate field name
without the full JSON path (e.g., just `"age"` instead of
`"person.address.age"`). Fix the path tracking in
`DeserializationContext` to accumulate the full property path using
the `JsonStreamContext` stack. Also add a corresponding entry to
`release-notes/VERSION` documenting the improved error message
format as a minor behavior change.

### N6: Fix SerializationContext not invalidating cached serializers on view change

Serializers resolved with `@JsonView` active are incorrectly cached
and reused for requests without a view, causing fields to be omitted.
Fix the cache key computation in `SerializerCache` to include the
active view class in the lookup key so that view-specific and
view-less serializations use separate cache entries.

### N7: Fix MapSerializer not respecting @JsonPropertyOrder on map entries

When serializing a `Map` with `@JsonPropertyOrder` on the containing
bean, map entries are emitted in iteration order rather than the
specified property order. Fix `MapSerializer.serializeEntries()` to
sort entries when the enclosing bean has property-order metadata
available through the `SerializationContext`.

### N8: Fix TypeIdResolver.idFromValue receiving proxy instead of actual object

When serializing objects behind JDK proxies (e.g., lazy-loaded JPA
entities), `TypeIdResolver.idFromValue()` receives the proxy object,
and `getClass()` returns the proxy class rather than the actual entity
class. Fix the polymorphic type serialization path to unwrap common
proxy types before calling `idFromValue()`.

### N9: Fix @JsonUnwrapped not working with @JsonCreator constructors

Deserialization of beans with both `@JsonUnwrapped` properties and a
`@JsonCreator` constructor throws `InvalidDefinitionException`. The
`BeanDeserializer` setup does not merge unwrapped properties into the
creator parameter resolution. Fix the property merging logic in
`deser/BeanDeserializer` to include unwrapped properties when
building creator-based deserializers.

### N10: Fix ObjectReader.withValueToUpdate not applying to collection types

`ObjectReader.withValueToUpdate(existingList)` ignores the existing
collection and constructs a new list instead of appending to it. The
`CollectionDeserializer` does not check for the update value in the
deserialization context. Fix `CollectionDeserializer.deserialize()` to
detect and populate the existing collection when a value-to-update is
set.

## Medium

### M1: Implement constructor parameter name discovery without @JsonProperty

Add automatic constructor parameter name resolution using the Java 8+
`-parameters` compiler flag or local-variable debug info. When
`@JsonProperty` is absent, use `java.lang.reflect.Parameter.getName()`
to match JSON keys to constructor parameters. Requires changes to
`introspect/AnnotatedConstructor`, `BasicBeanDescription` creator
detection, `BeanDeserializerFactory`, and `DeserializationConfig` for a
feature flag to enable/disable the behavior.

### M2: Add JSON Schema generation from Jackson-annotated classes

Implement `ObjectMapper.generateJsonSchema(Class<?>)` that produces a
JSON Schema (draft 2020-12) from bean metadata: required fields from
`@JsonProperty(required=true)`, type mappings, `@JsonPropertyDescription`
for descriptions, `@JsonFormat` for format hints, and polymorphic type
schemas from `@JsonTypeInfo`. Changes span a new `schema/` sub-package,
`introspect/BeanDescription` for metadata extraction, `type/TypeFactory`
for type mapping, and `ObjectMapper` for the entry point method. Also
add an optional `jackson-databind-jsonschema` dependency entry in
`pom.xml` and update `README.md` to document the JSON Schema
generation feature with usage examples.

### M3: Implement streaming large-collection serialization with backpressure

Add `ObjectWriter.writeValuesAsArray(OutputStream, Iterator)` that
serializes large collections without buffering the entire collection in
memory. Support backpressure by pausing iteration when the output buffer
is full. Requires changes to `ObjectWriter`, `ser/SequenceWriter`,
`SerializationContext` for streaming context, and `cfg/SerializationConfig`
for buffer-size configuration.

### M4: Add multi-level sealed class hierarchy support in deserialization

`JacksonAnnotationIntrospector.findSubtypesByPermittedSubclasses()`
already auto-registers direct permitted subclasses of a sealed class,
but it only inspects one level. When a permitted subclass is itself
sealed (nested sealed hierarchy), its own permitted subclasses are
silently ignored, leaving them unregistered as subtypes. Extend the
implementation to recursively walk sealed hierarchies: inspect each
permitted subclass for `isSealed()`, recurse into its permitted
subclasses, and register all reachable concrete leaf types. Changes
span `introspect/JacksonAnnotationIntrospector` for recursive
detection, `jsontype/impl/` type resolver builders for multi-level
candidate sets, `deser/BeanDeserializerFactory` for correct candidate
filtering of intermediate sealed types, and `type/TypeFactory` for
accurate sealed-type introspection.

### M5: Implement ObjectMapper.copyWith for selective configuration override

Add `ObjectMapper.copyWith(Consumer<MapperBuilder>)` that creates a
new mapper sharing the base configuration but with targeted overrides
applied via the builder consumer. Unlike `copy()`, this avoids full
reconfiguration and preserves cached serializer/deserializer state
where the override does not affect resolution. Changes span
`ObjectMapper.java` for the new method, `cfg/MapperBuilder.java` for
partial-state cloning, `cfg/BaseSettings.java` for selective field
copying, and `ser/SerializerCache.java` and
`deser/DeserializerCache.java` for shared-vs-private cache decisions.

### M6: Add property-name aliasing with conflict detection

Implement `@JsonAlias` support during serialization (currently only
works for deserialization) so aliases can be used as alternative output
names based on a `SerializationFeature` flag. Add conflict detection
that throws `InvalidDefinitionException` when multiple properties
resolve to the same alias. Changes span `ser/BeanPropertyWriter.java`
for alias-aware output name selection, `introspect/BeanDescription`
for alias metadata extraction, `cfg/SerializationConfig` for the
feature flag, and `ser/BeanSerializerFactory` for conflict validation
during serializer construction.

### M7: Add contextual serializer support for collection and set container types

`MapSerializer` and `CollectionDeserializer` already implement
`createContextual()`, but `CollectionSerializer` (and by extension
`IndexedListSerializer`, `EnumSetSerializer`) does not. Annotations
on container fields (e.g., `@JsonFormat` on a `List<Date>` field) are
therefore not propagated to element serializers during serialization.
Add `createContextual(SerializationContext, BeanProperty)` to
`ser/jdk/CollectionSerializer` so field-level annotations are resolved
and forwarded to the element serializer. Similarly add
`createContextual` to `deser/jdk/MapDeserializer` for annotation
propagation to key and value deserializers. Changes also touch
`ser/SerializationContext` and `deser/DeserializationContext` for
contextual resolution chaining.

### M8: Add CSV-style flat-mapping for nested objects

Implement `@JsonFlatten(separator=".")` that flattens nested object
properties into dot-separated keys during serialization (e.g.,
`address.city`) and reconstructs nested objects during deserialization.
Changes span a new `@JsonFlatten` annotation in `annotation/`, property
resolution in `ser/BeanPropertyWriter`, `deser/BeanDeserializer` for
unflatten logic, and `introspect/BeanDescription` for delimiter-aware
property discovery.

### M9: Implement typed exception deserialization with error-body mapping

Add `ObjectReader.onFailure(Class<ExcType>, Function<ExcData, ExcType>)`
that maps structured JSON error bodies to typed exceptions during
deserialization failures. Support multiple error shapes keyed by HTTP
status or error-code field. Changes span `ObjectReader`,
`DeserializationContext` error handling, `exc/MismatchedInputException`,
and a new `deser/ErrorBodyDeserializer`.

### M10: Add per-type serialization/deserialization interceptors

Implement `Module.addSerializationInterceptor(Class, Interceptor)` and
`Module.addDeserializationInterceptor(Class, Interceptor)` that allow
modules to hook into the serialization and deserialization lifecycle
for specific types. Interceptors receive the value before/after
processing and can modify or wrap it. Changes span
`module/SimpleModule.java` for the registration API,
`ser/BeanSerializer.java` for pre/post serialization hooks,
`deser/bean/BeanDeserializer.java` for pre/post deserialization hooks,
and `cfg/SerializerFactoryConfig.java` /
`cfg/DeserializerFactoryConfig.java` for interceptor storage.

## Wide

### W1: Implement a modular type system with pluggable type resolvers

Redesign the type resolution subsystem to support pluggable type
resolvers: `TypeResolverPlugin` SPI for custom type mapping (e.g.,
Kotlin data classes, Scala case classes, Lombok builders), compose
multiple resolvers with priority ordering, and cache resolved types
across mappers. Changes span `type/TypeFactory`, `type/TypeBindings`,
`introspect/AnnotatedClass`, `deser/BeanDeserializerFactory`,
`ser/BeanSerializerFactory`, `module/Module`, and a new
`type/spi/` sub-package.

### W2: Add async deserialization pipeline with non-blocking I/O

Implement `ObjectMapper.readValueAsync(AsyncInputStream)` returning
`CompletableFuture<T>` that deserializes JSON from non-blocking
streams. Support chunked input, streaming array elements via
`Flow.Publisher<T>`, and cancellation. Changes span `ObjectMapper`,
`ObjectReader`, `DeserializationContext` for async state management,
`deser/BeanDeserializer` for resumable parsing, `type/` for reactive
type detection, and a new `async/` sub-package.

### W3: Implement full JSON Merge Patch (RFC 7396) and JSON Patch (RFC 6902) support

Add `ObjectMapper.mergePatch(target, patch)` for RFC 7396 and
`ObjectMapper.applyPatch(target, patchOps)` for RFC 6902 (add, remove,
replace, move, copy, test). Support patching at both `JsonNode` level
and typed-bean level with automatic deserialization/reserialization.
Changes span `ObjectMapper`, `node/ObjectNode`, `node/ArrayNode`,
a new `patch/` sub-package with operation types, `DeserializationContext`
for partial updates, and `ser/` for diff generation.

### W4: Implement annotation-driven data validation during deserialization

Integrate Bean Validation (JSR 380) annotations (`@NotNull`, `@Size`,
`@Pattern`, `@Min`, `@Max`) into the deserialization pipeline so
validation errors are collected and reported as structured
`ConstraintViolationException` before returning the deserialized
object. Support nested validation, cross-field validation groups, and
custom validators. Changes span `deser/BeanDeserializer`,
`deser/SettableBeanProperty`, `DeserializationContext`,
`introspect/BeanDescription`, `cfg/DeserializationConfig`, and a new
`validation/` sub-package.

### W5: Add schema evolution and versioned deserialization support

Implement `@JsonVersioned(version=2)` and a `SchemaEvolutionModule`
that supports deserializing older JSON formats: field renames
(`@JsonAlias`-based migration), type changes (converters), removed
fields (ignored with warnings), added fields (defaults), and structural
changes (flatten/nest transforms). Changes span a new `evolution/`
sub-package, `deser/BeanDeserializerFactory` for version-aware
instantiation, `introspect/` for version metadata, `annotation/` for
new annotations, `module/` for the evolution module, and `cfg/` for
version configuration.

### W6: Implement a reflectionless code-generated serialization mode

Add `jackson-databind-codegen` annotation processor that generates
`JsonSerializer` and `JsonDeserializer` implementations at compile
time, eliminating runtime reflection. The processor must handle
`@JsonProperty`, `@JsonCreator`, `@JsonTypeInfo`, `@JsonUnwrapped`,
and custom serializers. At runtime, a `CodegenModule` registers
generated serializers with priority over reflective ones. Changes
span a new annotation processor module, `module/SimpleModule` for
registration, `ser/BeanSerializerFactory` and
`deser/BeanDeserializerFactory` for fallback, and `introspect/` for
metadata extraction at compile time.

### W7: Add comprehensive ObjectMapper diagnostics and debugging tools

Implement `ObjectMapper.diagnose(Class<?>)` that produces a structured
report: resolved serializer/deserializer chain, property mappings,
type bindings, active features affecting the type, applicable mixins,
and potential issues (ambiguous creators, conflicting annotations).
Add `ObjectMapper.explain(json, Class<?>)` that traces the
deserialization path step by step. Changes span `ObjectMapper`,
`ser/SerializerCache`, `deser/DeserializerCache`,
`introspect/BeanDescription`, `type/TypeFactory`, `cfg/MapperConfig`,
and a new `diagnostic/` sub-package.

### W8: Implement multi-format ObjectMapper supporting JSON, YAML, CBOR, and XML

Extend `ObjectMapper` to support format negotiation: auto-detect input
format from content, route to the appropriate `JsonFactory`, and apply
format-specific configuration. Add `ObjectMapper.forFormat(Format)` and
`ObjectMapper.autoDetect()`. Ensure all serializers and deserializers
work uniformly across formats. Changes span `ObjectMapper`,
`ObjectReader`, `ObjectWriter`, `cfg/MapperConfig` for format-specific
features, `ser/SerializerProvider`, `deser/DeserializationContext`,
and a new `format/` sub-package.

### W9: Implement lazy deserialization with proxy-backed object graphs

Add `@JsonLazy` annotation that deserializes marked fields on first
access via JDK dynamic proxies. Support configurable prefetch depth,
cycle detection in lazy graphs, serialization of partially-loaded
graphs, and cache eviction for memory management. Changes span
`deser/BeanDeserializer`, `deser/SettableBeanProperty`,
`introspect/BeanDescription`, `DeserializationContext`,
`ser/BeanSerializer` for lazy-aware serialization, `annotation/`
for the new annotation, and a new `lazy/` sub-package.

### W10: Add first-class Kotlin support module

Implement a `KotlinModule` that handles: data class deserialization
with default parameter values, `null` safety enforcement matching
Kotlin's type system (`String` vs `String?`), `value class` (inline
class) unwrapping, sealed class polymorphism via `@JsonTypeInfo`
inference, `companion object` factory detection, and coroutine
`suspend` function serialization. Changes span a new
`module/kotlin/` sub-package, `introspect/` for Kotlin metadata
reading, `deser/ValueInstantiator` for default-value constructors,
`type/TypeFactory` for Kotlin type mapping, and `ser/` for value
class handling.

### N11: Fix release-notes/VERSION not separating breaking changes from bug fixes

The `release-notes/VERSION` file lists all changes in a flat format
without distinguishing breaking API changes from bug fixes and new
features. When users upgrade between versions, they must read every
entry to identify breaking changes. Restructure `release-notes/VERSION`
to use subsection headers (`### Breaking Changes`, `### Bug Fixes`,
`### New Features`) for each release. Also add a
`release-notes/MIGRATION.md` template that documents
migration steps for breaking changes, and update `CONTRIBUTING.md`
(under `.github/CONTRIBUTING.md` if present) to require contributors
to categorize their changelog entries.

### M11: Add CodeQL security scanning and update pom.xml security metadata

The `.github/workflows/codeql-analysis.yml` workflow runs CodeQL
but does not include custom query suites for Jackson-specific
vulnerability patterns (unsafe deserialization, type coercion
bypasses). Add a `.github/codeql/jackson-queries.ql` custom query
that detects unvalidated `TypeResolverBuilder` configurations. Update
`pom.xml` to add `<security>` metadata with CVE disclosure contact
and security policy URL. Update the existing `SECURITY.md` to document
the vulnerability reporting process specific to deserialization-related
security issues. Update `.github/dependabot.yml` to add weekly
scanning for Maven dependency vulnerabilities (currently only
`github-actions` is scanned on a monthly schedule) and update the
`<dependencyManagement>` section in `pom.xml` to pin all test
dependencies with version ranges that exclude known CVEs.

### W11: Overhaul build configuration, CI workflows, and project documentation

Comprehensively update all non-code project files for the Jackson 3.x
release. Restructure `pom.xml` to document the Java baseline rationale
in the `<properties>` comments, add `maven-enforcer-plugin` rules for
minimum Maven/JDK versions, and configure reproducible builds via
`project.build.outputTimestamp`. Update `.github/workflows/main.yml`
to add `dep_build_v3.yml`-style dependent-build trigger comments and
add release-tagging steps. Restructure `release-notes/` to add
per-major-version migration guides (`release-notes/migration-3.x.md`)
with code examples comparing 2.x and 3.x API usage. Update `README.md`
to document the 3.x package rename (`tools.jackson` vs
`com.fasterxml.jackson`), add a feature comparison table between 2.x
and 3.x, and add links to the migration guide. Update `SECURITY.md`
with the Jackson security team contact and supported-version matrix.
Add a `.github/CONTRIBUTING.md` with guidelines on the module system
(`module-info.java`), testing requirements, and the release process.
