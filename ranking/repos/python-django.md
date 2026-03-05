# django/django

| Field | Value |
|-------|-------|
| **URL** | https://github.com/django/django |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Large (multi-team project) |
| **Category** | Full-stack web framework |

## Why this repo

- **No single developer knows it all**: ORM, template engine, admin interface,
  auth system, forms framework, middleware, URL routing, migrations, cache
  framework, internationalization, test runner — each a substantial subsystem
  with deep module hierarchies.
- **Well-structured**: Clear top-level package split (`django/db/`, `django/template/`,
  `django/contrib/admin/`, `django/forms/`, etc.). Two decades of consistent
  architectural patterns.
- **Rich history**: 35K+ commits, 15K+ closed PRs, meticulous code review.
  One of the oldest and most actively maintained Python projects.
- **Permissive**: BSD-3-Clause.

## Structure overview

```
django/
├── db/                  # ORM (models, querysets, backends, migrations)
│   ├── models/          # Model layer, fields, managers
│   ├── backends/        # PostgreSQL, MySQL, SQLite, Oracle
│   └── migrations/      # Schema migration engine
├── template/            # Template engine (lexer, parser, loaders, tags)
├── contrib/
│   ├── admin/           # Admin interface (views, widgets, filters)
│   ├── auth/            # Authentication and authorization
│   └── ...              # contenttypes, sessions, staticfiles, etc.
├── forms/               # Form handling, validation, widgets
├── http/                # Request/response, cookies, multipart
├── middleware/           # Security, CSRF, sessions, cache
├── core/                # Management commands, mail, serializers
├── urls/                # URL routing and resolution
├── views/               # Generic views, decorators
└── utils/               # Functional utilities, encoding, dates
```

## Scale indicators

- ~2,500 Python source files
- ~350K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting concerns span many subsystems

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `QuerySet.union()` ignoring `order_by()` on SQLite

When using `QuerySet.union()` followed by `.order_by()`, the ORDER BY
clause is silently dropped on SQLite backends. The SQL compiler for SQLite
wraps unioned queries in a subquery that loses the outer ordering. Fix the
SQLite compiler to preserve the ORDER BY clause on unioned querysets.

### N2: Add `violation_error_code` to UniqueConstraint

The `CheckConstraint` class supports `violation_error_code` to customize
the validation error code raised when the constraint is violated, but
`UniqueConstraint` does not. Add `violation_error_code` support to
`UniqueConstraint` with the same semantics as `CheckConstraint`.

### N3: Fix admin search with `__` lookups on JSONField

The admin `search_fields` option does not work correctly when using
double-underscore lookups on `JSONField` (e.g., `data__name__icontains`).
The admin search constructs the query incorrectly, treating `data__name`
as a related field traversal rather than a JSON path lookup. Fix the
admin search query construction to handle JSON path lookups.

### N4: Fix `F()` expression not working with `JSONField` key transforms

Using `F('json_field__key') + 1` in an `update()` call raises
`FieldError` because the ORM doesn't recognize JSON key transforms
as valid expressions for arithmetic. Fix the expression compiler to
support arithmetic on JSON key transforms for PostgreSQL and SQLite.

### N5: Add `db_comment` parameter to model fields

Table-level comments are supported via `Meta.db_table_comment`, but
individual field-level database comments are not. Add a `db_comment`
parameter to all field types that generates `COMMENT ON COLUMN` in
migrations for PostgreSQL and MySQL.

### N6: Fix `prefetch_related` generating duplicate queries with `to_attr`

When using `prefetch_related` with `to_attr` and the same queryset is
prefetched multiple times in nested serialization, duplicate SQL queries
are emitted. The prefetch cache lookup does not account for `to_attr`
in the cache key. Fix the prefetch engine to include `to_attr` in
cache identity.

### N7: Fix `ManifestStaticFilesStorage` not handling CSS `url()` with query strings

The manifest storage post-processor hashes filenames referenced in CSS
`url()` declarations, but it breaks when the URL contains query strings
(e.g., `url(font.woff2?v=1)`). The regex parser treats the query string
as part of the filename. Fix the CSS pattern to strip query strings
before resolving the referenced file.

### N8: Add `natural_key` support for `loaddata` with forward references

When loading fixtures via `loaddata` that use `natural_key()`, forward
references between models fail because the referenced object doesn't
exist yet. Add deferred natural key resolution that retries unresolved
references after all objects in the fixture are loaded.

### N9: Fix `assertNumQueries` count wrong with `atomic()` savepoints

When code under test uses `transaction.atomic()` as a savepoint, the
`SAVEPOINT` and `RELEASE SAVEPOINT` commands are counted as queries by
`assertNumQueries`. This makes tests fragile. Add an option to exclude
savepoint management queries from the count.

### N10: Fix `date_hierarchy` in admin crashing with timezone-aware DateTimeField

The admin's `date_hierarchy` feature generates incorrect SQL when used
with a timezone-aware `DateTimeField` and the database is configured for
a non-UTC timezone. The date truncation doesn't apply the timezone
conversion before grouping. Fix the admin date hierarchy to truncate
dates after timezone conversion.

## Medium

### M1: Add async support for database transactions

Django's `transaction.atomic()` context manager and decorator does not
work in async views — it uses synchronous database operations internally.
Implement `transaction.aatomic()` that supports async `with` statements
and async function decoration. This requires changes to the transaction
management layer and the database backend connection handling.

### M2: Implement "soft delete" model mixin

Add a built-in `SoftDeleteModel` mixin that adds a `deleted_at` timestamp
field and overrides `delete()` to set the timestamp instead of removing
the row. The default manager should exclude soft-deleted objects, with an
`all_with_deleted` manager available. Cascade behavior should respect soft
delete. Add admin integration so soft-deleted objects appear in a
separate "Trash" section.

### M3: Add field-level permissions to the admin

The admin currently supports model-level permissions (add, change, delete,
view) but not field-level permissions. Implement per-field read/write
permissions in the admin that can be controlled by the permission system.
This requires changes to `ModelAdmin` field rendering, form generation,
and the admin change view.

### M4: Implement query cost estimation

Add a `QuerySet.explain()` method that returns the database query plan
with cost estimates. Support all major backends (PostgreSQL EXPLAIN ANALYZE,
MySQL EXPLAIN, SQLite EXPLAIN QUERY PLAN). Parse the output into a
structured format with estimated row counts, index usage, and cost
numbers. Add a developer mode that logs slow queries with their
explain output.

### M5: Implement database connection health checking

Add automatic connection health checks. Before reusing a persistent
database connection, verify it's still alive with a lightweight query
(SELECT 1). Support configurable health check interval to avoid checking
on every request. Add connection pool statistics (active, idle, stale)
accessible via a management command.

### M6: Add full-text search for SQLite backend

Django's full-text search (`SearchVector`, `SearchQuery`) only works on
PostgreSQL. Implement SQLite FTS5 support for the same API. Map
`SearchVector` to FTS5 virtual tables, `SearchQuery` to FTS5 MATCH
syntax, and `SearchRank` to FTS5 rank function. Handle index creation
in migrations.

### M7: Implement model field encryption at rest

Add a `EncryptedCharField` and `EncryptedTextField` that transparently
encrypt values before writing to the database and decrypt on read. Use
Fernet symmetric encryption with the `SECRET_KEY`. Support key rotation
(multiple decryption keys, single encryption key). Add a management
command to re-encrypt after key rotation. Handle lookups (exact match via
deterministic encryption mode).

### M8: Add admin bulk action confirmation with preview

The admin's bulk delete action shows a confirmation page, but custom
bulk actions don't. Add a `confirmation_required` option to custom
admin actions that shows a preview of affected objects, allows the user
to deselect individual items, and requires explicit confirmation before
proceeding.

### M9: Implement database query tagging

Add `QuerySet.tag(label)` that annotates the resulting SQL with a
comment (e.g., `/* label: user_list_view */`). This helps identify
application code responsible for slow queries in database logs. Support
automatic tagging with the view name via middleware. Respect database
backend differences in comment syntax.

### M10: Add form wizard with session-based state

Implement a `FormWizard` view that manages multi-step forms with session
storage between steps. Support conditional step ordering (skip step 3 if
step 2 answer is X), per-step validation, back navigation without data
loss, and a summary review step. Include admin integration for wizard
definitions.

## Wide

### W1: Add GraphQL support alongside REST

Implement a `django.contrib.graphql` module that auto-generates a GraphQL
schema from Django models, integrating with the ORM for queries and
mutations, the permission system for authorization, the admin for
introspection, and the test framework for GraphQL endpoint testing.
Support subscriptions via Django Channels.

### W2: Migrate admin interface from jQuery to vanilla JS

Replace all jQuery usage in the admin interface with vanilla JavaScript.
This affects the calendar and date/time widgets, inline formset
management, autocomplete widgets, changelist sorting/filtering,
prepopulated fields, collapsible fieldsets, and the dynamic form
handling. All existing admin customization hooks must continue to work.

### W3: Add multi-tenancy support

Implement schema-based multi-tenancy for PostgreSQL. Add a `Tenant` model,
middleware that resolves the tenant from the request (subdomain, header,
or URL prefix), and a database router that sets the PostgreSQL search_path
per-tenant. Management commands should support tenant-aware migrations.
The admin should be tenant-aware. The test framework should support
per-tenant test isolation.

### W4: Implement real-time model change notifications via Channels

Add `django.contrib.notifications` that broadcasts model changes over
Django Channels WebSockets. Subscribe to model-level or instance-level
changes. Handle serialization of change payloads, permission checks on
subscriptions, and reconnection with missed-event replay. Integrate
with Django's signals for change detection and the admin for live
refresh.

### W5: Add API framework as a contrib package

Implement `django.contrib.api` — a built-in REST API framework. Auto-
generate CRUD endpoints from models, with serialization based on model
fields, authentication via Django auth, permissions via the permission
framework, filtering via querysets, and auto-generated OpenAPI schema.
Support custom endpoints, nested resources, and pagination. This crosses
models, views, URL routing, auth, and serialization.

### W6: Implement progressive migration system

Add support for non-blocking, progressive schema migrations. Split
migrations into fast (DDL-only) and slow (data migration) phases. Data
migrations run in background worker processes with progress tracking,
configurable batch sizes, and checkpoint/resume on failure. Add a
management command for monitoring migration progress. Changes span the
migration framework, management commands, and the database backends.

### W7: Implement content versioning for models

Add a `VersionedModel` mixin that automatically creates a new version
on every save. Store versions in a shadow table with the full field
state, author, timestamp, and change reason. Provide a diff API between
versions, rollback to specific versions, and admin integration showing
version history. Handle foreign key relationships in versioned content.
Changes span the ORM, migrations, admin, and serialization.

### W8: Add built-in feature flags framework

Implement `django.contrib.features` for feature flag management. Support
boolean flags, percentage rollouts, user-targeted flags, and
environment-based flags. Add template tags (`{% feature "flag_name" %}`),
view decorators (`@requires_feature("flag_name")`), and middleware for
feature-based request routing. Include an admin interface for managing
flags and a management command for flag lifecycle. Store flags in
database with cache layer.

### W9: Implement distributed task queue as contrib

Add `django.contrib.tasks` — a lightweight task queue built on Django's
ORM. Define tasks as decorated functions, schedule them immediately or
with delay, group them in chains/chords. Workers consume from the
database queue. Support task priorities, retry policies, dead-letter
queues, and result storage. Include admin views for queue monitoring.
This crosses the ORM, management commands, admin, and signals.

### W10: Add comprehensive observability integration

Implement automatic instrumentation for Django applications: distributed
tracing (OpenTelemetry spans for requests, DB queries, template rendering,
cache operations), metrics (request rates, latencies, error rates via
Prometheus), and structured logging with trace correlation. Add an
observability middleware stack, per-view tracing decorators, and a
diagnostics management command. Changes span middleware, database
backends, template engine, cache framework, and settings.
