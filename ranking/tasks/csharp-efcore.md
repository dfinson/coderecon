# Tasks — dotnet/efcore

10 tasks (3 narrow, 4 medium, 3 wide) for the C# ORM.

## Narrow

### N1: Fix `Include()` generating duplicate JOINs with `AsSplitQuery()`

When using `Include()` with `AsSplitQuery()`, including the same
navigation property through two different paths generates duplicate
SQL queries for the included entity. The query splitting logic does
not deduplicate navigations that resolve to the same table. Fix the
split query generator to detect and merge duplicate includes.

### N2: Add `HasComment()` support for columns in migrations

The `HasComment("...")` fluent API sets a comment on a table, but
there is no way to set a comment on an individual column. Add
`property.HasComment("...")` that generates `COMMENT ON COLUMN` in
migrations for databases that support it (PostgreSQL, MySQL). For
SQL Server, store the comment as an extended property.

### N3: Fix `ExecuteUpdate` not translating `DateOnly` arithmetic

When using `ExecuteUpdate` with date arithmetic on `DateOnly`
properties (e.g., `.SetProperty(e => e.ExpiryDate, e => e.ExpiryDate.AddDays(30))`), the LINQ translator throws
"could not be translated" because `DateOnly.AddDays` is not
registered as a translatable method. Add SQL translation for
`DateOnly.AddDays`, `AddMonths`, and `AddYears` across all providers.

## Medium

### M1: Implement soft delete global query filter with cascade

Add a built-in soft delete pattern via `modelBuilder.Entity<T>().HasSoftDelete()`. This should: add a `DeletedAt` shadow property,
configure a global query filter excluding deleted entities, override
`SaveChanges` to set `DeletedAt` instead of deleting, and handle
cascade soft delete for dependent entities. Add `IgnoreSoftDelete()`
for queries that need to see deleted entities.

### M2: Add query result caching with invalidation

Implement a query cache that stores materialized query results and
invalidates them when the underlying tables change. Add
`.TagWith("cache", duration)` for explicit caching and an
auto-invalidation system that tracks which tables each cached query
reads from. Support cache backends: in-memory (default) and
distributed (via `IDistributedCache`). Add cache hit/miss metrics.

### M3: Implement temporal table queries

Add first-class support for SQL Server temporal tables (system-versioned
tables). Implement `.TemporalAsOf(dateTime)`, `.TemporalBetween()`,
`.TemporalContainedIn()`, and `.TemporalAll()` query operators. Map
temporal period columns (`ValidFrom`, `ValidTo`) as shadow properties.
Support temporal JOINs where related entities are queried at the
same point in time.

### M4: Add bulk operations support

Implement `BulkInsert()`, `BulkUpdate()`, and `BulkDelete()` methods
that use database-specific bulk loading mechanisms (SQL Server
`BULK INSERT`, PostgreSQL `COPY`, MySQL `LOAD DATA`). Bypass the
change tracker for performance. Support configurable batch sizes and
progress callbacks. Maintain foreign key and index constraint
validation.

## Wide

### W1: Implement database-first code generation improvements

Overhaul the scaffolding/reverse engineering system. Add: preserved
custom code in regenerated files (partial classes), mapping comments
from database metadata to XML doc comments, stored procedure and
function scaffolding, view scaffolding with auto-detected key
properties, and incremental scaffolding that updates only changed
entities. Add T4 template support for custom code generation.

### W2: Add multi-tenancy support at the EF Core level

Implement native multi-tenancy patterns: schema-per-tenant
(PostgreSQL `SET search_path`), database-per-tenant (connection
string routing), and discriminator-per-tenant (global query filters).
Add `ITenantProvider` interface for tenant resolution. Support
tenant-aware migrations that apply schema changes across all tenant
databases. Add admin queries that span tenants.

### W3: Implement real-time change notifications

Add database change notification support that pushes entity changes
to subscribers in real-time. Use database-specific change tracking
mechanisms (SQL Server `SqlDependency`, PostgreSQL `LISTEN/NOTIFY`,
SQLite polling). Surface changes through an `IObservable<EntityChange<T>>`
API. Support filtering by entity type and property. Integrate with
SignalR for browser push. Handle reconnection and missed event
recovery.
