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
