# google/gson

| Field | Value |
|-------|-------|
| **URL** | https://github.com/google/gson |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Small (focused library) |
| **Category** | JSON serialization library |

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

8 tasks (3 narrow, 3 medium, 2 wide) for the Java JSON library.

## Narrow

### N1: Fix `@SerializedName` ignored on record components

Java records with `@SerializedName` on constructor parameters do not
use the annotated name during serialization. Gson's reflection logic
finds the annotation on the canonical constructor parameter but not on
the generated field. Fix the `ReflectiveTypeAdapterFactory` to check
record component annotations for `@SerializedName`.

### N2: Add `@JsonAdapter` support for enum constants

The `@JsonAdapter` annotation works on classes and fields but not on
individual enum constants. A common need is to serialize specific enum
values differently. Add support for `@JsonAdapter` on enum constants
so each constant can specify its own serialization/deserialization
logic.

### N3: Fix `JsonReader` incorrect line number after `skipValue()`

After calling `skipValue()` to skip a large nested JSON object,
`JsonReader.getPath()` reports the wrong line number in subsequent
error messages. The line counter does not track newlines within the
skipped content. Fix `skipValue()` to correctly count newlines even
when skipping.

## Medium

### M1: Implement JSON streaming with back-pressure

Add a `JsonStreamer` class that reads from an `InputStream` and emits
parsed JSON tokens incrementally without buffering the entire document.
Support configurable buffer sizes. When the consumer pauses, the reader
should stop consuming from the stream (back-pressure). Support streaming
serialization as well — writing large collections element by element
without holding the full list in memory.

### M2: Add null-safety annotations throughout the API

Annotate all public API methods with `@Nullable` and `@NonNull`
(from `org.jspecify.annotations`). Add annotations to `Gson`,
`JsonElement`, `JsonObject`, `JsonArray`, `TypeAdapter`, `TypeToken`,
and all public interfaces. This requires auditing each method's actual
null behavior and fixing any methods whose behavior contradicts the
annotation.

### M3: Implement custom `TypeAdapter` composition

Add a `TypeAdapters.compose()` method that chains multiple TypeAdapters
together: the first adapter serializes/deserializes, then the result
is passed to the next adapter. This enables reusable transformations
(e.g., trim strings, clamp numbers) that compose with any base type
adapter without subclassing.

## Wide

### W1: Add JSON Schema validation during deserialization

Implement JSON Schema (draft-2020-12) validation that can be applied
during deserialization. Add a `@JsonSchema` annotation that references
a schema resource, and a `GsonBuilder.setSchemaValidation()` option.
Schema violations should be collected as a list (not fail-fast) and
reported through a new `SchemaViolationException`. Support `$ref`,
`oneOf`, `allOf`, `anyOf`, and format validation.

### W2: Implement Gson 3.0 API with sealed interfaces and pattern matching

Design and implement a modernized Gson API that takes advantage of
Java 17+ features: sealed interfaces for `JsonElement` (enabling
pattern matching with `switch`), records for configuration, a builder
API using method chaining with generics, and `Stream<JsonElement>`
support for lazy iteration over JSON arrays. Maintain backward
compatibility through a compatibility layer.


---

## Solve Prompt

The following prompt is sent to the agent for each task in this repo.
`{task_id}` and `{task_text}` are filled per task.

```
You are working on the repository google/gson, cloned at ranking/clones/gson/.

The repository is a Java project. Key source locations:

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

Your task ({task_id}):

{task_text}

Solve this task. Read the code you need, make your edits, and verify
they work (run: ./gradlew test or mvn test). When done, say "DONE".
Do not explain your changes — just make them.
```

### Task index

  - N1: Fix `@SerializedName` ignored on record components
  - N2: Add `@JsonAdapter` support for enum constants
  - N3: Fix `JsonReader` incorrect line number after `skipValue()`
  - M1: Implement JSON streaming with back-pressure
  - M2: Add null-safety annotations throughout the API
  - M3: Implement custom `TypeAdapter` composition
  - W1: Add JSON Schema validation during deserialization
  - W2: Implement Gson 3.0 API with sealed interfaces and pattern matching


---

## Solve Prompt

The following prompt is sent to the agent for each task in this repo.
`{task_id}` and `{task_text}` are filled per task.

```
You are working on the repository google/gson, cloned at ranking/clones/gson/.

The repository is a Java project. Key source locations:

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

Your task ({task_id}):

{task_text}

Solve this task. Read the code you need, make your edits, and verify
they work (run: ./gradlew test or mvn test). When done, say "DONE".
Do not explain your changes — just make them.
```

### Task index

  - N1: Fix `@SerializedName` ignored on record components
  - N2: Add `@JsonAdapter` support for enum constants
  - N3: Fix `JsonReader` incorrect line number after `skipValue()`
  - M1: Implement JSON streaming with back-pressure
  - M2: Add null-safety annotations throughout the API
  - M3: Implement custom `TypeAdapter` composition
  - W1: Add JSON Schema validation during deserialization
  - W2: Implement Gson 3.0 API with sealed interfaces and pattern matching
