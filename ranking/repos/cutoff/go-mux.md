# gorilla/mux

| Field | Value |
|-------|-------|
| **URL** | https://github.com/gorilla/mux |
| **License** | BSD-3-Clause |
| **Language** | Go |
| **Scale** | Small |
| **Category** | HTTP router |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Clean single-package library with sharp separation
  of concerns — the `Router` and request dispatching (`mux.go`), route
  building and matching (`route.go`), path variable extraction and
  regexp compilation (`regexp.go`), middleware chaining (`middleware.go`),
  and test utilities (`test_helpers.go`). Small enough to read end-to-end
  yet rich enough for meaningful tasks.
- **Rich history**: 20K+ stars, 700+ commits. The most widely used Go
  HTTP router before the standard library added path parameters. PRs
  cover matching edge cases, middleware ordering, CORS, and
  performance optimization.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
mux/
├── mux.go                 # Router struct — ServeHTTP, Handle, HandleFunc, NewRouter
├── route.go               # Route struct — Path, Methods, Headers, Queries, Handler, Match
├── regexp.go              # routeRegexp — path variable extraction, template compilation
├── middleware.go          # MiddlewareFunc, Use, CORSMethodMiddleware
├── test_helpers.go        # Test helpers — SetURLVars for testing handlers
├── doc.go                 # Package documentation
├── old_test.go            # Legacy test suite
├── bench_test.go          # Benchmark suite
└── mux_test.go            # Main test suite
```

## Scale indicators

- ~6 Go source files
- ~2.5K lines of code
- Flat structure (single package, no subpackages)
- Zero external dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Router.ServeHTTP not setting 405 Method Not Allowed when path matches but method does not

When a request matches a route's path pattern but not its method
constraint, the router returns 404 Not Found instead of 405 Method Not
Allowed. The `Router.ServeHTTP` method in `mux.go` does not distinguish
between "no route found" and "route found but method mismatch." Fix the
match logic to track method mismatches and return 405 with an `Allow`
header listing permitted methods.

### N2: Fix Vars() returning nil instead of empty map for routes without path variables

When a route matches but has no path variables (e.g., a static path
`/health`), `mux.Vars(r)` returns `nil` instead of an empty map,
causing callers that iterate over the result to panic. Fix the variable
extraction path in `regexp.go` to always set a non-nil map in the
request context.

### N3: Fix Route.GetPathTemplate panicking for routes built with PathPrefix

When a route is created via `router.PathPrefix("/api")`, calling
`route.GetPathTemplate()` panics with a nil pointer dereference because
`PathPrefix` stores the regexp differently than `Path`. Fix
`GetPathTemplate()` in `route.go` to handle the prefix case by
returning the template from the prefix regexp.

### N4: Fix CORSMethodMiddleware not including OPTIONS in the Allow header

`CORSMethodMiddleware` sets the `Access-Control-Allow-Methods` header
based on methods registered for a route, but it omits `OPTIONS` even
when the middleware itself implicitly handles OPTIONS preflight requests.
Fix the middleware in `middleware.go` to always include `OPTIONS` in
the `Allow` header when CORS is active.

### N5: Fix path variable extraction failing when variable value contains encoded slashes

When a path variable value contains `%2F` (encoded slash), the regexp
matcher in `regexp.go` decodes it before matching, causing the variable
to be split incorrectly. Fix the regexp matching to operate on the
raw (encoded) path and decode variable values only after extraction.

### N6: Fix Route.Subrouter not inheriting middleware from the parent router

When a subrouter is created via `route.Subrouter()`, the middleware
registered on the parent router via `Use()` is not applied to requests
that match the subrouter's routes. Fix `Subrouter()` in `route.go` to
chain the parent router's middleware into the subrouter's middleware
stack.

### N7: Fix Route.URL not respecting custom regexp constraints in path variables

When a route is defined with `Path("/api/{version:[v][0-9]+}/resource")`,
`route.URL("version", "v2")` generates the correct URL but
`route.URL("version", "invalid")` does not report an error — it silently
produces an invalid URL. Fix `URL()` in `route.go` to validate variable
values against their regexp constraints before building the URL.

### N8: Fix Router.Walk skipping routes added after router.Use() was called

When middleware is added to a router via `Use()` and routes are added
afterward, `Router.Walk()` skips those routes because the walk
function iterates the route list captured before middleware registration.
Fix the walk implementation in `mux.go` to iterate the current route
list regardless of middleware registration order.

### N9: Fix HeadersRegexp matcher not compiling patterns once and reusing them

`Route.HeadersRegexp("Accept", "json.*")` compiles the regexp pattern
on every incoming request instead of compiling once during route
definition. This causes unnecessary allocations under load. Fix the
header matching in `route.go` to precompile and cache the regexp during
route construction.

### N10: Fix SetURLVars test helper not working with subrouter-matched requests

The `SetURLVars` test helper in `test_helpers.go` sets variables on the
request context, but when a handler is behind a subrouter, the
subrouter's match overwrites the context — discarding the test-injected
variables. Fix `SetURLVars` to use a context key that takes precedence
over the subrouter match.

## Medium

### M1: Implement route naming and URL generation with reverse routing

Add `Route.Name(name string)` to register named routes and
`Router.Get(name string)` to retrieve them by name. Implement
`Router.URLPath(name string, pairs ...string)` that builds a URL
from a named route's template by substituting path variables. Requires
a name registry on `Router`, integration with `Route.URL()` for
generation, error reporting for duplicate names, and handling of
subrouter-owned routes.

### M2: Add request body content-type based routing

Implement `Route.ContentType(types ...string)` that matches routes
based on the request's `Content-Type` header. Support MIME type
matching with wildcard subtypes (e.g., `application/*`). Requires a
new matcher in `route.go`, regexp-based MIME parsing, integration with
the 406/415 status code responses, and interaction with the existing
`Headers` matcher to avoid conflicts.

### M3: Implement route-level timeout middleware

Add `Route.Timeout(d time.Duration)` that wraps the route's handler
with a context timeout. When the timeout is exceeded, respond with
503 Service Unavailable and cancel the request context. Requires
integration with `http.TimeoutHandler` behavior, proper cleanup of
goroutines, an `OnTimeout` callback option per route, and interaction
with the middleware chain.

### M4: Add structured route listing and inspection API

Implement `Router.Routes() []RouteInfo` that returns a structured list
of all registered routes with their path templates, methods, handler
names, and middleware. Support JSON serialization for debugging
endpoints. Requires a `RouteInfo` struct, walking the route tree
including subrouters, handler name extraction via reflection, and
middleware listing.

### M5: Implement path variable type constraints

Add built-in type constraints for path variables: `{id:int}`,
`{slug:alpha}`, `{uuid:uuid}`. Each constraint type maps to a
precompiled regexp and an optional value converter. Requires a
constraint registry in `regexp.go`, template parser changes to
recognize constraint syntax, converter functions that return typed
values via context, and integration with `Route.URL()` for validation
during URL generation.

### M6: Add graceful 404/405 handling with custom negotiation

Implement `Router.NotFoundHandler` and `Router.MethodNotAllowedHandler`
with content negotiation: inspect the `Accept` header and respond with
JSON, XML, or plain text error bodies accordingly. Requires a
negotiation utility, default handlers in `mux.go`, integration with
the route matching to distinguish 404 from 405, and `Allow` header
generation for 405 responses.

### M7: Implement route versioning via header or path prefix

Add `Route.Version(v string)` and `Router.VersionFunc(func(r *http.Request) string)`
to route requests based on API version extracted from a header
(`Accept: application/vnd.api.v2+json`), query parameter, or path
prefix. Requires a version extractor interface, version-aware matching
in `route.go`, fallback to the latest version when unspecified, and
URL generation that includes the version.

### M8: Add request ID middleware and context propagation

Implement a built-in `RequestIDMiddleware` that extracts or generates
a unique request ID, stores it in the request context, and sets it as
a response header. Provide `mux.RequestID(r)` to retrieve the ID.
Requires a middleware implementation in `middleware.go`, UUID generation,
configurable header names (e.g., `X-Request-ID`), and integration with
the logging interface for correlation.

### M9: Implement route matching priority and ordering control

Add `Route.Priority(n int)` to explicitly control route evaluation
order when multiple routes could match. Higher-priority routes are
tested first. Requires a sorted route list in `Router`, re-sorting
when routes are added, consistent behavior with subrouters, and
clear documentation about the interaction with route registration
order.

### M10: Add route health status and circuit breaking

Implement `Route.HealthCheck(func() bool)` that marks a route as
unhealthy when the check fails. Unhealthy routes return 503 instead
of dispatching to the handler. Add `Router.HealthEndpoint(path string)`
that aggregates all route health checks and reports overall status.
Requires a health check registry, periodic check goroutine, thread-safe
status updates, and integration with the middleware chain.

## Wide

### W1: Implement a full middleware ecosystem with built-in handlers

Build a comprehensive middleware library: rate limiting (per-route and
global with token bucket), request logging (structured JSON with
latency, status, path), panic recovery with stack trace capture,
request/response body size limiting, gzip compression, and ETag
caching. Each middleware should be independently usable via `Use()`.
Changes span `middleware.go`, new middleware files, integration with
the router and route matching, configuration via options, and test
helpers for middleware testing.

### W2: Add OpenAPI/Swagger specification generation from route definitions

Implement `router.GenerateOpenAPI()` that produces an OpenAPI 3.0/3.1
specification from registered routes. Extract path templates, method
constraints, path variable types, query parameter matchers, header
constraints, and content-type requirements. Support user-supplied
metadata via `Route.Description()`, `Route.Tags()`, and struct-tag
inspection on handler parameters. Changes span route metadata
collection, a new OpenAPI builder module, JSON/YAML serialization,
path variable regexp-to-schema conversion, and integration with
subrouters.

### W3: Implement WebSocket and Server-Sent Events routing

Add `Route.WebSocket(handler WebSocketHandler)` and
`Route.SSE(handler SSEHandler)` for protocol-specific routing. WebSocket
routes should handle upgrade negotiation, subprotocol selection, and
origin validation. SSE routes should manage client connections, support
named events, and handle reconnection via `Last-Event-ID`. Changes span
new handler interfaces, upgrade detection in the matching logic,
connection management, route-specific middleware for WS/SSE, and test
helpers for both protocols.

### W4: Implement a testing framework for mux-based applications

Build a `mux.TestServer` that wraps a router with assertion helpers:
`Expect(method, path).WithHeaders(...).WithBody(...).ToReturn(status, body)`.
Support snapshot testing for response bodies, automatic route coverage
reporting (which routes were exercised), request recording for replay,
and parallel test safety. Changes span a new test framework module,
route instrumentation for coverage tracking, snapshot comparison
utilities, integration with `test_helpers.go`, and a response differ.

### W5: Add route-level authentication and authorization middleware

Implement a composable auth system: `Route.Auth(provider)` that
supports JWT validation, API key checking, and basic auth. Add
`Route.Authorize(policy)` for role/permission-based access control.
Include a `PermissionDenied` handler with content negotiation. Changes
span a new auth middleware module, token parsing and validation,
policy evaluation, integration with the request context for identity
propagation, route-level and router-level auth configuration, and
test utilities for auth simulation.

### W6: Implement request validation and binding framework

Add `mux.Bind(r, &target)` that reads and validates request data from
path variables, query parameters, headers, and body (JSON/XML/form)
into a struct using field tags. Support validation rules (`required`,
`min`, `max`, `pattern`), custom validators, and structured error
responses listing all validation failures. Changes span a new binding
module, tag parsing, type conversion, validation engine, error
formatting with field paths, integration with route variables from
`regexp.go`, and content-type detection.

### W7: Implement route grouping with shared configuration

Add `Router.Group(prefix string, opts ...GroupOption)` that creates a
route group sharing common path prefix, middleware, headers, and host
constraints. Groups should nest arbitrarily. Internally, groups should
use subrouters but expose a flatter API. Changes span
`Router`/`Route` construction in `mux.go` and `route.go`, middleware
chaining for groups, URL generation that respects group prefixes,
`Walk()` support for group metadata, and the route inspection API.

### W8: Add distributed tracing integration across the request lifecycle

Instrument the router with OpenTelemetry tracing: create spans for
route matching, middleware execution, and handler invocation. Propagate
trace context via W3C TraceContext headers. Record path template,
method, status code, and path variables as span attributes. Support
configurable sampling and span naming. Changes span the `ServeHTTP`
pipeline, middleware execution, route matching instrumentation, a
new tracing module, context propagation through subrouters, and test
helpers for trace assertion.

### W9: Implement hot-reloadable route configuration

Add `Router.ReloadRoutes(config RouteConfig)` that atomically replaces
the route table at runtime without dropping in-flight requests. Support
loading route definitions from JSON/YAML configuration with path, method,
handler reference, and middleware. Include a file watcher for auto-reload
on config changes. Changes span an atomic route table swap in `mux.go`,
a config parser, handler registry for name-to-handler mapping, graceful
drain of old routes, health endpoint updates, and integration with the
middleware chain.

### W10: Add comprehensive HTTP/2 and HTTP/3 push and routing support

Implement HTTP/2 server push integration: `Route.Push(resources ...string)`
that automatically pushes linked resources when a route is matched.
Add protocol-aware routing via `Route.Protocol("h2", "h3")` that
matches based on the request protocol. Support QUIC-specific headers
and Alt-Svc advertisement. Changes span protocol detection in the
matching logic, push promise integration in `mux.go`, Alt-Svc header
management, route-level push configuration, a push resource registry,
middleware support for protocol-specific behavior, and test helpers
for HTTP/2 and QUIC simulation.
