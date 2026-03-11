# square/okhttp

| Field | Value |
|-------|-------|
| **URL** | https://github.com/square/okhttp |
| **License** | Apache-2.0 |
| **Language** | Java (Kotlin) |
| **Scale** | Medium (multi-module project) |
| **Category** | HTTP client |
| **Set** | ranker-gate |
| **Commit** | `e2061bdd146ae3ce5f097b78fa7a731dc7638510` |

## Why this repo

- **Multi-module with clear boundaries**: Connection pooling, HTTP/2 framing,
  interceptor chain, TLS/certificate pinning, cache, cookie handling, DNS
  resolver, WebSocket support — each a distinct subsystem requiring navigation
  between them.
- **Well-structured**: Core library under `okhttp/` with clear package split.
  Interceptors, connection management, and protocol handling are cleanly
  separated. Supplementary modules (logging, TLS, mock server) in separate
  directories.
- **Rich history**: 6K+ commits, maintained by Square with strong review
  culture. Migration from Java to Kotlin provides variety in commit patterns.
- **Permissive**: Apache-2.0.

## Structure overview

```
okhttp/src/commonJvmAndroid/kotlin/okhttp3/
├── OkHttpClient.kt       # Main client entry point
├── Call.kt                # HTTP call abstraction
├── Request.kt / Response.kt  # Request/response models
├── internal/
│   ├── connection/        # Connection pool, route selection
│   │   ├── RealConnectionPool.kt
│   │   ├── RouteSelector.kt
│   │   └── Exchange.kt
│   ├── http/              # HTTP protocol handling
│   │   ├── RealInterceptorChain.kt
│   │   ├── RetryAndFollowUpInterceptor.kt
│   │   └── CallServerInterceptor.kt
│   ├── http2/             # HTTP/2 framing
│   ├── cache/             # Disk cache
│   ├── tls/               # TLS, certificate pinning
│   ├── ws/                # WebSocket implementation
│   └── platform/          # Platform-specific (JDK, Android)
├── Interceptor.kt         # Interceptor interface
├── Cache.kt               # Cache API
└── CookieJar.kt           # Cookie handling
```

## Scale indicators

- ~200 Kotlin/Java source files
- ~50K lines of code
- Clear subsystem boundaries with interceptor-chain architecture
- 3-4 levels of package nesting

## Notes

- Primarily Kotlin since OkHttp 4.x, with some Java interop code remaining.
  Codeplane indexes Kotlin as JavaScript family — verify grammar support.
  If Kotlin indexing is insufficient, substitute with `square/retrofit`
  (Apache-2.0, Java-primary) or `apache/commons-lang` (Apache-2.0).

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add per-host connection count API to `ConnectionPool`

`ConnectionPool` exposes `idleConnectionCount()` and `connectionCount()`
for aggregate pool statistics, but provides no way to query the number
of connections to a specific host. Applications that implement
per-host throttling or observability have no programmatic way to inspect
per-host connection usage. Add a `connectionCount(url: HttpUrl): Int`
method to `RealConnectionPool` that iterates `connections` and counts
entries whose `route().address.url.host` matches the given host, and
expose it through the public `ConnectionPool` API with the same
signature. Also add a corresponding `idleConnectionCount(url: HttpUrl)`
overload that counts only idle (no active calls) connections to the
host.

### N2: Add `DNS-over-HTTPS` cache TTL support

The built-in `DnsOverHttps` client does not cache DNS responses. Each
request performs a fresh DoH lookup even when the previous response
included a TTL. Implement a response cache in the DoH client that
respects the DNS record TTL from the response. Add a configuration
option for maximum cache size.

### N3: Distinguish cancellation failures in `EventListener.callFailed`

When a call is explicitly cancelled via `call.cancel()` and subsequently
fails, `EventListener.callFailed` is invoked with a plain
`IOException("Canceled")` that is identical to any other I/O error.
Applications using `EventListener` cannot programmatically tell whether
a failure was due to cancellation or a genuine network problem. Fix
`RealCall` so that when a call fails because `isCanceled()` is true, the
exception passed to `callFailed` is a dedicated `CallCancelledException`
(extending `IOException`) with a message identifying the explicit
cancellation. Add an entry to `CHANGELOG.md` under the next unreleased
version documenting the new exception type.

### N4: Log `CertificatePinner` failures during HTTP/2 connection coalescing

When `RealConnection.isEligible()` performs a certificate-pin check for
HTTP/2 connection coalescing, any `SSLPeerUnverifiedException` thrown by
`CertificatePinner.check()` is silently caught and the coalescing attempt
is rejected with no diagnostic information:
`} catch (_: SSLPeerUnverifiedException) { return false }`. Developers
have no way to distinguish a coalescing rejection due to pin mismatch from
one due to IP-address mismatch or protocol mismatch. Fix `isEligible()` to
log the pin verification failure message via `Platform.get().log()` before
returning false, and add a unit test that verifies the log output when
coalescing is rejected due to a pin mismatch.

### N5: Correct `Content-Length` mismatch on retried POST requests

When a POST request with a fixed-length `RequestBody` is retried after
a connection failure, the `Content-Length` header from the original
attempt is reused even if the body source has been partially consumed.
Fix the retry path to re-read the body and recompute the content length
before sending the retried request.

### N6: Fix `RouteSelector` failing to skip failed routes on proxy fallback

When a proxy connection fails and the `RouteSelector` falls back to
the next proxy in the list, it does not record the failed route. This
causes the same broken route to be retried on subsequent requests. Fix
the route failure tracking so that failed proxy routes are properly
excluded during proxy fallback selection.

### N7: Implement `Cache-Control: stale-if-error` in `CacheStrategy` and `CacheInterceptor`

The `Cache-Control: stale-if-error` directive (RFC 5861) is not
implemented. When a network request fails with a server error (5xx) or
a connection error, and the cached response is within the
`stale-if-error` window, OkHttp should serve the stale cached response
instead of surfacing the error to the caller. Add a
`staleIfErrorSeconds: Int` property to `CacheControl` (defaulting to
`-1` for absent), update the `Cache-Control` parser in
`-CacheControlCommon.kt` to populate it, and update `CacheInterceptor`
to check the directive when the network response is a 5xx or an
`IOException` and the directive window covers the response age.

### N8: Fix stale `Exchange` reference after `CallServerInterceptor` timeout

When a request times out inside the `CallServerInterceptor`, the
associated `Exchange` object retains a reference to the now-dead
connection. Subsequent attempts to read the response body throw a
misleading `IllegalStateException` instead of a timeout exception.
Fix the exchange lifecycle so the connection is properly detached
on timeout.

### N9: Honor `Cache-Control: immutable` in `CacheStrategy`

`CacheControl` parses and exposes the `immutable` boolean property
(RFC 8246) but `CacheStrategy` never reads it. When a cached response
carries `Cache-Control: immutable`, it should be treated as perpetually
fresh for the duration of its `max-age`; no conditional request
(`If-None-Match` / `If-Modified-Since`) should be generated even after
the freshness lifetime expires. Currently such responses are still
revalidated with conditional requests on every request after max-age.
Fix `CacheStrategy.Companion.compute()` to check
`cacheResponse.cacheControl.immutable` and, when true and the response
is still within its freshness window, return the cached response
directly without building a conditional network request.

### N10: Fix `WebSocket` ping/pong frame ordering under back-pressure

When the write buffer is full and the WebSocket writer is blocked,
ping frames are queued behind data frames. If the pong deadline
expires before the queued ping is sent, the connection is incorrectly
closed as unresponsive. Fix the frame writer to prioritize ping/pong
control frames over queued data frames.

## Medium

### M1: Implement automatic retry with exponential backoff

Add configurable automatic retry for failed requests. Support
retry on connection failures, 5xx responses, and configurable
status codes. Use exponential backoff with jitter. Add a
`RetryPolicy` interface with a default implementation, configurable
via `OkHttpClient.Builder.retryPolicy()`. Respect `Retry-After`
headers. Track retry count in the response for observability. Also update `README.md` to document the new retry API in the "Features" overview, and add a recipe to `docs/recipes.md` showing how to configure retry policies with examples for idempotent and non-idempotent requests.

### M2: Add HTTP/3 (QUIC) transport support

Implement an HTTP/3 transport using QUIC. Add connection migration
support (handling network changes without reconnection), 0-RTT
connection resumption, and Alt-Svc header parsing for HTTP/3
discovery. The transport should be selectable via the client builder
and fall back to HTTP/2 when QUIC is unavailable.

### M3: Implement response body decompression pipeline

Currently OkHttp handles gzip decompression as a special case in
the `BridgeInterceptor`. Refactor to support a pluggable decompression
pipeline: gzip, deflate, brotli, and zstd. Each decompressor should
implement a common interface. Content-Encoding negotiation should
advertise all available decompressors in the Accept-Encoding header.
Add content verification (checksum) after decompression.

### M4: Add mutual TLS (client certificate) rotation without restart

OkHttp currently requires rebuilding the `OkHttpClient` to change
client certificates used for mutual TLS. Implement a `ClientCertificateSelector`
interface that is consulted on each TLS handshake, allowing the
application to supply different client certificates per host or rotate
certificates at runtime. Integrate with the `HandshakeCertificates`
builder and ensure that the connection pool evicts connections whose
client certificate no longer matches the selector's current choice.

### M5: Implement `Cache-Control: stale-while-revalidate` with background refresh

The `Cache-Control: stale-while-revalidate` directive (RFC 5861) is
not implemented. When a cached response is stale but falls within the
`stale-while-revalidate` window, OkHttp should return the stale
response immediately and concurrently dispatch a background request
to refresh the cache entry for future calls. Add a
`staleWhileRevalidateSeconds: Int` property to `CacheControl` and its
parser in `-CacheControlCommon.kt`. Update `CacheStrategy` to detect
the stale-while-revalidate window and mark the strategy accordingly.
Update `CacheInterceptor` to return the stale cached response to the
caller while enqueuing an asynchronous revalidation via the
`OkHttpClient`'s `Dispatcher`. Add a
`cacheStaleWhileRevalidate(call: Call, cachedResponse: Response)` event
to `EventListener` that fires when a stale response is served with a
background refresh in flight. Ensure the background refresh updates the
cache entry and does not surface errors to the original caller.

### M6: Add per-host connection pool configuration

The connection pool currently applies a single idle connection limit
and keep-alive duration to all hosts. Add per-host pool configuration
via `ConnectionPool.Builder` that allows different limits for specific
hosts — for example, more idle connections for high-traffic API
endpoints and fewer for low-traffic ones. Include support for host
pattern matching (exact and wildcard) and a default fallback policy.
Ensure eviction respects per-host limits independently.

### M7: Implement transparent request queuing with priority support

Add a request queue that limits the number of concurrent in-flight
requests per host and globally. Excess requests should be queued and
dispatched as capacity becomes available. Support priority levels
(high, normal, low) so that important requests skip ahead in the queue.
Integrate with the `Dispatcher` and expose queue depth and wait time
through the `EventListener`. Queued calls must be cancellable.

### M8: Add network interceptor for automatic OAuth token refresh

Implement an `Authenticator`-adjacent mechanism that detects expired
OAuth tokens from 401 responses, acquires a new access token using a
refresh token, and retries the original request with the updated
`Authorization` header. Serialize concurrent token refreshes so only
one refresh is in flight at a time while other 401-receiving calls
wait. Expose token lifecycle events through the `EventListener` and
limit the number of refresh retries to prevent loops.

### M9: Implement connection preconnect and speculative warming

Add a `preconnect(url, count)` API to `OkHttpClient` that proactively
opens TCP connections and performs TLS handshakes to a host before any
request is made. Support DNS pre-resolution as an option. The
preconnected sockets should be placed in the connection pool and used
by subsequent requests. Integrate with the `EventListener` to report
preconnect outcomes and ensure that preconnected connections respect
the pool's idle timeout and eviction policies.

### M10: Add response caching for authenticated requests via `Cache-Control` extensions

By default the cache does not store responses to requests that carry an
`Authorization` header. Implement support for the `Cache-Control`
response directives `must-revalidate`, `public`, and `s-maxage` that
explicitly allow caching of authenticated responses. Add a builder
option to opt in to this behavior. Ensure cached authenticated
responses are properly revalidated and that the cache key includes
enough request metadata to prevent serving one user's cached response
to another.

## Wide

### W1: Enhance `HttpLoggingInterceptor` with structured output and async logging

The existing `HttpLoggingInterceptor` in `okhttp-logging-interceptor/`
logs plain-text request/response information with four levels
(`NONE`, `BASIC`, `HEADERS`, `BODY`) but lacks several capabilities
needed for production observability. Extend it as follows. Add a
`Logger` interface variant that accepts structured key-value maps so
JSON log sinks can be plugged in without string parsing, and provide
a `JsonLogger` implementation that emits one JSON object per
request/response event. Add a `redactHeader(name: String)` API that
replaces the value of sensitive headers (e.g., `Authorization`,
`Cookie`) with `██` in log output; the existing code logs all header
values verbatim. Add a `maxBodySize(bytes: Long)` limit that truncates
logged body content and appends `"... (truncated)"` rather than
buffering unlimited response bodies. Add a `contentTypeFilter` that
skips body logging for non-text content types such as `image/*` and
`application/octet-stream`. Support async logging via an
`AsyncLogger` wrapper that offloads log writes to a dedicated thread
so the HTTP call thread is not blocked by slow log sinks. Update
`LoggingEventListener` in the same module to surface the new
structured fields. Update the module's `README.md` to document each
new option with examples.

### W2: Implement connection health monitoring dashboard

Add an internal monitoring system that tracks: connection pool
utilization (active/idle/total per host), connection lifetimes, TLS
handshake durations, DNS resolution times, request durations
(percentiles), retry rates, and cache hit rates. Expose metrics
through a `MetricsListener` interface and provide a JMX MBean
implementation. Add a diagnostic `dump()` method that produces a
human-readable report of all connection pool state.

### W3: Refactor interceptor chain to support async non-blocking execution

The current interceptor chain is synchronous and blocks a thread for
the entire duration of a request. Redesign the chain to support
non-blocking, asynchronous interceptors that yield on I/O operations.
Each interceptor should be able to return a deferred result. The
synchronous API should remain backward-compatible by wrapping the
async chain. Update all built-in interceptors — retry, bridge,
cache, connect, and call-server — to use the async path. Ensure
the `EventListener` callbacks are dispatched on a consistent thread.

### W4: Implement configurable circuit breaker per host

Add a circuit breaker pattern that tracks failures per host and
temporarily stops sending requests to hosts that are consistently
failing. Implement the three-state model: closed (normal), open
(blocking requests and returning an immediate failure), and half-open
(allowing a probe request to test recovery). Make failure thresholds,
open duration, and half-open probe count configurable per host via
the client builder. Integrate with the `EventListener` for state
transitions. Failing-open should still allow callers to opt out
via a request tag.

### W5: Add full HTTP/2 server push support

Implement client-side handling for HTTP/2 server push. When the
server sends a `PUSH_PROMISE` frame, validate the promised request
against a configurable policy (same-origin check, allowed methods,
header filters). Store accepted pushed responses in the cache for
subsequent matching requests. Add a `PushPromiseHandler` callback
interface so applications can accept, reject, or modify pushed
responses. Handle cancellation of push streams, RST_STREAM
propagation, and flow-control accounting for pushed data frames.

### W6: Implement transparent cookie persistence and policy engine

Replace the stateless `CookieJar` interface with a full cookie
management subsystem. Implement persistent cookie storage backed by
a file-based store with encryption at rest. Enforce RFC 6265
semantics: domain and path scoping, Secure and HttpOnly flags,
SameSite attribute, maximum cookie limits per domain and total.
Add a `CookiePolicy` that allows applications to accept, reject,
or modify cookies per domain. Provide migration from the existing
`CookieJar` and ensure thread safety for concurrent request paths.

### W7: Build a diagnostic proxy mode for request/response inspection

Add an embeddable diagnostic proxy to OkHttp that sits between the
application and the network. It should capture full request and
response details, including headers, bodies, TLS handshake
information, timing breakdowns (DNS, connect, TLS, request send,
response receive), and connection reuse metadata. Provide a
programmatic API for registering inspection callbacks, filtering
captured traffic by host or URL pattern, and exporting captured
sessions as HAR files. Ensure the proxy has minimal performance
overhead when no inspector is registered.

### W8: Implement graceful connection draining and shutdown protocol

Add a coordinated shutdown mechanism for the connection pool. When
shutdown is initiated, stop acquiring new connections, send HTTP/2
GOAWAY frames on active HTTP/2 connections, allow in-flight requests
to finish within a configurable grace period, and forcefully close
remaining connections after the deadline. Track shutdown progress
through the `EventListener`. Add a `shutdownNow()` variant that
immediately cancels all in-flight calls. Ensure WebSocket connections
receive a close frame before termination. Integrate with the
`Dispatcher` to reject new calls during shutdown.

### W9: Add end-to-end request tracing with distributed trace propagation

Implement distributed tracing support that assigns a unique trace
and span ID to each request and propagates them via W3C Trace Context
headers. Capture spans for each phase of the request lifecycle: DNS
resolution, TCP connect, TLS handshake, request write, response
headers read, and response body read. Model retries and redirects as
child spans. Provide a `TraceExporter` interface with implementations
for logging and OpenTelemetry-compatible output. Integrate with the
`EventListener` so that existing listener consumers can correlate
events to trace spans.

### W10: Implement a response body streaming transformation pipeline

Add a `BodyTransformer` interface that allows chaining transformations
on the response body as it streams. Support built-in transformers
for decompression, decryption, byte-range reassembly, and content
validation (checksum/signature verification). Transformers should
compose as a pipeline where each stage receives a streaming source
from the previous stage. Integrate with the cache so that
transformed bodies can optionally be stored post-transformation.
Expose per-stage metrics (bytes processed, time spent) through the
`EventListener`.

### N11: Fix broken `changelogs/changelog.md` reference and missing `releasing.md` in `mkdocs.yml`

The `mkdocs.yml` navigation includes `'Change Log': changelogs/changelog.md`
but `docs/changelogs/changelog.md` does not exist — the 5.x change log
lives at the repo root as `CHANGELOG.md`. The nav entry will produce a
broken link on the documentation site. Additionally, `docs/releasing.md`
exists but is absent from the `nav:` section entirely, making it
unreachable from the site sidebar. Fix both issues: create
`docs/changelogs/changelog.md` that mirrors the 5.x content from the
root `CHANGELOG.md` (or uses MkDocs `!include` / symlink as appropriate),
and add `'Releasing': releasing.md` as an entry under the Contributing
group in `mkdocs.yml`.

### M11: Expand contributor build documentation and add a dedicated build guide page

The current `CONTRIBUTING.md` covers CLA requirements, basic
`./gradlew clean check`, and Android test setup, but does not explain
how to build and test individual modules in the multi-module project.
`docs/contribute/contributing.md` (the rendered docs version) has the
same gaps. Add a "Module Build Guide" section to both files documenting
how to run checks for specific modules (e.g., `./gradlew :okhttp:check`,
`./gradlew :mockwebserver-junit5:check`, `./gradlew :okhttp-tls:check`).
Add a "Gradle Properties Reference" section explaining each flag in
`gradle.properties` (`androidBuild`, `graalBuild`, `loomBuild`,
`containerTests`, `okhttpModuleTests`, `okhttpDokka`) with descriptions
of what they enable and when contributors should set them. Also document
how to enable the Gradle configuration cache and troubleshoot common
serialization issues with `org.gradle.configuration-cache.problems=fail`.
Create a new `docs/contribute/build-guide.md` page with the full
Module Build Guide and Gradle Properties Reference content (linking to
it from both `CONTRIBUTING.md` and `docs/contribute/contributing.md`),
and add the new page to `mkdocs.yml` under the Contributing navigation
group.

### W11: Create a unified release engineering guide and update `mkdocs.yml` navigation

Release-related documentation is scattered across `docs/releasing.md` (release steps), `CHANGELOG.md` (version history with 800+ lines), `CONTRIBUTING.md` (committer's guides), and `build.gradle.kts` (publishing plugins: `maven-publish`, `dokka`, `binary-compatibility-validator`). Create a comprehensive `docs/release-engineering.md` that consolidates the full release lifecycle: version numbering conventions (matching the `CHANGELOG.md` format), changelog entry formatting rules, the Gradle publishing pipeline configuration, API binary compatibility validation using the `binary-compatibility-validator` plugin, MkDocs documentation site deployment via `deploy_website.sh`, and post-release verification steps. Update `mkdocs.yml` to add the new page to the navigation structure. Add a cross-reference from `CONTRIBUTING.md`'s "Committer's Guides" section to the new release engineering document.
