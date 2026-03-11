# gorilla/mux

| Field | Value |
|-------|-------|
| **URL** | https://github.com/gorilla/mux |
| **License** | BSD-3-Clause |
| **Language** | Go |
| **Scale** | Small |
| **Category** | HTTP router |
| **Set** | Cutoff |
| **Commit** | `db9d1d0073d27a0a2d9a8c1bc52aa0af4374d265` |

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

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix Router.Match not populating RouteMatch.MatchErr for host mismatch

When a request matches a route's path and method but fails the host
constraint (set via `Route.Host()`), `Router.Match()` returns `false`
with `match.MatchErr` left at its zero value instead of being set to
a distinguishable error. This makes it impossible for callers to
tell whether the match failed due to host, path, or method. Fix the
match loop in `mux.go` to set a `ErrHostMismatch` sentinel on
`RouteMatch.MatchErr` when the host matcher is the only failing
constraint.

### N2: Fix Vars() returning nil instead of empty map for routes without path variables

When a route matches but has no path variables (e.g., a static path
`/health`), `mux.Vars(r)` returns `nil` instead of an empty map,
causing callers that iterate over the result to panic. Fix the variable
extraction path in `regexp.go` to always set a non-nil map in the
request context.

### N3: Fix Route.Name silently overwriting names registered by other routes

When two different routes both call `.Name("api")`, the second
registration silently overwrites the first in the `namedRoutes` map
in `route.go`. `Router.Get("api")` then returns the wrong route with
no warning. The existing check in `Name()` only prevents renaming the
same route. Fix `Name()` to return an error when the name is already
registered by a different route in the `namedRoutes` map. Update
`doc.go` to document the name uniqueness constraint and the new error
return behavior.

### N4: Fix CORSMethodMiddleware not including OPTIONS in the Access-Control-Allow-Methods header

`CORSMethodMiddleware` sets the `Access-Control-Allow-Methods` header
based on methods registered for a route, but it only sets the header
when `OPTIONS` is already present in the collected methods list. When a
route handles OPTIONS explicitly (e.g., via `Methods(http.MethodOptions,
...)`), OPTIONS is included; however, the collected methods may omit
OPTIONS for routes that rely on the CORS middleware itself to handle
preflight. Fix `getAllMethodsForRoute` and the middleware loop in
`middleware.go` to always append `OPTIONS` to the methods list when
building the `Access-Control-Allow-Methods` response header, ensuring
preflight responses are correct even when OPTIONS is not in a route's
explicit method matcher.

### N5: Fix path variable extraction failing when variable value contains encoded slashes

When a path variable value contains `%2F` (encoded slash), the regexp
matcher in `regexp.go` decodes it before matching, causing the variable
to be split incorrectly. Fix the regexp matching to operate on the
raw (encoded) path and decode variable values only after extraction.

### N6: Fix Router.Walk sharing mutable ancestor slice across WalkFunc callbacks

In `mux.go`, the `walk` method reuses and mutates the `ancestors` slice
between iterations. It calls `ancestors = append(ancestors, t)` before
descending into a subrouter and truncates afterward, but the `WalkFunc`
receives the live slice. A callback that stores the `ancestors` reference
will see it mutated by later calls. Fix `walk` to pass a copy of the
ancestors slice to `WalkFunc` and when recursing into subrouters.

### N7: Fix Route.URL not percent-encoding path variable values containing reserved characters

In `regexp.go`, the `url()` method applies `url.QueryEscape` for query
variable values but inserts path variable values into the URL string
without calling `url.PathEscape`. If a path variable value contains
reserved characters like `?`, `#`, or space, the resulting URL from
`Route.URL()` is malformed. Fix `url()` in `regexp.go` to apply
`url.PathEscape` to path variable values before format-string
insertion.

### N8: Fix Router.ServeHTTP not setting request context for method-not-allowed handlers

In `mux.go`, when `Match()` returns `false` with
`match.MatchErr == ErrMethodMismatch`, the 405 handler is invoked
without the router context set on the request. Middleware and the
`MethodNotAllowedHandler` cannot call `mux.CurrentRouter(r)` because
`requestWithRouter` is only called in the match-success branch. Fix
`ServeHTTP` to call `requestWithRouter` before dispatching the 405
handler so the router is available in the request context.

### N9: Fix CORSMethodMiddleware producing duplicate methods in the Access-Control-Allow-Methods header

In `middleware.go`, `getAllMethodsForRoute` collects methods from all
routes that match the request path, but when multiple routes with
overlapping path matchers register the same HTTP methods, the collected
list contains duplicates (e.g., `GET,GET,POST`). The
`Access-Control-Allow-Methods` header is then set with repeated values.
Fix `getAllMethodsForRoute` to deduplicate the methods list before
returning.

### N10: Fix Router.ServeHTTP clean path redirect using 301 for non-GET methods

In `mux.go`, when `cleanPath` produces a path different from the
request, `ServeHTTP` always responds with `http.StatusMovedPermanently`
(301). For POST, PUT, and DELETE requests, most HTTP clients convert
the redirected request to GET, losing the original method and body.
Fix the redirect to use 308 (Permanent Redirect) for methods other
than GET and HEAD so the original method is preserved.

## Medium

### M1: Implement route conflict detection and diagnostics

Add `Router.CheckConflicts() []RouteConflict` that analyzes all
registered routes and detects overlapping patterns that could cause
ambiguous matching. Detect: routes with identical path templates but
different methods (informational), routes where one path is a prefix
of another without `PathPrefix` (warning), and routes with overlapping
regexp patterns in path variables. Requires path template comparison
logic in `regexp.go`, a `RouteConflict` struct with severity and
affected routes, integration with `Router.Walk()` for route collection,
and clear formatting of conflict reports. Add a "Route Diagnostics"
section to `README.md` documenting `CheckConflicts()` usage with
examples, and update the `Makefile` to add a `vet` target that runs
the conflict checker.

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

`Router` already exposes `NotFoundHandler` and `MethodNotAllowedHandler`
fields, but the built-in fallback handlers (`http.NotFoundHandler()` and
the internal `methodNotAllowedHandler()`) return plain-text responses
with no content negotiation. Add default negotiating implementations in
`mux.go` that inspect the `Accept` header and write JSON, XML, or
plain-text error bodies accordingly. Requires a negotiation utility,
new default handler constructors, integration with the route-matching
fallback paths in `ServeHTTP`, and `Allow` header generation for 405
responses listing the permitted methods.

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

### N11: Fix .github/workflows/test.yml not testing the latest Go versions

The test workflow matrix specifies `go: ['1.20','1.21']` but newer Go
releases (1.22, 1.23) are available. The `go.mod` declares `go 1.20`
as the minimum version but the CI matrix does not verify compatibility
with current releases. Update `.github/workflows/test.yml` to add Go
1.22 and 1.23 to the test matrix. Also add `.editorconfig` rules for
`*.yml` files to enforce 2-space indentation, consistent with the
existing workflow file formatting.

### M11: Add comprehensive project documentation and contributor resources

Create a `CONTRIBUTING.md` with guidelines for adding new matchers to
`route.go`, extending the regexp system in `regexp.go`, and writing
middleware in `middleware.go`. Add a `docs/` directory with
`architecture.md` describing the Router → Route → regexp matching
pipeline in `mux.go`, `route.go`, and `regexp.go`. Create a
`docs/examples.md` with common usage patterns: subrouters, middleware
chains, path variable extraction, and CORS configuration. Update
`README.md` to link to the new documentation and add badges for CI
status from `.github/workflows/test.yml` and Go report card. Update
the `LICENSE` copyright year to current.

### W11: Overhaul CI pipelines and project governance

Expand `.github/workflows/test.yml` to add a race condition detection
job (`go test -race`), a static analysis job using `golangci-lint`, and
a benchmark job that runs `bench_test.go` and compares results with the
base branch. Add a `.github/workflows/release.yml` for automated GitHub
release creation with changelog generation from `git log`. Create a
`.golangci.yml` configuration enabling `govet`, `errcheck`,
`staticcheck`, `ineffassign`, and `gosec` linters with appropriate
exclusions for test files. Add a `SECURITY.md` with a vulnerability
disclosure process and response timeline. Update `Makefile` with
`lint`, `bench`, and `coverage` targets. Update the existing
`.github/copilot-instructions.md` with project-specific coding
conventions for the router matching logic in `regexp.go` and
`mux.go`. Update `go.mod` comments to document the minimum Go version
policy.
