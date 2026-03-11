# django/django

| Field | Value |
|-------|-------|
| **URL** | https://github.com/django/django |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Large (multi-team project) |
| **Category** | Full-stack web framework |
| **Set** | ranker-gate |
| **Commit** | `787166fe27b0e7c7f97505da5766cfa72e76ae25` |

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
clause is silently dropped on SQLite backends. The `SQLCompiler` in
`django/db/models/sql/compiler.py` checks `supports_slicing_ordering_in_compound`
(from `django/db/backends/base/features.py`, default `False`) and strips
ORDER BY from compound subqueries. Fix the compiler's compound query
assembly to preserve the outer ORDER BY clause on unioned querysets.

### N2: Fix `_get_unique_checks()` crashing with `AttributeError` when `exclude` is a list and model has `CompositePrimaryKey`

`Model._get_unique_checks()` in `django/db/models/base.py` starts with:
`if exclude is None: exclude = set()` — but when called with a non-None
list (e.g., `model.validate_unique(exclude=['field_a'])`), `exclude` is
not converted to a set. Later at line ~1521 the code calls
`exclude.isdisjoint(names)` while iterating local fields for a model with
a `CompositePrimaryKey`, raising `AttributeError: 'list' object has no
attribute 'isdisjoint'`. `full_clean()` already converts `exclude` to a
set before delegating to `validate_unique()`, but user code calling
`validate_unique()` or `_get_unique_checks()` directly with a list is a
supported usage (the parameter signature accepts any iterable). Fix
`_get_unique_checks()` to normalize `exclude` to a set unconditionally at
the top of the method, matching `full_clean()`'s existing pattern.

### N3: Fix admin search with `__` lookups on JSONField

The admin `search_fields` option does not work correctly when using
double-underscore lookups on `JSONField` (e.g., `data__name__icontains`).
The admin search constructs the query incorrectly, treating `data__name`
as a related field traversal rather than a JSON path lookup. Fix the
admin search query construction to handle JSON path lookups.

### N4: Fix `F()` expression not working with `JSONField` key transforms in `update()`

Using `Model.objects.update(data__key=F('data__key') + 1)` raises
`FieldDoesNotExist: has no field named 'data__key'` because the ORM
resolves the left-hand side of an `update()` call through `_meta.get_field`,
which does not recognise JSON key-path syntax. Separately, using the
key-transform on the right side only — `update(data=F('data__key') + 1)` —
compiles without error but overwrites the entire JSON column with a scalar
integer, silently discarding the rest of the document. Fix the `update()`
query-building path to detect `JSONField` key-path notation on both sides of
an update assignment, and generate the appropriate JSON replacement SQL
(e.g., `json_set`/`jsonb_set`) so that `update(data__key=F('data__key') + 1)`
performs an in-place atomic increment of the named key for PostgreSQL and
SQLite.

### N5: Fix `GeneratedField` serialized by `dumpdata` causing `loaddata` failures

`GeneratedField` in `django/db/models/fields/generated.py` does not
override `serialize`, so it inherits `serialize=True` from `Field`. This
means `dumpdata` includes generated column values in fixtures. When such
a fixture is loaded via `loaddata`, backends that treat `STORED` generated
columns as read-only (PostgreSQL `GENERATED ALWAYS AS … STORED`, SQLite
virtual columns) reject the `INSERT` with a value for the computed column,
causing `loaddata` to fail. Fix `GeneratedField.__init__()` to set
`serialize=False` as the default — since the database always recomputes
the value on `INSERT`, there is no need to round-trip it through fixtures.
Update the `GeneratedField` reference documentation in
`docs/ref/models/fields.txt` to document the new default behaviour and
note that callers may pass `serialize=True` explicitly if they need the
value present in exported data.

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

### N8: Fix `CompositePrimaryKey` fields not supported in admin `search_fields`

The `CompositePrimaryKey` field in `django/db/models/fields/composite.py`
cannot be used safely in `ModelAdmin.search_fields`. When the admin's
`get_search_results()` method in `django/contrib/admin/options.py`
iterates `search_fields` and encounters `'pk'`, it resolves to the
`CompositePrimaryKey` field, which returns the composite key descriptor.
The `construct_search()` helper then produces `'pk__icontains'`, and
the ORM generates invalid SQL — `"field_a", "field_b" LIKE %term%
ESCAPE '\'` — rather than separate per-component lookups, causing a
database error when the search is executed. Fix `construct_search()` inside
`get_search_results()` to detect `CompositePrimaryKey` and expand
it into separate `icontains` searches on each component field.

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

### M4: Add structured output parsing for `QuerySet.explain()`

The existing `QuerySet.explain()` method in `django/db/models/query.py`
returns a raw text string from the database. Add an `output='structured'`
option that parses the plan into a Python data structure. For PostgreSQL,
parse the JSON output of `EXPLAIN (FORMAT JSON)` into dataclasses with
estimated rows, actual rows, cost, and index usage. For SQLite, parse
`EXPLAIN QUERY PLAN` detail strings. Add this to the SQL compiler in
`django/db/models/sql/compiler.py` and the backend-specific `explain`
implementations in `django/db/backends/*/operations.py`. Include a
middleware that auto-logs parsed explain output for queries exceeding
a configurable duration threshold.

### M5: Add connection pool metrics and management command

Django has connection health checking via `CONN_HEALTH_CHECKS` in
`django/db/backends/base/base.py` and PostgreSQL connection pooling
via `psycopg_pool.ConnectionPool` in `django/db/backends/postgresql/base.py`,
but there is no way to inspect pool state at runtime. Add a
`dbconnstats` management command in `django/core/management/commands/`
that reports per-alias pool statistics (active, idle, stale, total
connections, health check pass/fail counts). Wire it into the
`BaseDatabaseWrapper` class with a `get_connection_stats()` method
that each backend overrides. For PostgreSQL, read from
`ConnectionPool.get_stats()`. For SQLite, report single-connection state.
Also add a system check to the existing `django/core/checks/database.py`
that warns when `CONN_HEALTH_CHECKS` is disabled but `CONN_MAX_AGE` is set.
Add a documentation entry in `docs/ref/django-admin.txt` for the
`dbconnstats` command.

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

### W9: Add database-backed task backend with worker and admin integration

Django already has a `django.tasks` module with `Task`, `TaskResult`,
`BaseTaskBackend`, and `ImmediateBackend`/`DummyBackend` in
`django/tasks/backends/`. Add a new `DatabaseBackend` in
`django/tasks/backends/database.py` that stores enqueued tasks in a
model table, with a `runworker` management command that polls and
executes tasks. Implement retry policies (max retries, backoff),
dead-letter handling for permanently failed tasks, and
task chaining (`Task.then()`). Add an admin `ModelAdmin` in
`django/contrib/admin/` for browsing queued/running/failed tasks with
filters by status, queue name, and priority. Wire task lifecycle into
`django.tasks.signals`. This crosses the ORM (new models and migrations),
management commands, admin interface, and the existing task framework.

### W10: Add comprehensive observability integration

Implement automatic instrumentation for Django applications: distributed
tracing (OpenTelemetry spans for requests, DB queries, template rendering,
cache operations), metrics (request rates, latencies, error rates via
Prometheus), and structured logging with trace correlation. Add an
observability middleware stack, per-view tracing decorators, and a
diagnostics management command. Changes span middleware, database
backends, template engine, cache framework, and settings.

### N11: Fix incomplete JavaScript tooling files in MANIFEST.in

`MANIFEST.in` includes `Gruntfile.js` and `package.json` for the source
distribution but omits `eslint.config.mjs`, `eslint-recommended.js`, and
`globals.js`. Since `eslint.config.mjs` imports both `globals.js` and
`eslint-recommended.js` (lines 1–2), the JavaScript lint configuration
shipped in the sdist is incomplete — running `npm test` (which executes
`eslint .` via the `pretest` script in `package.json`) from an sdist
would fail because ESLint cannot resolve its config imports.

**Gold files:** `MANIFEST.in`, `eslint.config.mjs`

### M11: Reconcile Python version and dependency divergence in doc builds

`.readthedocs.yml` builds documentation with Python `3.12`, while
`.github/workflows/docs.yml` uses Python `3.14`. Both install from
`docs/requirements.txt` (which pins `Sphinx>=4.5.0`). Since RTD sets
`fail_on_warning: true`, Python-version-sensitive Sphinx deprecation
warnings could pass in CI on 3.14 but fail on RTD with 3.12, or vice
versa. The `tox.ini` `[testenv:docs]` adds a third inconsistent
environment — it installs bare `Sphinx` (unpinned) plus `pyenchant`
and `sphinxcontrib-spelling` directly instead of using
`docs/requirements.txt`, so `tox -e docs` may use a different Sphinx
major version than either CI or RTD.

**Gold files:** `.readthedocs.yml`, `.github/workflows/docs.yml`,
`tox.ini`, `docs/requirements.txt`

### W11: Eliminate flake8 version drift across linting environments

`tox.ini` `[testenv:flake8]` requires `flake8 >= 3.7.0` (a minimum
from 2019), while `.pre-commit-config.yaml` pins flake8 at
`rev: 7.3.0`, and `.github/workflows/linters.yml` installs flake8
unpinned via `pip install flake8`. All three read rule configuration
from `.flake8` (which uses `per-file-ignores` with `W601` — a rule
whose semantics changed between flake8 major versions). The wide
version range means local tox runs, pre-commit hooks, and CI may
enforce different rule sets. Similarly, `.editorconfig` and `.flake8`
both encode `max_line_length = 88` / `max-line-length = 88` to match
black's default, but `tox.ini` `[testenv:black]` runs black with no
explicit `--line-length` — if black's default changes, all four files
would need coordinated updates.

**Gold files:** `tox.ini`, `.pre-commit-config.yaml`,
`.github/workflows/linters.yml`, `.flake8`, `.editorconfig`
