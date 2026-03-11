# gin-gonic/gin

| Field | Value |
|-------|-------|
| **URL** | https://github.com/gin-gonic/gin |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Medium |
| **Category** | HTTP web framework |
| **Set** | eval |
| **Commit** | `3e44fdc4d1636a2b1599c6688a76e13216a413dd` |

## Why this repo

- **Layered router**: Radix-tree routing with method trees, parameter/wildcard segments, middleware chains
- **Well-scoped**: Clear separation between engine (gin.go), context, routing, binding, render, recovery, logger
- **High adoption**: 80K+ stars, one of the most used Go web frameworks

## Structure overview

```
gin/
├── gin.go               # Engine: router mux, ServeHTTP, Run, route tree init
├── context.go           # Request context: params, query, bind, response render
├── routergroup.go       # Route group: Use, GET/POST/PUT, middleware chaining
├── tree.go              # Radix tree: node insertion, lookup, wildcard matching
├── errors.go            # Error type, error collection on context
├── recovery.go          # Panic recovery middleware
├── logger.go            # Logger middleware, custom formatters
├── response_writer.go   # ResponseWriter wrapper tracking status/size
├── auth.go              # BasicAuth middleware
├── mode.go              # Debug/release/test mode switching
├── path.go              # URL path cleaning
├── fs.go                # File-system helpers
├── utils.go             # Misc utilities
├── binding/             # Request binding: JSON, XML, YAML, form, query, protobuf
├── render/              # Response rendering: JSON, HTML, XML, YAML, protobuf, redirect
├── codec/json/          # Pluggable JSON codec (std, jsoniter, sonic, go_json)
├── ginS/                # Singleton default-engine convenience functions
└── internal/            # bytesconv, fs helpers
```

## Scale indicators

- ~59 Go source files (excluding tests)
- ~8K lines of code
- Radix-tree router with parameter extraction
- Pluggable binding and render subsystems

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `Context.Redirect` allowing redirect with status code 201

`render.Redirect.Render()` in `render/redirect.go` validates that the status code is in the 300–308 range but contains a special-case exception that also permits `http.StatusCreated` (201). Calling `c.Redirect(201, "/path")` sets a `Location` header and returns a 201 response with no redirect semantics, because 201 Created is not a redirect status code. Remove the `&& r.Code != http.StatusCreated` exception from the validation condition so that only genuine 3xx redirect codes are accepted.

### N2: Fix route conflict panic not reporting the full existing conflicting path

When `addRoute` detects a wildcard conflict in `tree.go`, the panic message reconstructs the existing prefix by string manipulation (`fullPath[:strings.Index(fullPath, pathSeg)] + n.path`) instead of using `n.fullPath`, which stores the complete registered path of the conflicting route. The reconstructed prefix can be incomplete or misleading. Use `n.fullPath` directly in the panic message to show the actual registered route that caused the conflict.

### N3: Fix `BasicAuth` middleware leaking credential position via early-exit loop

The `searchCredential` method on `authPairs` in `auth.go` iterates over credentials using `subtle.ConstantTimeCompare` for each individual comparison, but returns immediately when a match is found. Because the loop exits early, response time varies depending on which position in the slice holds the matching credential, creating a timing side-channel that reveals which credential was accepted. Rewrite the loop to always complete all comparisons, accumulating the result in a constant-time variable without early exit.

### N4: Add `Context.RemoveHeader` method

`Context.Header` can set and delete response headers (empty value deletes), but the delete behavior is undiscoverable. Add an explicit `RemoveHeader(key string)` method on `Context` that calls `c.Writer.Header().Del(key)`.

### N5: Fix `LoggerWithConfig` not sanitizing newlines in request path

The default `LoggerWithConfig` writes `c.Request.URL.Path` directly into log output. A request path containing `\n` can inject fake log lines. Sanitize control characters in the path before formatting. Update `CONTRIBUTING.md` with security-related logging guidelines for future middleware contributions.

### N6: Fix `redirectTrailingSlash` not updating `RawPath` when redirecting

`redirectTrailingSlash` in `gin.go` modifies `req.URL.Path` to add or remove a trailing slash, but does not update `req.URL.RawPath`. When the request URL contains percent-encoded characters (e.g., `/api/hello%20world`), `net/url.URL.String()` prefers `RawPath` over `Path`, so the redirect response uses the original unmodified `RawPath` and the trailing slash change is silently ignored, potentially causing an infinite redirect loop. Update `req.URL.RawPath` in `redirectTrailingSlash` consistently with `req.URL.Path` when `RawPath` is set.

### N7: Add `Context.FullError` to collect all handler errors as a single joined error

`Context.Errors` collects `*Error` values, but there is no convenience method to return a single `error` that joins them. Add `FullError() error` that returns `nil` when empty or `errors.Join`-ed result.

### N8: Fix `Engine.HandleContext` not saving and restoring `Context.Keys` across re-dispatch

`HandleContext` in `gin.go` saves and restores `c.index` and `c.handlers` around the internal re-dispatch, but calls `c.reset()` which sets `c.Keys = nil`. Any context values stored by middlewares before the `HandleContext` call — such as authenticated user data, request IDs, or tracing spans — are permanently lost. The re-dispatched route and any subsequent middleware in the original chain cannot access those values. Save `c.Keys` before `c.reset()` and restore it after `engine.handleHTTPRequest(c)`, alongside the existing `c.index` and `c.handlers` restoration.

### N9: Fix `SecureJSON` prefix not configurable per-route

`SecureJSONPrefix` is set on the `Engine` globally. When different route groups need different prefixes (e.g., internal vs. external APIs), there is no way to override it. Add a `Context.SetSecureJSONPrefix` method that takes precedence over the engine default.

### N10: Fix `ShouldBindBodyWith` exhausting `c.Request.Body` without restoring it

`ShouldBindBodyWith` in `context.go` reads and caches the request body under `BodyBytesKey` in `c.Keys`. After the first call, `c.Request.Body` is fully consumed and subsequent reads return EOF. Any call that reads `c.Request.Body` directly — such as `ShouldBind`, `ShouldBindJSON`, or `c.GetRawData()` — receives empty data instead of the original body. After caching the bytes, `ShouldBindBodyWith` should replace `c.Request.Body` with `io.NopCloser(bytes.NewReader(body))` so that the body remains readable for other consumers in the same request lifecycle.

### N11: Update `docs/doc.md` and `README.md` to document the `Context.RemoveHeader` method

The new `Context.RemoveHeader` method is undocumented. Add an API reference entry with signature and usage example to `docs/doc.md`. Update `README.md` with a code snippet in the response helpers section. Add a feature entry to `CHANGELOG.md`.

## Medium

### M1: Implement request-scoped timeout middleware

Add a `Timeout(duration time.Duration)` middleware that wraps the request context with `context.WithTimeout`. When the timeout fires, the middleware should call `c.Abort()` and respond with 504 Gateway Timeout. Coordinate with `response_writer.go` to prevent double-writes after timeout.

### M2: Add route-level metadata annotations

Implement a mechanism to attach arbitrary metadata (tags, descriptions, deprecation flags) to routes at registration time. Add `RouterGroup.Handle` variant that accepts `RouteOption` functions. Expose metadata through `RoutesInfo` and a new `RouteInfo.Metadata` field.

### M3: Implement ETag response middleware

Add middleware that computes a weak ETag from response body hash, sets the `ETag` header, and returns 304 Not Modified when the request includes a matching `If-None-Match`. Integrate with `ResponseWriter` to buffer the body for hashing before flushing. Update `README.md` with ETag middleware usage examples and configuration options, and add a `CHANGELOG.md` entry.

### M4: Add request ID middleware with propagation

Implement middleware that reads `X-Request-ID` from the incoming request or generates a UUID if absent. Store it on the context, inject it into all response headers, and make it available in `LogFormatter` via `LogFormatterParams`. Changes touch middleware creation, context, and logger.

### M5: Implement CORS middleware with preflight caching

Add a configurable CORS middleware supporting allowed origins (including wildcards), methods, headers, credentials, max-age. Handle OPTIONS preflight requests. Integrate with `RouterGroup` to allow per-group CORS configuration separate from global settings.

### M6: Add response compression middleware with content negotiation

Implement middleware that compresses response bodies using gzip or deflate based on `Accept-Encoding`. Skip compression for small bodies, already-compressed content types, and SSE streams. Wrap `ResponseWriter` to handle streaming compression.

### M7: Implement structured error handling with error types

Add an `ErrorHandler` function type on `Engine` that is called after the handler chain completes when `Context.Errors` is non-empty. Implement typed error matching so handlers can use `errors.As` on `*gin.Error` to extract domain-specific error types and map them to appropriate HTTP responses.

### M8: Add form binding support for nested structs with array indices

The form binding in `binding/form_mapping.go` does not support nested struct arrays like `items[0].name=foo&items[1].name=bar`. Implement array-index parsing in `form_mapping.go`, update `multipart_form_mapping.go`, and add validation for sparse/out-of-order indices.

### M9: Implement file upload progress callback

Add `Context.SaveUploadedFileWithProgress` that accepts a callback `func(bytesWritten, totalBytes int64)` invoked during file copy. Wrap the `io.Copy` in `SaveUploadedFile` with a progress-reporting `io.Writer` and surface the file size from the multipart header.

### M10: Add render negotiation based on route-level accepted types

Extend `Context.Negotiate` to support route-level configuration of offered formats. Currently `NegotiateFormat` uses the `Accepted` field set globally. Add per-`RouterGroup` default offered types and allow individual handlers to override via `NegotiateConfig` options.

### M11: Add release automation and documentation for structured error handling

Update `.goreleaser.yaml` with changelog generation rules for error handling features. Add an error handling guide to `docs/doc.md` covering error types, `ErrorHandler` customization, and `ProblemDetail` patterns. Update `CONTRIBUTING.md` with error type development guidelines and testing expectations. Update `.github/workflows/goreleaser.yml` release note templates and add `CHANGELOG.md` entries.

## Wide

### W1: Implement graceful shutdown with connection draining

Add `Engine.Shutdown(ctx context.Context)` that stops accepting new connections, waits for in-flight requests to complete, and respects the context deadline. Changes span `gin.go` (Run/RunTLS lifecycle), `response_writer.go` (tracking active connections), and a new `shutdown.go` module for coordination.

### W2: Add OpenAPI 3.0 schema generation from routes

Implement automatic OpenAPI spec generation by inspecting registered routes, binding struct tags, and render types. Changes span `routergroup.go` (route metadata capture), `binding/` (struct-to-schema conversion), `render/` (response type inference), and a new `openapi/` package. Update `docs/doc.md` with OpenAPI schema generation documentation and add CI validation in `.github/workflows/gin.yml` for spec correctness.

### W3: Implement rate limiting with pluggable backends

Add rate-limiting middleware supporting fixed-window and sliding-window algorithms. Implement in-memory and Redis backends. Configuration per route group with burst, limit, key extraction (IP, header, param). Changes span middleware creation, context key storage, a new `ratelimit/` package, and `routergroup.go` integration.

### W4: Add WebSocket support with upgrade handling

Implement WebSocket upgrade middleware that integrates with gin's context and middleware chain. Support route-level WebSocket handlers via `RouterGroup.WS(path, handler)`. Add connection lifecycle hooks (open, message, close, error). Changes span `routergroup.go`, `context.go`, `gin.go`, and a new `ws/` package.

### W5: Implement request validation framework with custom rules

Add a validation layer beyond struct tag binding. Support custom validation rules registered per field/type, cross-field validation, localized error messages, and validation groups. Changes span `binding/` (validator integration), `context.go` (validation helpers), `errors.go` (validation error type), and a new `validation/` package.

### W6: Add distributed tracing integration

Implement OpenTelemetry tracing middleware that creates spans for each request, propagates trace context through headers, annotates spans with route pattern and status. Add trace context to `Context`, integrate with `logger.go` for correlated logging, and update `recovery.go` to record panic spans.

### W7: Implement template rendering engine with layout support

Extend the HTML rendering system to support template layouts, partials, blocks, and template inheritance. Add a template registry with hot-reload in debug mode. Changes span `render/html.go` (layout rendering), `gin.go` (template configuration), `debug.go` (hot-reload), and a new `templates/` loader package.

### W8: Add server-sent events (SSE) streaming framework

Implement a structured SSE module with event broker pattern: clients subscribe to named channels, handlers publish events, and the framework manages connection lifecycle and heartbeats. Changes span `context.go` (SSEvent rework), `response_writer.go` (flush support), `routergroup.go` (SSE route registration), and a new `sse/` package.

### W9: Implement plugin system for engine extensions

Add a plugin registration API where plugins can hook into the engine lifecycle (startup, shutdown, route registration, request handling). Plugins can add middleware, modify configuration, and register routes. Changes span `gin.go` (plugin registry and lifecycle), `routergroup.go` (plugin route hooks), `context.go` (plugin context access), and a new `plugin/` package.

### W10: Add HTTP/2 server push support

Implement server push integration where handlers can trigger push promises for preloading resources. Add `Context.Push(path string, opts *PushOptions)` that uses the `http.Pusher` interface. Changes span `context.go` (push API), `response_writer.go` (Pusher interface assertion), `gin.go` (HTTP/2 configuration), and `render/html.go` (auto-push linked assets).

### W11: Overhaul CI, benchmarks, and contributor docs for HTTP/2 server push

Update `.github/workflows/gin.yml` with an HTTP/2 test matrix including TLS configurations. Add TLS test certificates to `testdata/certificate/` for HTTP/2 testing. Update `BENCHMARKS.md` with HTTP/2 push performance benchmarks and comparison tables. Update `CONTRIBUTING.md` with HTTP/2 development and testing guidelines. Update `README.md` with HTTP/2 push usage examples, update `docs/doc.md` with push API reference, and add feature entries to `CHANGELOG.md`.
