# fastapi/fastapi

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fastapi/fastapi |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium (multi-module project) |
| **Category** | Web framework |

## Why this repo

- **Multi-module with clear boundaries**: Routing, dependency injection,
  security (OAuth2, API keys), middleware, background tasks, WebSockets,
  OpenAPI schema generation — each a distinct subsystem requiring navigation
  between them.
- **Well-structured**: All source under `fastapi/` with logical subpackages
  (`security/`, `middleware/`, `openapi/`). Not a monorepo.
- **Rich history**: 10K+ commits, very active PR flow, strong code review
  culture. One of the most popular Python frameworks.
- **Permissive**: MIT license.

## Structure overview

```
fastapi/
├── applications.py      # FastAPI app class
├── routing.py           # APIRouter, route handling
├── dependencies/        # Dependency injection system
├── security/            # OAuth2, HTTP Basic/Bearer, API keys
├── middleware/           # CORS, GZip, HTTPSRedirect
├── openapi/             # OpenAPI schema generation
├── params.py            # Query, Path, Header, Cookie params
├── responses.py         # Response classes
├── encoders.py          # JSON encoding
├── exceptions.py        # HTTP exception handling
├── background.py        # Background tasks
├── websockets.py        # WebSocket support
└── testclient.py        # Test utilities
```

## Scale indicators

- ~80 Python source files
- ~25K lines of code
- Requires navigating between routing, DI, security, and schema subsystems
- Moderate dependency tree (Starlette, Pydantic)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `Depends()` with `yield` not closing on WebSocket disconnect

When a WebSocket connection drops unexpectedly, dependencies that use
`yield` (generator dependencies) do not have their cleanup code executed.
The `finally` block of the dependency generator is never reached because
the WebSocket disconnect exception propagates before the dependency
manager can finalize. Fix the WebSocket route handling to ensure generator
dependencies are always cleaned up on disconnect.

### N2: Add `deprecated` parameter to `APIRouter`

FastAPI supports marking individual routes as deprecated via
`@app.get("/path", deprecated=True)`, but there's no way to mark an
entire router as deprecated. Add a `deprecated` parameter to `APIRouter`
that applies to all routes registered on that router. Routes should
inherit the router's deprecated status unless they explicitly override it.

### N3: Fix OpenAPI schema for `Optional[List[str]]` query parameter

When a query parameter is typed as `Optional[List[str]]`, the generated
OpenAPI schema incorrectly sets `required: true` instead of `required: false`.
The optionality is lost during schema generation. Fix the parameter schema
generation to correctly handle `Optional` wrapping around collection types.

### N4: Fix `Query` parameter with `alias` not reflected in OpenAPI

When a query parameter uses `Query(alias="q")`, the OpenAPI schema shows
the original parameter name instead of the alias. The alias is used for
request parsing but not propagated to the schema generator. Fix the
schema generation to use the alias as the parameter name.

### N5: Fix `BackgroundTasks` not running when response is streamed

When a route returns a `StreamingResponse`, background tasks added via
`BackgroundTasks` never execute. The task runner waits for the response
to complete, but streaming responses close the ASGI connection before
the background task hook fires. Fix the ASGI lifecycle to run background
tasks after streaming completes.

### N6: Add `HEAD` method support in `TestClient`

The `TestClient` does not properly handle HEAD requests — it returns a
body despite the HTTP spec requiring an empty body for HEAD responses.
Fix the test client to strip the response body for HEAD requests while
preserving the Content-Length header.

### N7: Fix `Form` data not validated when mixed with `Body` JSON

When a route accepts both `Form()` and `Body()` parameters, the form
data parameters bypass Pydantic validation. The request body parser
detects JSON content-type and skips form field validation. Fix the
parameter resolver to validate form fields regardless of body type.

### N8: Add `status_code` parameter to dependency injection error responses

When a dependency raises `HTTPException`, the status code is always
what the dependency specifies. Add a way for the route decorator to
specify a default error status code for dependency failures:
`@app.get("/", dependency_error_status=503)`.

### N9: Fix path parameter regex validation not working with `Path(regex=...)`

The `regex` parameter on `Path()` is accepted but never actually applied
to validate the incoming path segment. The regex is included in OpenAPI
but not enforced at runtime. Fix the path parameter resolver to validate
incoming values against the regex.

### N10: Fix duplicate `422` response in OpenAPI when custom error handler is set

When a custom exception handler is registered for `RequestValidationError`,
the OpenAPI schema still includes the default 422 response schema
alongside any custom error schema. Fix the OpenAPI generator to use the
custom handler's response schema when one is registered.

## Medium

### M1: Add rate limiting middleware

Implement a built-in rate limiting middleware that supports per-client
(by IP) and per-route rate limits using a sliding window algorithm. The
middleware should support configurable backends (in-memory for single-process,
with a protocol for external stores like Redis). Include proper `429 Too Many
Requests` responses with `Retry-After` headers. Add the rate limit
configuration to the OpenAPI schema as a vendor extension.

### M2: Implement dependency overrides scoped to test context

The current `app.dependency_overrides` is a global dict that affects all
concurrent requests, making it unsafe for parallel test execution. Implement
a context-scoped override mechanism (using contextvars) so that dependency
overrides in one test don't leak into another test running concurrently.
Provide both a context manager and a pytest fixture for test authors.

### M3: Add response model validation in debug mode

Currently, FastAPI validates request inputs but does not validate response
bodies against the declared `response_model`. Add an optional debug mode
that validates outgoing responses against the response model and raises a
detailed error if the response doesn't match. This should be off by default
and configurable via an application setting. Include the validation error
details in the error response.

### M4: Implement WebSocket dependency injection

WebSocket routes don't support FastAPI's dependency injection system.
`Depends()` is silently ignored in WebSocket handlers. Implement full
DI support for WebSocket routes including generator dependencies with
cleanup, sub-dependencies, and security dependencies.

### M5: Add automatic request ID propagation

Implement a built-in request ID system. Generate a UUID for each request
(or accept from `X-Request-ID` header), make it available via dependency
injection, include it in all response headers, propagate it through
background tasks, and include it in log records via a custom log filter.

### M6: Implement API endpoint deprecation with sunset headers

Add `sunset` parameter to route decorators that sets the `Sunset` HTTP
header (RFC 8594) with the deprecation date. Include sunset information
in the OpenAPI schema. Add a middleware that logs warnings for requests
to endpoints past their sunset date. Support gradual deprecation with
warning periods.

### M7: Add OpenAPI schema customization hooks

Add hooks that allow modifying the generated OpenAPI schema before it's
served. Support `schema_extra` at the app level (modify the root schema),
route level (modify operation schemas), and parameter level. Add a
`SchemaTransformer` protocol for reusable schema modifications.

### M8: Implement circuit breaker pattern for dependencies

Add a `CircuitBreaker` dependency wrapper that stops calling a failing
dependency after a threshold of failures. Track failure counts per
dependency. Support half-open state for testing recovery. Return
configurable fallback responses when the circuit is open.

### M9: Add request body size limiting with streaming support

Implement configurable request body size limits. Reject oversized
requests with 413 before fully reading the body (use Content-Length
for pre-check, enforce during streaming). Support per-route limits
via route decorator parameters. Add rate limiting for request body
throughput.

### M10: Implement structured audit logging

Add an audit logging middleware that records who did what: endpoint
path, method, authenticated user, request parameters (with configurable
redaction of sensitive fields), response status, and duration. Support
pluggable audit sinks (file, database, external service). Add a
decorator for marking routes as audit-exempt.

## Wide

### W1: Add first-class support for API versioning

Implement API versioning support across the framework. Support URL-prefix
versioning (`/v1/`, `/v2/`), header-based versioning (`Accept-Version`),
and query parameter versioning (`?version=1`). This requires changes to
the router to support version-qualified route registration, the dependency
injection system to make the active version available as a dependency,
OpenAPI schema generation to produce per-version schemas, and the test
client to support specifying a version.

### W2: Implement structured error responses with problem details (RFC 9457)

Replace the current `HTTPException` error handling with a system that
produces RFC 9457 (Problem Details for HTTP APIs) compliant error responses.
This affects the exception classes, exception handlers, validation error
formatting, OpenAPI schema generation (error response schemas), and
documentation generation. Provide migration helpers for existing
`HTTPException` usage.

### W3: Add GraphQL support alongside REST

Implement a `FastAPI` extension that adds GraphQL endpoints using the
Strawberry library. Auto-generate GraphQL types from Pydantic models,
support queries/mutations/subscriptions, integrate with FastAPI's
dependency injection for resolvers, and share authentication/permission
logic between REST and GraphQL endpoints. Includes schema stitching
for combining multiple GraphQL schemas.

### W4: Implement multi-tenancy support

Add tenant-aware request handling. Support tenant resolution from
subdomain, header, or URL path prefix. Make the current tenant
available as a dependency. Add tenant-scoped database connections
(via dependency injection), tenant-scoped caching, and tenant-specific
configuration. Include OpenAPI schema generation per tenant.

### W5: Add server-sent events (SSE) with typed channels

Implement a typed SSE system. Define event channels with Pydantic
models for event data. Support multiple subscribers per channel,
automatic reconnection handling (via `Last-Event-ID`), heartbeat
pings, and backpressure when subscribers are slow. Add test utilities
for asserting SSE event sequences. Changes span routing, response
handling, and a new SSE module.

### W6: Implement request/response transformation pipeline

Add a middleware system for transforming requests and responses.
Support request body transformation (decompress, decrypt, validate
signatures), response body transformation (compress, encrypt, sign),
and header transformation. Transformers should compose in a pipeline.
Add built-in transformers for JSON Schema validation, response
envelope wrapping, and field-level encryption.

### W7: Add comprehensive health check framework

Implement a health check system with dependency health probes (database
connectivity, Redis availability, external API health), aggregated
health status, startup/liveness/readiness probe separation for
Kubernetes, health history tracking, and a health dashboard endpoint.
Support async health checks with configurable timeouts and caching.
Changes span middleware, routing, and a new health module.

### W8: Implement API analytics and usage tracking

Add request analytics collection: endpoint usage counts, response time
percentiles, error rates by endpoint, payload size distributions, and
client fingerprinting (by API key, user agent). Store in a pluggable
backend (in-memory, Redis, PostgreSQL). Add an admin endpoint for
querying analytics data. Support real-time streaming of analytics events.

### W9: Migrate to Pydantic v3 with backward compatibility

Update FastAPI to support Pydantic v3's new API while maintaining
backward compatibility with Pydantic v2 models. This affects model
validation, schema generation, serialization, dependency injection
type resolution, and the OpenAPI schema output. Add a compatibility
layer that detects the Pydantic version at import time and dispatches
to the appropriate code path.

### W10: Implement plugin system for FastAPI extensions

Add a formal plugin architecture that allows third-party packages to
register routes, middleware, dependencies, exception handlers, and
OpenAPI schema extensions through a declarative plugin interface.
Support plugin lifecycle hooks (startup, shutdown), plugin
dependencies/ordering, and plugin configuration via the app's settings.
Changes span the application class, router, middleware stack, and a
new plugin registry module.
