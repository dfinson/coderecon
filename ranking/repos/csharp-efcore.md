# dotnet/efcore

| Field | Value |
|-------|-------|
| **URL** | https://github.com/dotnet/efcore |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Large (multi-team project) |
| **Category** | Object-relational mapper (ORM) |

## Why this repo

- **No single developer knows it all**: Model building (conventions, annotations,
  fluent API), change tracking, query pipeline (LINQ translation → SQL
  generation), migrations, database providers (SQL Server, SQLite, PostgreSQL,
  Cosmos DB), scaffolding/reverse engineering — each a deep subsystem with
  substantial internal complexity.
- **Well-structured**: Clear project split — `EFCore/` (core abstractions),
  `EFCore.Relational/` (relational database layer), `EFCore.SqlServer/`,
  `EFCore.Sqlite/`, `EFCore.Cosmos/`, `EFCore.Design/` (migrations tooling).
  Query pipeline has clear phases (model, expression tree, SQL generation).
- **Rich history**: 15K+ commits, Microsoft-maintained with regular releases.
  Dense PR history covering performance work, query translation edge cases,
  provider-specific behavior, and API evolution.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── EFCore/                          # Core abstractions
│   ├── DbContext.cs                 # Unit of work
│   ├── ChangeTracking/              # Entity state tracking
│   ├── Metadata/                    # Model metadata (entity types, properties)
│   │   ├── Builders/                # Fluent API model builders
│   │   └── Conventions/             # Convention-based configuration
│   ├── Query/                       # Query pipeline core
│   │   ├── Internal/                # Expression visitors, compilation
│   │   └── ResultOperators/         # LINQ operator translation
│   ├── Storage/                     # Value conversion, type mapping
│   └── Infrastructure/              # Service collection, DI
├── EFCore.Relational/               # Relational database layer
│   ├── Query/                       # SQL generation
│   │   ├── SqlExpressions/          # SQL expression tree
│   │   └── Internal/                # Query SQL generator
│   ├── Migrations/                  # Schema migration engine
│   ├── Storage/                     # Relational type mapping
│   └── Update/                      # Command batching
├── EFCore.SqlServer/                # SQL Server provider
├── EFCore.Sqlite/                   # SQLite provider
├── EFCore.Cosmos/                   # Cosmos DB provider
├── EFCore.Design/                   # Migrations tooling, scaffolding
└── EFCore.Proxies/                  # Lazy loading proxies
```

## Scale indicators

- ~3,000 C# source files
- ~500K+ lines of code
- Deep module hierarchies (5+ levels)
- Cross-cutting query pipeline, change tracking, and provider abstractions
