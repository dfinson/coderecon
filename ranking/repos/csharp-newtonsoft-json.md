# JamesNK/Newtonsoft.Json

| Field | Value |
|-------|-------|
| **URL** | https://github.com/JamesNK/Newtonsoft.Json |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Medium (multi-module project) |
| **Category** | JSON framework |

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
