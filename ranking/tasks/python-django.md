# Tasks — django/django

10 tasks (3 narrow, 4 medium, 3 wide) for the Python full-stack web framework.

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
