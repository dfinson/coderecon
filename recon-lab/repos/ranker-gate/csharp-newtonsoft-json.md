# JamesNK/Newtonsoft.Json

| Field | Value |
|-------|-------|
| **URL** | https://github.com/JamesNK/Newtonsoft.Json |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Medium (multi-module project) |
| **Category** | JSON framework |
| **Set** | ranker-gate |
| **Commit** | `e5f67150a0a2cd6fafcee483add00c1bb0fae306` |

## Why this repo

- **Multi-module with clear boundaries**: Serialization engine, LINQ-to-JSON
  (JToken tree), reader/writer streaming API, schema validation, converters,
  BSON support — each a distinct subsystem. Working on serialization attributes
  requires navigating between attribute definitions, converter resolution,
  and the serialization pipeline.
- **Well-structured**: Source under `Src/Newtonsoft.Json/` with clear namespace
  split. `Linq/` for JToken, `Serialization/` for the serialization engine,
  `Converters/` for type converters, `Schema/` for JSON schema.
- **Rich history**: 3K+ commits, 15+ years of development. The most widely
  used .NET library — rich variety of bug fixes, feature additions, and
  performance optimizations in the PR history.
- **Permissive**: MIT license.

## Structure overview

```
Src/Newtonsoft.Json/
├── JsonConvert.cs               # Static entry point
├── JsonSerializer.cs            # Serialization engine
├── JsonReader.cs                # Base streaming reader
├── JsonWriter.cs                # Base streaming writer
├── JsonTextReader.cs            # Text JSON reader
├── JsonTextWriter.cs            # Text JSON writer
├── Linq/                        # LINQ-to-JSON
│   ├── JToken.cs                # Base token type
│   ├── JObject.cs               # JSON object
│   ├── JArray.cs                # JSON array
│   └── JValue.cs                # JSON primitive
├── Serialization/               # Serialization pipeline
│   ├── JsonSerializerInternalReader.cs
│   ├── JsonSerializerInternalWriter.cs
│   ├── JsonContract.cs          # Type contract system
│   ├── DefaultContractResolver.cs
│   └── JsonProperty.cs
├── Converters/                  # Built-in type converters
├── Bson/                        # BSON reader/writer
└── Utilities/                   # Internal utilities
```

## Scale indicators

- ~150 C# source files
- ~60K lines of code
- 2-3 levels of namespace nesting
- Clear subsystem boundaries (serialization, LINQ, streaming)

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `JsonConvert.PopulateObject` throwing for `Required.Always` properties absent from the JSON when target already has values set

When calling `JsonConvert.PopulateObject` with a partial JSON update
(not all properties present), `EndProcessProperty` in
`JsonSerializerInternalReader` throws a `JsonSerializationException`
for any property marked `Required.Always` or `Required.AllowNull` that
is absent from the JSON — even when that property already has a valid
non-null value on the existing target object. The check in
`EndProcessProperty` compares only `PropertyPresence.None` without
reading the current value from the target. Fix `EndProcessProperty` to
skip the required-missing exception when the target object's property
already holds a non-null value (i.e., `property.Readable` is true and
`property.ValueProvider.GetValue(newObject)` returns non-null).

### N2: Add `Required.AllowDefault` for value types

The `[JsonProperty(Required = Required.Always)]` attribute requires a
non-null value, which for value types (int, bool) means the property
must be present in the JSON even when the value is the type's default
(0, false). Add `Required.AllowDefault` that only checks for presence
in the JSON without validating against type defaults.

### N3: Add `MaxDepth` property to `JsonLoadSettings`

`JsonLoadSettings` (used by `JToken.Parse` and `JToken.Load`) exposes
`CommentHandling`, `LineInfoHandling`, and `DuplicatePropertyNameHandling`
but provides no way to configure the maximum nesting depth. To restrict
depth when loading via the LINQ-to-JSON API, callers must create a
`JsonTextReader` manually and set `MaxDepth` on it. Add a nullable
`MaxDepth` property to `JsonLoadSettings` and propagate it to the
`JsonTextReader` created inside `JToken.Load(JsonReader, JsonLoadSettings)`
so callers can control the depth limit through the settings object. When
`MaxDepth` is null, the reader's own default should be preserved.

### N4: Add `AllowTrailingCommas` option to `JsonLoadSettings` to optionally reject trailing commas

`JsonTextReader` currently accepts trailing commas in arrays and objects
(e.g., `[1, 2, 3,]`) — `ParseObject` and `ParseValue` treat a comma
immediately before a closing `}` or `]` as valid JSON. This permissive
behavior is intentional for lenient parsing, but there is no way to
opt into stricter RFC 8259 compliance via the `JToken.Parse` /
`JToken.Load` API. Add a `bool AllowTrailingCommas` property to
`JsonLoadSettings` that controls this behavior: `true` (the default)
preserves the existing permissive behavior for backward compatibility,
while `false` makes the reader strict. Add a matching
`AllowTrailingCommas` property to `JsonTextReader` and propagate the
setting from `JsonLoadSettings` to the reader inside `JToken.Load`.
When `AllowTrailingCommas` is `false`, the reader's `ParseObject` and
`ParseValue` methods should throw a `JsonReaderException` upon
encountering a trailing comma before the closing `}` or `]`.

### N5: Fix `DefaultContractResolver` not sharing a static contract cache across instances

`CamelCasePropertyNamesContractResolver` uses a static
`Dictionary<StructMultiKey<Type, Type>, JsonContract>` keyed by
`(objectType, resolverType)` (guarded by `TypeContractCacheLock`) so
that multiple instances share their resolved contracts. By contrast,
`DefaultContractResolver` stores contracts only in a per-instance
`ThreadSafeStore<Type, JsonContract>`, so every new instance recreates
contracts from scratch. Add a static contract cache to
`DefaultContractResolver.ResolveContract` using the same
`StructMultiKey<Type, Type>` pattern already present in
`CamelCasePropertyNamesContractResolver`, and update `ResolveContract`
to check and populate the static cache before falling back to the
instance store.

### N6: Fix `JValue.Equals` not comparing `DateTimeOffset` and `DateTime` correctly

`JValue.Equals` returns `false` when comparing a `JValue` holding a
`DateTime` with one holding a `DateTimeOffset` that represents the
same instant. The `Compare` method in `JValue` does not attempt to
normalize the two date types before comparison. Fix `JValue.Compare`
to convert to a common representation when both values are date-like.

### N7: Fix `JsonTextWriter.QuoteChar` setter not guarding against mid-stream changes

Changing `JsonTextWriter.QuoteChar` after writing has begun (i.e., after
at least one token has been written) produces inconsistent output: the
writer silently switches the quote character mid-stream, yielding output
such as `{"first":"value",'second':'value'}` which is syntactically
invalid JSON. The `set_QuoteChar` property validates only that the
character is `"` or `'` but does not check whether the writer is past
`WriteState.Start`. Fix `JsonTextWriter.set_QuoteChar` to throw an
`InvalidOperationException` when `WriteState != WriteState.Start`,
with a message indicating that the quote character cannot be changed
after writing has started.

### N8: Fix `BsonReader.ReadBinary` not validating outer byte count for `BsonBinaryType.BinaryOld`

For `BsonBinaryType.BinaryOld` fields, the BSON spec stores an outer
4-byte length followed by a second inner 4-byte length; the outer
length therefore must be at least 4. `BsonReader.ReadBinary` reads
the outer `dataLength` via `ReadInt32` and, for `BinaryOld`, always
reads a second `ReadInt32` for the inner length. When a malformed
BSON document encodes a `BinaryOld` field with `dataLength < 4`,
the second `ReadInt32` over-reads into the following BSON data,
silently corrupting the read position. Add a guard in
`BsonReader.ReadBinary` that throws a `JsonReaderException` when a
`BinaryOld` field's outer byte count is less than 4.

### N9: Fix `JsonConvert.PopulateObject` not applying `$id` reference tracking only when reference preservation is enabled

In `JsonSerializerInternalReader.Populate`, the `$id` metadata
property is read and consumed (lines that check
`JsonTypeReflector.IdPropertyName`) regardless of whether
`PreserveReferencesHandling` is enabled on the serializer. When
reference preservation is `None`, the id is extracted and silently
discarded, which advances the reader past the first real property of
the object, causing that property to be skipped. Fix `Populate` to
only parse and consume the `$id` token when
`Serializer.PreserveReferencesHandling != PreserveReferencesHandling.None`
so that objects whose first property name happens to be `$id` are
deserialized correctly when reference tracking is off.

### N10: Add `StringComparison` property to `JsonSelectSettings` for case-insensitive property matching in `SelectTokens`

`JToken.SelectTokens` (and `SelectToken`) always performs case-sensitive
property name matching because `FieldFilter.ExecuteFilter` and
`FieldMultipleFilter.ExecuteFilter` use the `JObject` indexer (`o[Name]`)
and `JObject.GetValue(name)` without a `StringComparison` argument.
`JsonSelectSettings` has no property to control case sensitivity.
Add a `StringComparison` property to `JsonSelectSettings` (defaulting
to `StringComparison.Ordinal` for backward compatibility). Pass the
settings object into `FieldFilter.ExecuteFilter` and
`FieldMultipleFilter.ExecuteFilter` and replace the `o[Name]` call with
`o.GetValue(Name, settings.StringComparison)` so callers can opt in to
case-insensitive path evaluation.

## Medium

### M1: Implement async serialization and deserialization

Add `JsonConvert.SerializeObjectAsync` and `DeserializeObjectAsync`
that work with `Stream` and use async I/O. The async path should
not buffer the entire JSON in memory. Support `CancellationToken`
for cancellation. The async reader/writer should yield periodically
to avoid blocking the thread pool on large documents. Also update
`Doc/SerializingJSON.aml` to add an "Async Serialization" section
documenting the new async API with usage examples, and update
`Src/Newtonsoft.Json/Newtonsoft.Json.csproj` to conditionally
reference `System.IO.Pipelines` for `net6.0` and above targets
that support async streaming.

### M2: Add support for `System.Text.Json` JsonDocument interop

Implement converters that bridge between Newtonsoft.Json's `JToken`
tree and System.Text.Json's `JsonDocument`/`JsonElement`. Add
`JToken.FromJsonElement(JsonElement)` and `JToken.ToJsonElement()`.
Support round-tripping without data loss. Handle the semantic
differences between the two JSON models (Newtonsoft allows duplicate
keys, System.Text.Json does not).

### M3: Implement JSON Patch (RFC 6902) support

Add a `JsonPatch` class that represents a sequence of JSON Patch
operations (add, remove, replace, move, copy, test). Implement
`JToken.ApplyPatch(JsonPatch)` and `JsonPatch.Create(JToken original, JToken modified)` that generates the minimal patch between two
documents. Support atomic patch application (all-or-nothing).

### M4: Add polymorphic deserialization using a configurable discriminator property

Currently `TypeNameHandling` embeds the full .NET type name in JSON,
which is a security risk and couples JSON to CLR types. Add a
`DiscriminatorAttribute` and a `DiscriminatorConverter<TBase>` that
maps a JSON property (e.g., `"type": "circle"`) to concrete types
without exposing CLR names. Support registration via attribute and
via serializer settings. Wire the discriminator into the
`JsonSerializerInternalReader` type resolution pipeline alongside
the existing `$type` mechanism.

### M5: Implement JSON Merge Patch (RFC 7396) support on `JToken`

Add `JToken.MergePatch(JToken patch)` that applies an RFC 7396 merge
patch: null values delete keys, objects merge recursively, and
non-object values replace. Also add
`JToken.CreateMergePatch(JToken original, JToken modified)` to
compute the minimal merge patch between two documents. Unlike the
existing `Merge` method, this must follow RFC 7396 semantics exactly,
including null-means-delete behavior.

### M6: Add JSONPath script-function support in `SelectTokens` filter expressions

`JToken.SelectTokens` supports filter expressions with comparison and
existence operators (e.g., `$.store.book[?(@.price < 10)]`, `[?(@.isbn)]`).
However, it does not support script functions such as `length()`, `size()`,
`keys()`, or `type()` that appear in popular JSONPath proposals (Goessner
extension, JSONPath spec drafts). Implement a `ScriptFunction` abstraction
in the `Linq/JsonPath/` layer with built-in functions: `length()` (string
and array length), `size()` (alias for `length`), `type()` (returns the
`JTokenType` name), and `keys()` (returns property names of a JObject).
Extend `JPath.ParseExpression` and `BooleanQueryExpression.IsMatch` to
recognize and evaluate function calls within filter expression paths
(e.g., `[?( length(@.title) > 5 )]`, `[?(@.type() == 'array')]`). Wire
the function registry into `JsonSelectSettings` so callers can add custom
script functions.

### M7: Add `IAsyncEnumerable<T>` serialization and deserialization support

`JsonSerializer` does not handle `IAsyncEnumerable<T>` — it throws
at runtime because no `JsonContract` matches the interface. Add an
`AsyncEnumerableContract` and corresponding read/write logic in
`JsonSerializerInternalReader` and `JsonSerializerInternalWriter`
that streams elements from an async sequence to a JSON array and
deserializes a JSON array by yielding elements asynchronously.

### M8: Implement configurable `NamingStrategy` composition

The current `NamingStrategy` on `DefaultContractResolver` applies a
single naming transformation. Add `CompositeNamingStrategy` that
chains multiple strategies (e.g., snake_case first, then prefix with
underscore for private fields). Support per-type overrides via
`[JsonObject(NamingStrategy = ...)]` that compose with the resolver-
level strategy. Update `JsonProperty.PropertyName` resolution in
`DefaultContractResolver.SetPropertySettingsFromAttributes` to walk
the chain.

### M9: Add `JToken.ToObject<T>` support for dictionary types with complex keys

`JToken.ToObject<T>()` fails for `Dictionary<TKey, TValue>` when
`TKey` is not `string`, because the LINQ-to-JSON conversion path
assumes dictionary keys are always strings. Add conversion logic
in `JToken.ToObject` that detects non-string key dictionaries,
deserializes each `JProperty` name through the key type's converter,
and constructs the dictionary. Support `enum`, `int`, `Guid`, and
custom `TypeConverter`-backed key types.

### M10: Add support for deserializing C# record types with primary constructors

`JsonSerializerInternalReader` cannot correctly deserialize C# record
types that use primary constructors because the constructor parameter
names do not match the property names after `NamingStrategy` is
applied. Add logic in `DefaultContractResolver.CreateConstructorParameters`
to match constructor parameters to JSON properties using the resolved
property name rather than the CLR parameter name. Support `init`-only
setters and `with`-expression cloning for record population.

## Wide

### W1: Add JSON Schema generation from .NET types

Implement `JsonSchema.Generate<T>()` that produces a JSON Schema
(draft-2020-12) from a .NET type using its Newtonsoft.Json serialization
configuration. Respect `[JsonProperty]` attributes, `[JsonConverter]`
annotations, `Required` settings, `NullValueHandling`, and custom
contract resolvers. Support recursive types, generic types, inheritance
(`$ref`, `allOf`), and enum schemas. Add schema validation for
`JToken` instances. Also update `CONTRIBUTING.md` to add a
"Schema Module" section describing the architecture and
contribution guidelines for schema-related changes, and add
documentation entries in `Doc/JsonSchema.aml` covering the schema
generation API with worked examples for common type hierarchies.

### W2: Implement source generator for compile-time serialization

Add a C# source generator that emits serialization/deserialization
code at compile time, eliminating runtime reflection cost. The
generator should analyze `[JsonObject]` and `[JsonProperty]`
annotations, handle contract customization, support converters, and
produce code equivalent to the reflection-based serializer. Add a
`[JsonSourceGeneration]` attribute to opt-in per type. Maintain
behavioral parity with the runtime serializer for all supported
features.

### W3: Implement a JSON diff engine producing RFC 6902 patches

Add `JsonDiff.Compare(JToken left, JToken right)` that produces a
minimal RFC 6902 JSON Patch representing the structural difference
between two documents. The diff engine should handle object property
additions, removals, and modifications; array element insertions,
deletions, and moves (using LCS for optimal edit distance); and
nested changes with correct JSON Pointer paths. Integrate with the
existing `JToken` tree and provide `JToken.Diff(JToken other)` as a
convenience method. Support configurable diff strategies (structural
vs. semantic equality).

### W4: Add comprehensive date and time handling overhaul

Centralize all date/time conversion logic currently scattered across
`DateTimeUtils`, `IsoDateTimeConverter`, `JavaScriptDateTimeConverter`,
and inline parsing in `JsonReader`. Introduce a `DateTimeHandling`
settings object on `JsonSerializerSettings` that controls format
selection (ISO 8601, Unix epoch seconds, Unix epoch milliseconds,
Microsoft `/Date()/`), time zone normalization (UTC, local, offset-
preserving), and calendar system. Update `JsonTextReader.ParseDate`,
`JsonTextWriter.WriteValue(DateTime)`, and all date converters in
`Converters/` to delegate to the centralized handler. Ensure
round-trip fidelity for `DateTime`, `DateTimeOffset`, and `TimeSpan`.

### W5: Implement buffered large-document streaming with `JsonReader.ReadAhead`

Add a `ReadAheadJsonReader` wrapper that reads large JSON documents
in configurable chunks (default 64 KB) without loading the entire
document into memory. Integrate with `JsonSerializer.Deserialize` so
that collection deserialization can process elements incrementally.
Add `JsonSerializer.DeserializeStream<T>(Stream, Action<T> onItem)`
for processing large arrays element by element. Wire into
`JsonSerializerInternalReader.CreateList` to support incremental
collection building. Support cancellation and progress reporting.

### W6: Add structured diagnostic event tracing for the serialization pipeline

The existing `ITraceWriter` in `Serialization/ITraceWriter.cs` and its
built-in implementations (`MemoryTraceWriter`, `DiagnosticsTraceWriter`)
emit flat string trace messages through `JsonSerializerInternalBase`.
Add a structured event layer on top: introduce `IStructuredTraceWriter`
with a `Trace(SerializationTraceEvent)` method where
`SerializationTraceEvent` carries typed fields (event kind, source
type, target contract, converter used, property name, skip reason,
JSON path). Hook structured event emission into
`JsonSerializerInternalReader` (contract resolution, converter
selection, property population, `$ref` resolution) and
`JsonSerializerInternalWriter` (serialization dispatch, reference
loop detection, converter invocation). Add
`StructuredMemoryTraceWriter` that collects events for programmatic
querying. Wire `IStructuredTraceWriter` into `JsonSerializerSettings`
alongside the existing `TraceWriter` property. Ensure zero overhead
when structured tracing is not configured.

### W7: Implement AOT and trimmer compatibility for the serialization engine

The reflection-heavy `DefaultContractResolver` and converter
discovery pipeline cause trimming warnings and failures in
NativeAOT scenarios. Add `[DynamicallyAccessedMembers]` annotations
throughout `Serialization/`, introduce `JsonTypeInfo<T>` metadata
containers that can be pre-generated, and create a
`TrimSafeContractResolver` that uses only statically-known type
information. Update `JsonSerializerInternalReader` and
`JsonSerializerInternalWriter` to optionally use the pre-built
contracts. Add linker XML for types that must survive trimming.
Validate with `ILLink` analyzer and `PublishTrimmed`.

### W8: Add a contract resolver composition and override system

Add `CompositeContractResolver` that chains multiple
`IContractResolver` instances with priority-based override semantics.
Each resolver in the chain can modify, wrap, or replace the contract
produced by lower-priority resolvers. Add `ContractResolverBuilder`
for fluent configuration: `.AddResolver<T>()`, `.ForType<T>(c => ...)`
per-type overrides, `.WithFallback(resolver)`. Update
`JsonSerializer.ContractResolver` to accept `IContractResolver` or
`CompositeContractResolver`. Ensure the caching layer in
`DefaultContractResolver` is aware of the composition chain so that
per-resolver caches do not conflict.

### W9: Implement a JSON Schema validation engine on `JToken`

Add `JsonSchemaValidator` that validates `JToken` instances against
a JSON Schema (draft-2020-12). Support `type`, `properties`,
`required`, `additionalProperties`, `items`, `prefixItems`, `anyOf`,
`oneOf`, `allOf`, `not`, `if/then/else`, `$ref` with `$defs`,
`pattern`, `format`, `minimum/maximum`, `minLength/maxLength`,
`minItems/maxItems`, `uniqueItems`, and `enum`/`const`. Return
structured `ValidationResult` with JSON Pointer paths to failing
nodes and human-readable error messages. Support custom format
validators via `FormatHandlerRegistry`. Wire into `JToken.IsValid`
and `JToken.Validate`.

### W10: Add multi-target `Utf8JsonWriter`-based output for `JsonSerializer`

Currently `JsonSerializer` writes through `JsonTextWriter` which
internally builds a `string` via `StringWriter`. Add an alternative
output path using `System.Buffers.IBufferWriter<byte>` that writes
UTF-8 JSON bytes directly without intermediate string allocation.
Introduce `Utf8JsonSerializerWriter` implementing `JsonWriter` that
wraps `Utf8JsonWriter` from System.Text.Json. Update
`JsonSerializerInternalWriter.Serialize` to dispatch to the
appropriate writer based on output target. Support `Stream`,
`PipeWriter`, and `Memory<byte>` targets. Ensure feature parity
with `JsonTextWriter` including formatting, string escaping, and
comment writing.

### N11: Fix `Build/version.json` and `.csproj` version drift

The version metadata is split between `Build/version.json`
(which declares `Major: 13, Release: 5, Prerelease: beta1`) and
`Src/Newtonsoft.Json/Newtonsoft.Json.csproj` (which declares
`VersionPrefix` `11.0.1` with `VersionSuffix` `beta2`). These two
sources of truth disagree on both the major version and the
pre-release label, causing the build script `Build/build.ps1` to
stamp NuGet packages with a version derived from `version.json`
while the assembly version comes from the `.csproj`. Fix the drift
by updating `Build/version.json` to match the `.csproj`
(`Major: 11, Release: 0, Prerelease: beta2`) and add a
`<Target Name="ValidateVersionConsistency">` in
`Src/Directory.Build.props` that reads `version.json` at build time
and emits a build error if the major or pre-release fields
diverge from the `.csproj` properties.

### M11: Modernise the `Doc/` SHFB documentation project and add missing topics

The `Doc/` directory uses Sandcastle Help File Builder (`.shfbproj`)
with `.aml` topic files, but several topics are outdated or missing.
Update `Doc/doc.shfbproj` to target the latest SHFB version and
remove the build warning suppressions that hide broken cross-
references. Add three new `.aml` topic files:
`Doc/AsyncSerialization.aml` covering the async serialization API,
`Doc/DiscriminatorDeserialization.aml` covering the discriminator-
based polymorphic deserialization pattern, and
`Doc/JsonMergePatch.aml` covering RFC 7396 merge-patch usage.
Update `Doc/doc.content` to include the new topics in the table of
contents under a "Modern Patterns" heading. Update `CONTRIBUTING.md`
to add a "Documentation" section that explains the `.aml` format
and how to preview the SHFB output locally. Update `README.md` to
link to the generated documentation site rather than embedding
API examples inline.

### W11: Overhaul build infrastructure, CI pipeline, and project configuration

Modernise the repository build infrastructure across multiple
non-code files. In `azure-pipelines.yml`, replace the single-job
pipeline with a multi-stage layout: a `Build` stage that builds all
TFMs, a `Test` stage with a matrix across `windows-2022`,
`ubuntu-latest`, and `macos-latest`, and a `Pack` stage that
creates NuGet packages and publishes them as pipeline artifacts.
Add a `PublishCodeCoverageResults` step using Cobertura format.
In `Src/Newtonsoft.Json/Newtonsoft.Json.csproj`, remove the
legacy `net20`, `net35`, and `net40` TFMs that are no longer
supported by the .NET SDK, add `net9.0` to the
`<TargetFrameworks>` list, and replace the hard-coded
`<AssemblyVersion>` and `<FileVersion>` with properties derived
from `Build/version.json` via an MSBuild `ReadLinesFromFile` task
so that version metadata has a single source of truth. In
`Src/Directory.Build.props`, remove the individual per-package
version properties and migrate to Central Package Management by
adding `<ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>`
and a `<PackageVersion>` item group. Update `Src/NuGet.Config`
to add a `<trustedSigners>` section matching the signature
fingerprints from nuget.org. Update `Src/global.json` to pin the
SDK to `10.0.100`. In `Build/version.json`, add a `Build` field
so that CI can inject a build number without modifying the file.
Add a `Doc/build-infrastructure.md` guide documenting the purpose
of every build-related file and the CI pipeline contract.
