# dotnet/aspnetcore

| Field | Value |
|-------|-------|
| **URL** | https://github.com/dotnet/aspnetcore |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Large |
| **Category** | Web framework |
| **Set** | Cutoff |
| **Commit** | `2b1cb0a7ae862ba622fd172a960ed937063c5f08` |

## Why this repo

- **Full-stack web framework**: ASP.NET Core spans the entire web stack
  — HTTP abstractions, routing, MVC, Razor Pages, Blazor components,
  SignalR real-time, minimal APIs, middleware pipeline, authentication,
  authorization, CORS, caching, health checks, OpenAPI, diagnostics,
  hosting, and server abstractions. Each module is a distinct project
  with clear boundaries.
- **Rich history**: 35K+ stars, maintained by Microsoft with extensive
  community contributions. Covers security fixes, performance
  optimizations, API design evolution, and cross-platform support.
- **Permissive**: MIT license.
- **Scale anchor**: Large codebase (10,000+ C# source files across
  50+ projects) anchoring the "Large" end of scale assessment.

## Structure overview

```
src/
├── Http/
│   ├── Http/src/                # DefaultHttpContext, FormCollection, HeaderDictionary
│   ├── Http.Abstractions/       # HttpContext, HttpRequest, HttpResponse interfaces
│   ├── Http.Features/           # Feature interfaces (IHttpRequestFeature, etc.)
│   ├── Http.Extensions/         # Extension methods, RequestDelegate helpers
│   ├── Http.Results/            # IResult implementations (Ok, NotFound, Json, etc.)
│   ├── Routing/src/             # Endpoint routing, route matching, link generation
│   ├── Routing.Abstractions/    # IEndpointRouteBuilder, RouteValueDictionary
│   ├── Headers/                 # Typed header parsing and generation
│   ├── WebUtilities/            # Query string helpers, multipart parsing
│   ├── Authentication.Abstractions/  # Auth schemes, claims, tickets
│   ├── Authentication.Core/     # AuthenticationService, handler infrastructure
│   └── Metadata/                # Endpoint metadata (attributes, conventions)
├── Mvc/
│   ├── Mvc.Core/src/            # Controllers, action selection, model binding,
│   │                            #   filters, formatters, routing integration
│   ├── Mvc.Abstractions/        # ActionResult, IActionFilter interfaces
│   ├── Mvc.ApiExplorer/         # API description for documentation generation
│   ├── Mvc.DataAnnotations/     # Validation attribute integration
│   ├── Mvc.Cors/                # CORS policy per-action/controller
│   └── Mvc.Formatters.Json/     # JSON input/output formatters
├── Middleware/
│   ├── CORS/src/                # CORS middleware and policy builder
│   ├── Diagnostics/src/         # Developer exception page, status code pages
│   ├── HttpLogging/src/         # HTTP request/response logging, W3C logging
│   ├── HttpsPolicy/             # HTTPS redirection middleware
│   ├── HostFiltering/           # Host header validation
│   ├── HttpOverrides/           # Forwarded headers middleware
│   ├── Localization/            # Request localization middleware
│   └── HealthChecks/            # Health check middleware and endpoints
├── Hosting/
│   ├── Hosting/                 # WebHost builder, startup, server integration
│   ├── Abstractions/            # IWebHost, IWebHostBuilder interfaces
│   ├── Server.Abstractions/     # IServer, IHttpApplication interfaces
│   └── TestHost/                # In-process test server
├── SignalR/
│   ├── common/                  # Protocol definitions, hub protocol
│   ├── clients/csharp/          # .NET SignalR client
│   ├── clients/ts/              # TypeScript SignalR client
│   └── clients/java/            # Java SignalR client
├── Components/                  # Blazor components, rendering, JSInterop
├── Identity/                    # ASP.NET Core Identity (users, roles, tokens)
├── OpenApi/                     # OpenAPI document generation
├── Caching/                     # Distributed caching (Redis, SQL Server)
├── Antiforgery/                 # Anti-CSRF token middleware
├── DataProtection/              # Data protection (encryption key management)
├── Grpc/                        # gRPC integration
└── DefaultBuilder/              # WebApplication.CreateBuilder() defaults
```

## Scale indicators

- 10,000+ C# source files across 50+ projects
- Deep hierarchy: HTTP stack → middleware → MVC → Blazor → SignalR
- Complex build system (MSBuild with shared SDK, source generators)
- Extensive cross-project dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add request body size limit validation to DefaultHttpContext

`DefaultHttpContext` in `src/Http/Http/src/DefaultHttpContext.cs` does
not enforce request body size limits by default. When
`MaxRequestBodySize` is configured on the server, the limit is checked
at the server layer but not surfaced in `HttpContext` for middleware
inspection. Add a `RequestBodySizeLimit` property to
`DefaultHttpContext` that exposes the configured limit and throws
`BadHttpRequestException` when the body exceeds it during buffered
reads.

### N2: Fix HeaderDictionary not validating header name characters

`HeaderDictionary` in `src/Http/Http/src/HeaderDictionary.cs` accepts
arbitrary string keys without validating that they conform to RFC 7230
token rules (no spaces, control characters, or delimiters). Setting
`headers["Invalid Header!"] = "value"` silently succeeds. Add
validation in the indexer setter that rejects non-token characters
with an `ArgumentException`.

### N3: Add Results.NotModified() for HTTP 304 conditional responses

The `Results` and `TypedResults` classes in `src/Http/Http.Results/src/`
provide typed results for common HTTP status codes (`Results.NoContent()`,
`Results.Ok()`, `Results.Conflict()`) but have no `Results.NotModified()`
method for HTTP 304. Applications that implement custom conditional GET
logic (checking `If-None-Match` or `If-Modified-Since` themselves)
must fall back to `Results.StatusCode(304)`, which conveys no semantic
intent and has no type-safe equivalent in `TypedResults`. Add
`Results.NotModified()` and `TypedResults.NotModified()` backed by a
new `NotModifiedHttpResult` class (following the same pattern as
`NoContentHttpResult`) that sets status 304 with no response body.
Register the result in `ResultsCache` and update `IResultExtensions`.
Touches `src/Http/Http.Results/src/` (3–4 files).

### N4: Fix CorsPolicyBuilder not validating origin URL format

`CorsPolicyBuilder.WithOrigins()` in `src/Middleware/CORS/src/Infrastructure/CorsPolicyBuilder.cs`
accepts origin strings without validating their format. Passing
`"not-a-url"` or `"http://example.com/path"` (origins should not have
paths) is silently accepted and produces a policy that never matches.
Add URI validation that checks for scheme + authority format and
rejects origins with paths or query strings.

### N5: Add structured logging fields to HttpLoggingMiddleware

`HttpLoggingMiddleware` in `src/Middleware/HttpLogging/src/HttpLoggingMiddleware.cs`
logs request/response details as formatted strings. Add structured
logging using `LoggerMessage.Define` with named fields (`RequestMethod`,
`RequestPath`, `StatusCode`, `Duration`) so log aggregators can index
and query individual fields without parsing. Also update
`docs/EventSourceAndCounters.md` to document the new structured logging
fields, their names, types, and usage examples for common log
aggregation tools.

### N6: Fix DeveloperExceptionPageMiddleware ProblemDetails not conforming to RFC 7807

The `DeveloperExceptionPageMiddlewareImpl.CreateProblemDetails()` in
`src/Middleware/Diagnostics/src/DeveloperExceptionPage/DeveloperExceptionPageMiddlewareImpl.cs`
sets `ProblemDetails.Title` to the exception type name (e.g.,
`"NullReferenceException"`) but never sets `ProblemDetails.Type`,
which defaults to `null` (treated as `about:blank` by RFC 7807).
RFC 7807 requires that when `type` is `about:blank`, `title` MUST
equal the HTTP status reason phrase (e.g., `"Internal Server Error"`
for 500). Fix `CreateProblemDetails()` to set `Type` to `about:blank`
and change `Title` to the HTTP status reason phrase, then add an
`ExceptionTypeUriProvider` delegate property to
`DeveloperExceptionPageOptions` that allows callers to supply a
documentation URI for known exception types, overriding `about:blank`.
Touches `src/Middleware/Diagnostics/src/DeveloperExceptionPage/`
(2–3 files).

### N7: Add QueryCollection.TryGetValues() for multi-value query parameters

`QueryCollection` in `src/Http/Http/src/QueryCollection.cs` provides
`TryGetValue()` that returns a `StringValues`, but there is no method
that returns `false` when the key is missing while also providing
empty default handling. Add `TryGetValues(string key, out string[] values)`
that splits `StringValues` into a typed array and returns `false` for
missing keys.

### N8: Fix FormFile.CopyToAsync not respecting cancellation token promptly

`FormFile.CopyToAsync()` in `src/Http/Http/src/FormFile.cs` passes the
cancellation token to the underlying stream copy but does not check
for cancellation before starting the copy. For large files, the
cancellation may not take effect until the next buffer read. Add an
early `cancellationToken.ThrowIfCancellationRequested()` check and
reduce the default buffer size for more responsive cancellation.

### N9: Add BindingAddress.TryParse() for safe address parsing

`BindingAddress.Parse()` in `src/Http/Http/src/BindingAddress.cs`
throws `FormatException` for invalid addresses. Add a
`BindingAddress.TryParse(string address, out BindingAddress result)`
method that returns `false` instead of throwing, matching the .NET
`TryParse` pattern for configuration scenarios where invalid addresses
should be logged rather than crash the application.

### N10: Fix SendFileFallback not setting Content-Length header

`SendFileFallback` in `src/Http/Http/src/SendFileFallback.cs` streams
file content to the response but does not set the `Content-Length`
header when the file length is known. This forces chunked transfer
encoding even for small files. Set `Content-Length` from the file
length when the response has not yet started and no other transfer
encoding is configured.

### N11: Fix global.json SDK version constraint blocking local builds

The `global.json` at the repo root pins the SDK to a specific preview
version (`11.0.100-preview.1.26104.118`) using an exact version match
with no `rollForward` policy. Contributors with a compatible but not
identical SDK installed cannot build locally without running
`restore.sh` / `restore.cmd` first. Update `global.json` to add a
`rollForward` policy of `latestFeature` under the `sdk` object so
that compatible minor SDK versions are accepted, and add
`allowPrerelease: true` to permit preview SDKs. Also update
`.markdownlint.json` to add a rule enforcing that any documentation
file referencing a specific SDK version includes a note directing
users to run `restore.sh` / `restore.cmd` to bootstrap the required
SDK. Touches `global.json` and `.markdownlint.json`.

## Medium

### M1: Add rate limit response headers to the existing rate limiting middleware

The rate-limiting middleware in `src/Middleware/RateLimiting/src/`
rejects requests with `429 Too Many Requests` but does not add
standard rate limit response headers (`X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `Retry-After`) to either successful responses
(informing clients of current usage) or rejected responses (informing
clients when to retry). Add an `IRateLimitHeadersPolicy` interface
and default implementations that extract metadata from `RateLimitLease`
(via `TryGetMetadata<RetryAfterMetadata>()`) to populate these
headers. Add a `EmitRateLimitHeaders` property to `RateLimiterOptions`
(default `false`) and update the existing `RateLimiterApplicationBuilderExtensions`
to invoke the header policy. Extend `RateLimiterOptionsExtensions`
so `AddFixedWindowLimiter` and `AddSlidingWindowLimiter` can optionally
include limit metadata in the lease. Touches `src/Middleware/RateLimiting/src/`
(5–6 files).

### M2: Add distributed cache backing store to ResponseCaching middleware

The `ResponseCachingMiddleware` in `src/Middleware/ResponseCaching/src/`
caches responses using `IResponseCache`, but the only built-in
implementation is `MemoryResponseCache`. There is no
`IDistributedCache`-backed store, so cached responses cannot be
shared across server instances in a load-balanced deployment. Add a
`DistributedResponseCache` class implementing `IResponseCache` that
serializes and stores `CachedResponse` and `CachedVaryByRules` entries
using `IDistributedCache`, with configurable key prefix and absolute
expiry. Add `UseDistributedResponseCaching()` and
`AddDistributedResponseCaching()` DI extension methods. Touches
`src/Middleware/ResponseCaching/src/` (4–5 files) and
`src/Caching/` for any shared distributed-cache helpers.

### M3: Implement health check aggregation with dependency tree

Extend the health check system in `src/Middleware/HealthChecks/` to
support dependency trees: health check A depends on B and C, and
the aggregate status reflects the dependency graph. Add parallel
execution with timeouts per check, machine-readable output
(JSON with nested dependency status), and a UI endpoint for
visualization. Touches `src/Middleware/HealthChecks/`,
`src/Http/Http.Results/`, and DI extensions.

### M4: Add global/cross-document transformer registration to OpenAPI

The OpenAPI transformer system in `src/OpenApi/src/Transformers/` and
`src/OpenApi/src/Services/OpenApiOptions.cs` requires transformers to
be registered per named document via
`services.AddOpenApi("v1", opts => opts.AddDocumentTransformer<T>())`.
There is no way to register a transformer that automatically applies
to ALL named OpenAPI documents without enumerating each name. Add
`AddOpenApiDocumentTransformer<T>()`, `AddOpenApiOperationTransformer<T>()`,
and `AddOpenApiSchemaTransformer<T>()` extension methods on
`IServiceCollection` in `src/OpenApi/src/Extensions/OpenApiServiceCollectionExtensions.cs`
that store global transformer registrations in a shared
`OpenApiGlobalTransformersOptions`. Update `OpenApiDocumentService`
to merge global transformers with per-document transformers during
document generation. Touches `src/OpenApi/src/Extensions/`,
`src/OpenApi/src/Services/OpenApiOptions.cs`,
`src/OpenApi/src/Services/OpenApiDocumentService.cs`, and a new
`OpenApiGlobalTransformersOptions` class.

### M5: Implement request body validation middleware

Add a validation middleware that validates request bodies against
registered schemas before they reach action handlers. Support JSON
Schema validation for minimal APIs, DataAnnotations validation for
MVC, and custom validators via DI. Return `400 Bad Request` with
a `ProblemDetails` response listing all violations. Touches
`src/Http/Http.Extensions/`, `src/Mvc/Mvc.Core/`, and a new
validation project.

### M6: Add WebSocket compression support to SignalR

SignalR in `src/SignalR/` transports messages over WebSockets but
does not support per-message compression (RFC 7692). Implement
per-message deflate negotiation, compression/decompression in the
WebSocket transport, configurable compression level, and integration
with the existing hub protocol negotiation. Touches
`src/SignalR/common/`, `src/SignalR/clients/csharp/`, and the
server-side transport.

### M7: Add ordered endpoint filter execution via IOrderedEndpointFilter

The endpoint filter pipeline in
`src/Http/Routing/src/Builder/EndpointFilterExtensions.cs` executes
filters in registration order with no mechanism to control relative
ordering between filters added at different call sites (e.g., handler
level vs. group level). Add an `IOrderedEndpointFilter` interface
in `src/Http/Http.Abstractions/src/` extending `IEndpointFilter` with
an `int Order { get; }` property. Update
`RequestDelegateFilterPipelineBuilder` in `src/Http/Routing/src/` to
sort filters implementing `IOrderedEndpointFilter` by `Order` before
building the pipeline (filters without an order are treated as
`Order = 0`). Add `AddEndpointFilter<TFilter>(int order)` convenience
overloads to `EndpointFilterExtensions` that wrap an existing filter
with the specified order. Touches `src/Http/Http.Abstractions/src/`
(new interface), `src/Http/Routing/src/` (pipeline builder and
extensions), and `src/Http/Http.Extensions/` (delegate factory
integration).

### M8: Add layered Content-Encoding decompression to RequestDecompression middleware

The `DefaultRequestDecompressionProvider` in
`src/Middleware/RequestDecompression/src/DefaultRequestDecompressionProvider.cs`
returns `null` and logs a warning when `Content-Encoding` contains
multiple tokens (e.g., `Content-Encoding: gzip, br`), silently skipping
decompression. Per RFC 7231, multiple `Content-Encoding` values
represent sequential compression applied in order; decompression should
be applied in reverse. Add support for layered decompression in
`DefaultRequestDecompressionProvider` by iterating the encoding tokens
in reverse and chaining their decompression streams. Add an
`AllowMultipleEncodings` property (default `false`) to
`RequestDecompressionOptions` as an opt-in guard against unexpected
multi-encoding payloads. Update `RequestDecompressionMiddleware` to
correctly dispose all chained streams in the `finally` block. Touches
`src/Middleware/RequestDecompression/src/` (3–4 files).

### M9: Add per-exception-type ProblemDetails customization to ExceptionHandlerOptions

The `ExceptionHandlerOptions` in
`src/Middleware/Diagnostics/src/ExceptionHandler/ExceptionHandlerOptions.cs`
supports mapping exceptions to HTTP status codes via
`StatusCodeSelector`, but provides no mechanism to map exception types
to structured `ProblemDetails` instances with custom `Type` URIs,
`Title`, or `Detail` fields. Add an `ExceptionToProblemDetailsMapper`
delegate property of type
`Func<HttpContext, Exception, ProblemDetails?>?` to
`ExceptionHandlerOptions`. Update `ExceptionHandlerMiddlewareImpl` to
invoke the mapper before falling back to `IProblemDetailsService`,
merging the returned `ProblemDetails` into the response. Add
`MapExceptionToProblemDetails<TException>()` and
`MapExceptionToProblemDetails()` convenience extension methods to
`ExceptionHandlerServiceCollectionExtensions` for registering
per-type mappings without replacing the entire delegate. Touches
`src/Middleware/Diagnostics/src/ExceptionHandler/` (4 files).

### M10: Add HTTP/3 QUIC connection metrics

The server abstractions support HTTP/3 but the HTTP logging and
diagnostics middleware do not capture QUIC-specific metrics (connection
migration events, 0-RTT resumption, stream multiplexing counts).
Add QUIC transport metrics to the diagnostics middleware using
`System.Diagnostics.Metrics`, with named instruments for each
metric category. Touches `src/Middleware/HttpLogging/`,
`src/Middleware/Diagnostics/`, and `src/Hosting/Server.Abstractions/`.

### M11: Add npm workspace build integration for SignalR TypeScript client

The `package.json` at the repo root defines npm workspaces for the
SignalR TypeScript client (`src/SignalR/clients/ts/`), Blazor JS
interop, and component custom elements. However, the CI pipeline in
`.github/workflows/` does not run `npm test` or `npm run lint` for
these workspaces as part of the main build. Add a CI workflow step
that runs `npm ci && npm run build && npm run test` for the npm
workspaces, update `package.json` to add a `typecheck` script using
`tsc --noEmit`, and update the `eng/targets/Node.Common.targets`
MSBuild target to invoke npm workspace builds during the solution-level
build so TypeScript compilation errors are caught alongside C# builds.

## Wide

### W1: Implement API versioning across minimal APIs and MVC

Add first-class API versioning: `app.MapGet("/api/v{version}/users",
handler).HasApiVersion(2.0)`. Support URL-segment, query-string,
header, and media-type versioning strategies. Include version
negotiation, sunset headers, OpenAPI document generation per version,
and deprecation warnings. Changes span `src/Http/Routing/`,
`src/Mvc/Mvc.Core/`, `src/OpenApi/`, `src/Http/Http.Abstractions/`,
and a new versioning project.

### W2: Add multi-tenant architecture support

Implement tenant resolution middleware that identifies tenants from
subdomain, path, header, or claim. Add per-tenant service scoping
in DI, tenant-specific configuration, tenant-isolated data protection
keys, per-tenant authentication schemes, and tenant-aware routing.
Changes span `src/Hosting/`, `src/Http/Http/`, `src/Http/Routing/`,
`src/Identity/`, `src/DataProtection/`, and a new multi-tenancy
project.

### W3: Implement real-time API change notification via SignalR

Add a system where API consumers subscribe to endpoint changes via
SignalR. When routes, schemas, or versions change (detected at
startup or via hot-reload), connected clients receive structured
notifications with diffs. Include a JavaScript SDK for client
integration and webhook fallback. Changes span `src/SignalR/`,
`src/OpenApi/`, `src/Http/Routing/`, `src/Hosting/`, and a new
notification project.

### W4: Add distributed tracing integration across all middleware

Implement end-to-end distributed tracing using `System.Diagnostics.Activity`:
HTTP request/response spans, middleware execution spans, MVC action
spans, SignalR hub method spans, database query spans (via
`DbConnection` instrumentation), and external HTTP client spans.
Include trace context propagation (`traceparent` header), span
attribute enrichment, and configurable sampling. Changes span
`src/Hosting/`, `src/Middleware/`, `src/Mvc/Mvc.Core/`,
`src/SignalR/`, `src/Http/Http.Extensions/`, and
`src/HttpClientFactory/`. Also add a new CI workflow in
`.github/workflows/` for tracing integration tests that spins up a
Jaeger container, update `eng/Dependencies.props` with the
OpenTelemetry SDK package references, and add a `docs/Tracing.md`
guide on configuring distributed tracing for ASP.NET Core applications.

### W5: Add keep-alive, retry, and connection lifecycle management to ServerSentEvents

The `ServerSentEventsResult<T>` in `src/Http/Http.Results/src/ServerSentEventsResult.cs`
provides basic SSE responses but lacks production-ready lifecycle
features: automatic keep-alive heartbeats (periodic `:` comment lines
to prevent proxy/firewall disconnection), configurable reconnection
retry interval (the SSE `retry:` field), last-event-ID echo-back for
resumption on reconnect, and a server-side push abstraction for
out-of-band event emission. Add a `ServerSentEventsOptions` class with
`KeepAliveInterval`, `ReconnectInterval`, and `DefaultEventType`
properties; update `ServerSentEventsResult<T>` to honour these options
and inject keep-alive comments; add a `ChannelBackedSseResult<T>` that
wraps a `Channel<SseItem<T>>` for push semantics; add an
`ISseConnectionManager` DI service backed by a `ConcurrentDictionary`
for managing active connections by group name; and integrate keep-alive
timer logic with `IHostedService` for background heartbeat emission.
Changes span `src/Http/Http.Results/` (new result types and options),
`src/Http/Http.Extensions/` (DI registration extensions),
`src/Http/Http/src/` (response flush feature interface),
`src/Hosting/` (IHostedService integration),
`src/Http/Routing/src/` (endpoint metadata), and
`src/DefaultBuilder/` (DI wiring).

### W6: Add GraphQL endpoint support alongside REST

Implement a GraphQL middleware that integrates with the ASP.NET Core
pipeline: schema-first and code-first definition, query/mutation/
subscription support, DataLoader for N+1 prevention, persisted
queries, complexity analysis, and authentication/authorization
integration. Changes span `src/Http/Routing/`, `src/Http/Http.Extensions/`,
a new `src/GraphQL/` project, `src/Middleware/`, `src/Identity/`
(auth integration), and `src/OpenApi/` (parallel documentation).

### W7: Implement auto-scaling feedback loop with load metrics

Add a metrics emission system that publishes load indicators
(request queue depth, response latency percentiles, thread pool
saturation, memory pressure) in a format consumable by orchestrators
(Kubernetes HPA, Azure Autoscale). Include a health check provider
that returns `503 Service Unavailable` under load pressure and
graceful drain support during shutdown. Changes span `src/Hosting/`,
`src/Middleware/Diagnostics/`, `src/Middleware/HealthChecks/`,
`src/Http/Http/`, and a new scaling advisory project.

### W8: Add comprehensive API testing framework

Implement a testing SDK extending `TestHost`: API contract testing
(snapshot comparison of OpenAPI documents), load testing with
configurable concurrency, mutation testing for security
(authentication bypass, injection), and integration test fixtures
with database seeding. Include a test runner that generates
HTML reports. Changes span `src/Hosting/TestHost/`, `src/OpenApi/`,
a new `src/Testing/` project, `src/Mvc/Mvc.Core/`, and CI
integration.

### W9: Implement plugin-based middleware pipeline

Add a plugin system where middleware can be packaged as NuGet
packages and auto-discovered at startup. Support plugin ordering
via `[Before]`/`[After]` attributes, plugin configuration through
`appsettings.json`, versioned plugin interfaces, and a plugin
health dashboard. Changes span `src/Hosting/`, `src/Http/Http/`,
`src/Middleware/`, a new `src/Plugins/` project, DI integration,
and the `WebApplicationBuilder`.

### W10: Integrate Blazor SSR component rendering with output caching infrastructure

Blazor's server-side rendering in `src/Components/Endpoints/src/`
re-renders static Blazor components on every request even when their
output depends only on stable route parameters or public data with no
per-user personalization. The existing output caching infrastructure
in `src/Middleware/OutputCaching/src/` is not wired to Blazor component
rendering. Integrate the two: add a `[BlazorOutputCache]` attribute in
`src/Components/Web/src/` for marking static components as cacheable;
implement a `BlazorComponentOutputCachePolicy` in
`src/Middleware/OutputCaching/src/` that generates cache keys from
component type, route data, and query parameters and delegates storage
to the registered `IOutputCacheStore`; add bypass logic in
`EndpointHtmlRenderer` (`src/Components/Endpoints/src/Rendering/`)
to skip re-rendering and serve cached HTML when a warm cache entry
exists; add a `BlazorOutputCacheTagHelper` in
`src/Mvc/Mvc.TagHelpers/src/` for applying the policy from Razor
views; and add DI registration in `src/DefaultBuilder/src/`. Changes
span `src/Components/Endpoints/src/` (renderer integration),
`src/Components/Web/src/` (attribute),
`src/Middleware/OutputCaching/src/` (new policy),
`src/Mvc/Mvc.TagHelpers/src/` (tag helper),
`src/DefaultBuilder/src/` (DI wiring), and
`src/Hosting/` (configuration options).

### W11: Consolidate build infrastructure and documentation across all projects

The build system spans `Directory.Build.props`,
`Directory.Build.targets`, `Directory.Build.BeforeCommonTargets.targets`,
`eng/Versions.props`, `eng/Version.Details.xml`, `eng/Dependencies.props`,
and `eng/SharedFramework.External.props`. Several properties are
duplicated across these files. Consolidate shared MSBuild properties
into `eng/Common.props`, deduplicate version pins between
`eng/Versions.props` and `eng/Version.Details.xml`, and update
`docs/ProjectProperties.md` to document the canonical location for each
build property. Also update `CONTRIBUTING.md` with a build quickstart
section referencing `global.json` SDK requirements and `restore.sh`,
add a `docs/BuildSystem.md` architectural guide explaining the
relationship between `eng/` targets files and `Directory.Build.props`,
and update the `AspNetCore.slnx` solution file to include a
`Solution Items` folder containing all build infrastructure files for
IDE discoverability.
