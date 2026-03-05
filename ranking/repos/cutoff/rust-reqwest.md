# seanmonstar/reqwest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/seanmonstar/reqwest |
| **License** | MIT OR Apache-2.0 |
| **Language** | Rust |
| **Scale** | Large |
| **Category** | HTTP client |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Clear separation between async and blocking APIs,
  with dedicated modules for connection management, proxying, TLS,
  redirects, cookies, multipart uploads, and response decoding. Built
  on `hyper` + `tokio` with optional HTTP/3 (h3) support.
- **Rich history**: 9K+ stars, 2K+ commits. The most popular Rust HTTP
  client, used by thousands of crates and applications. PRs cover TLS
  backends, proxy authentication, connection pooling, streaming bodies,
  and cookie handling.
- **Permissive**: Dual-licensed MIT OR Apache-2.0.

## Structure overview

```
reqwest/
└── src/
    ├── lib.rs                 # Re-exports, top-level Client and get() convenience API
    ├── async_impl/
    │   ├── client.rs          # Async Client — builder pattern, connection pooling, middleware
    │   ├── request.rs         # Async Request — method, URL, headers, body construction
    │   ├── response.rs        # Async Response — status, headers, body streaming, json/text/bytes
    │   ├── body.rs            # Async Body — from bytes, stream, file, with content-length
    │   ├── decoder.rs         # Response body decoder — gzip, brotli, deflate, zstd decompression
    │   ├── multipart.rs       # Multipart form data — Part, Form, streaming encoder
    │   ├── h3_client.rs       # HTTP/3 client implementation via quinn
    │   └── upgrade.rs         # Connection upgrade support (WebSocket handshake)
    ├── blocking/
    │   ├── client.rs          # Blocking Client — sync wrapper spawning a tokio runtime
    │   ├── request.rs         # Blocking Request — sync API mirroring async_impl
    │   ├── response.rs        # Blocking Response — sync body reading, copy_to, json
    │   ├── body.rs            # Blocking Body — from Read, bytes, with content-length
    │   └── multipart.rs       # Blocking multipart — sync Part and Form builders
    ├── connect.rs             # Connection management — Connector, HttpsConnector, connection pooling
    ├── proxy.rs               # Proxy support — HTTP/HTTPS/SOCKS5 proxies, no_proxy, authentication
    ├── redirect.rs            # Redirect policy — follow, limited, none, custom policy
    ├── tls.rs                 # TLS configuration — native-tls and rustls backends, certificates
    ├── dns/
    │   ├── resolve.rs         # DNS resolution trait and default resolver
    │   └── gai.rs             # getaddrinfo-based resolver
    │   └── hickory.rs         # Hickory (trust-dns) async resolver
    ├── cookie.rs              # Cookie jar — cookie storage and automatic header management
    ├── error.rs               # Error type — URL, connect, timeout, status, decode errors
    ├── into_url.rs            # IntoUrl trait — string/Url conversion with validation
    ├── util.rs                # Internal utilities
    └── wasm/
        ├── client.rs          # WASM Client — fetch API backend for browser/worker targets
        ├── request.rs         # WASM Request builder
        ├── response.rs        # WASM Response wrapper
        ├── body.rs            # WASM Body — JsValue conversion
        └── multipart.rs       # WASM multipart support
```

## Scale indicators

- ~35 Rust source files
- ~15K lines of code
- Single crate with feature-gated modules (blocking, cookies, multipart, gzip, brotli, h3, rustls, native-tls)
- Key dependencies: hyper, tokio, http, url, h2, quinn (h3), native-tls/rustls

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Response::text() not respecting charset from Content-Type header

When a response has `Content-Type: text/html; charset=iso-8859-1`,
`response.text()` in `async_impl/response.rs` always decodes as UTF-8
instead of using the declared charset. Fix the text decoding to parse
the `charset` parameter from the `Content-Type` header and use the
`encoding_rs` crate for non-UTF-8 encodings.

### N2: Fix blocking Client not forwarding timeout to connect phase

When `ClientBuilder::timeout(Duration::from_secs(5))` is set on the
blocking client, the timeout applies only to the overall request but
not to the TCP connect phase. A slow DNS + connect can exceed the
intended timeout. Fix `blocking/client.rs` to propagate the timeout
to the `connect_timeout` if no explicit connect timeout is set.

### N3: Fix proxy authentication not URL-encoding special characters in credentials

When proxy credentials contain special characters like `@` or `:` in
the password (e.g., `http://user:p@ss:word@proxy:8080`), the URL parser
in `proxy.rs` splits incorrectly at the embedded `@`. Fix the proxy
URL parsing to properly handle URL-encoded credentials and decode
them before sending the `Proxy-Authorization` header.

### N4: Fix gzip decoder not handling concatenated gzip streams

When a server sends a response with multiple concatenated gzip members
(valid per RFC 1952), the decoder in `async_impl/decoder.rs` stops
after the first member and truncates the response body. Fix the gzip
decoder to continue decompressing subsequent members until EOF.

### N5: Fix redirect policy not preserving fragment from original URL

When a request to `http://example.com/page#section` receives a 301
redirect to `/new-page`, the fragment `#section` is dropped from the
final URL. Per RFC 7231 §7.1.2, the fragment should be inherited if
the redirect target has no fragment. Fix `redirect.rs` to preserve
the original fragment when the redirect URL lacks one.

### N6: Fix cookie jar not handling domain cookies starting with a dot

When a server sets `Set-Cookie: sid=abc; Domain=.example.com`, the
cookie jar in `cookie.rs` stores it with the leading dot, but matching
logic fails to match requests to `sub.example.com` because it compares
`.example.com` != `sub.example.com` without stripping the leading dot
per RFC 6265 §5.2.3. Fix domain matching in the cookie store.

### N7: Fix multipart form not setting Content-Length when all parts are known-size

When all parts of a multipart form have known sizes (bytes or string),
`multipart.rs` in `async_impl/` still sends with `Transfer-Encoding: chunked`
instead of computing and setting `Content-Length`. Fix the form builder
to calculate total content length when all parts support it, falling
back to chunked only for stream parts.

### N8: Fix error Display impl not including the URL for timeout errors

When a request times out, the error message from `error.rs` says
"request timeout" without including the URL that timed out, making
debugging difficult when multiple requests are in flight. Fix the
`Display` implementation of `Error` to include the URL for timeout
error variants.

### N9: Fix IntoUrl rejecting valid URLs with empty path segments

`IntoUrl` in `into_url.rs` rejects URLs like `http://example.com//api/v1`
(double slash) as invalid, even though this is a valid URL per RFC 3986.
Fix the URL validation to accept empty path segments.

### N10: Fix WASM client not sending custom headers set via RequestBuilder

When running in a WASM target, custom headers set via
`request.header("X-Custom", "value")` in `wasm/request.rs` are silently
dropped because the fetch API `Headers` object is constructed before
custom headers are applied. Fix the header construction order to apply
custom headers after default headers.

## Medium

### M1: Implement retry policy with exponential backoff

Add `ClientBuilder::retry_policy(RetryPolicy::exponential(3, Duration::from_millis(100)))`
that automatically retries failed requests on transient errors (5xx,
connection reset, timeout). Support configurable max retries, backoff
strategy, jitter, retry-after header respect, and idempotency checks.
Requires a retry middleware layer in `async_impl/client.rs`, a new
`retry.rs` module, integration with `blocking/client.rs`, error
classification in `error.rs`, and request body cloning/rewindable
body support.

### M2: Add request and response interceptors/middleware

Implement a middleware system: `ClientBuilder::with_middleware(mw)` where
middleware can inspect/modify requests before sending and responses after
receiving. Support ordering, short-circuiting (for caching), async
middleware, and access to error cases. Requires a `Middleware` trait, a
middleware chain executor in `async_impl/client.rs`, integration with
the blocking client, error propagation, and changes to the request
pipeline.

### M3: Implement connection pool metrics and diagnostics

Add `Client::pool_status()` returning metrics: active connections,
idle connections, pending requests per host, connection age histogram,
and TLS session reuse rate. Support a callback for pool events (connect,
reuse, close, timeout). Requires instrumentation in `connect.rs`, a
metrics struct, pool-state tracking, integration with hyper's connection
pool, and exposure through both async and blocking clients.

### M4: Add HTTP/3 Alt-Svc automatic upgrade

Implement automatic protocol upgrade from HTTP/2 to HTTP/3 when a
server advertises `Alt-Svc: h3=":443"`. Cache Alt-Svc records per
origin, support max-age expiry, and fall back to HTTP/2 on QUIC failure.
Requires Alt-Svc header parsing, a cache module, integration with
`h3_client.rs`, connection selection logic in `connect.rs`, and
configuration knobs on `ClientBuilder`.

### M5: Implement streaming multipart upload with progress callbacks

Add progress tracking for multipart uploads:
`Form::progress(|bytes_sent, total| { ... })`. Support per-part progress,
overall progress, and upload speed calculation. Requires wrapping the
body stream in `async_impl/multipart.rs`, a progress callback trait,
integration with `Content-Length` computation, the blocking multipart
wrapper, and cancellation support via the callback return value.

### M6: Add certificate pinning with multiple pin strategies

Implement `ClientBuilder::pin_certificate(pin)` supporting: SHA-256
public key pins, certificate chain pins, and custom pin verification
callbacks. Support pin sets with backup pins, reporting violations
without blocking (report-only mode), and pin validation at connection
time. Requires changes to `tls.rs` for both native-tls and rustls
backends, a pin storage module, verification callbacks in `connect.rs`,
and error types for pin violations.

### M7: Implement response caching with HTTP cache semantics

Add an optional response cache following RFC 7234: respect
`Cache-Control`, `ETag`, `Last-Modified`, and `Vary` headers. Support
in-memory and on-disk cache backends, conditional requests with
`If-None-Match`/`If-Modified-Since`, cache invalidation, and a
`CacheControl` builder for programmatic cache directives. Requires a
cache module, integration with the request pipeline in
`async_impl/client.rs`, header parsing utilities, storage abstraction,
and blocking client integration.

### M8: Add SOCKS5 proxy with DNS resolution options

Implement full SOCKS5 proxy support with: remote DNS resolution
(hostname sent to proxy), local DNS resolution (IP sent to proxy),
username/password authentication, and SOCKS5h variant. Support chaining
SOCKS5 with HTTP proxies. Requires a SOCKS5 handshake implementation,
integration with `proxy.rs`, connection establishment in `connect.rs`,
TLS-over-SOCKS tunneling, and the `no_proxy` bypass logic.

### M9: Implement request body streaming from AsyncRead sources

Add `Body::from_reader(async_reader, content_length)` and
`Body::from_stream(stream)` for streaming request bodies from files,
pipes, or custom async readers without buffering the entire body in
memory. Support `Content-Length` when known, chunked transfer when
unknown, and progress callbacks. Requires changes to `async_impl/body.rs`,
integration with the request pipeline, the blocking body wrapper, and
multipart integration for file-part streaming.

### M10: Add DNS-over-HTTPS resolver backend

Implement a DoH resolver: `ClientBuilder::dns_resolver(DohResolver::new("https://dns.google/dns-query"))`.
Support RFC 8484 wire format, response caching with TTL, concurrent
A/AAAA queries, fallback to system resolver on failure, and bootstrap
resolution for the DoH server itself. Requires a new resolver in
`dns/`, integration with the resolver trait, HTTP client bootstrapping
to avoid circular dependency, and configuration on `ClientBuilder`.

## Wide

### W1: Implement a comprehensive tracing and diagnostics layer

Add OpenTelemetry-compatible instrumentation: trace spans for DNS
resolution, TCP connect, TLS handshake, request send, response headers,
and body streaming. Emit metrics for request duration histogram, bytes
sent/received, connection pool utilization, error rate by type, and
protocol version distribution. Support configurable exporters and
`tracing` crate integration. Changes span `connect.rs`, `async_impl/client.rs`,
`async_impl/response.rs`, `proxy.rs`, `tls.rs`, `dns/`, `error.rs`,
a new telemetry module, and the blocking client wrapper.

### W2: Implement HAR (HTTP Archive) recording and playback

Add `ClientBuilder::record_har(path)` that records all requests and
responses in HAR 1.2 format, including timing, headers, bodies,
cookies, and TLS details. Add `Client::from_har(path)` for test playback
that serves recorded responses for matching requests. Support request
matching by URL, method, headers, and body fuzzy matching. Changes span
`async_impl/client.rs`, `async_impl/request.rs`, `async_impl/response.rs`,
a new `har` module, cookie capture, timing infrastructure in `connect.rs`,
TLS metadata extraction, and blocking client integration.

### W3: Add WebSocket client support with full RFC 6455 compliance

Implement `Client::websocket(url)` returning a `WebSocket` connection
with: text/binary message sending and receiving, ping/pong handling,
close handshake, per-message compression (RFC 7692), connection upgrade
from an existing HTTP response, and automatic reconnection with backoff.
Changes span a new `websocket` module, upgrade handling in
`async_impl/upgrade.rs`, proxy tunneling for WSS, TLS configuration,
the blocking client for sync WebSocket, and `wasm/` for browser
WebSocket API.

### W4: Implement adaptive connection pooling with smart preconnect

Add intelligent connection management: preconnect to hosts based on
usage patterns, adaptive pool sizing per host, connection warm-up (TLS
handshake before first request), HTTP/2 stream multiplexing awareness,
idle connection health checks, and connection affinity for session
stickiness. Changes span `connect.rs`, `async_impl/client.rs`, a new
pool management module, DNS pre-resolution in `dns/`, TLS session
resumption in `tls.rs`, metrics collection, and the blocking client.

### W5: Add comprehensive request/response body transformation pipeline

Implement a body transformation pipeline:
`ClientBuilder::request_transform(compress_gzip).response_transform(decrypt_aes)`.
Support compression (gzip, brotli, zstd), encryption/decryption,
signing/verification, content-type transformation (JSON ↔ MessagePack),
and streaming transforms without buffering. Changes span
`async_impl/body.rs`, `async_impl/decoder.rs`, `async_impl/request.rs`,
a new transforms module, the multipart encoder, `blocking/body.rs`,
and the WASM body types.

### W6: Implement API client generation from OpenAPI specifications

Build a code-generation tool that reads an OpenAPI 3.x spec and
generates a type-safe reqwest-based API client with: strongly-typed
request/response structs, method-per-endpoint API, authentication
helpers, pagination support, rate limiting, and error mapping. Changes
span a new `reqwest-codegen` crate, template engine integration, type
mapping from JSON Schema, authentication module integration with
the existing client, generated test stubs, and CI integration for
spec-driven regeneration.

### W7: Add platform-native TLS backend abstraction with hot-swapping

Implement a unified TLS abstraction that supports runtime backend
selection between native-tls, rustls, and platform-specific APIs
(Security.framework, SChannel, OpenSSL). Support hot-swapping
certificates without client restart, per-host TLS configuration, client
certificate authentication with hardware token support, and TLS
debugging with key logging. Changes span `tls.rs`, `connect.rs`, a new
backend abstraction layer, certificate management utilities, platform
detection, feature flag reorganization, and integration tests per
backend.

### W8: Implement request coalescing and deduplication

Add request deduplication: when multiple concurrent requests target the
same URL+method+headers, coalesce them into a single network request
and fan out the response to all waiters. Support configurable key
extraction, TTL-based result caching, cache invalidation on
non-idempotent methods, and metrics for coalescing hit rate. Changes
span `async_impl/client.rs`, a new coalescing module, response cloning
in `async_impl/response.rs`, body buffering for fan-out, integration
with the caching layer, the blocking client, and error propagation
for coalesced failures.

### W9: Add comprehensive mock server for testing reqwest clients

Implement `reqwest_mock::MockServer` that runs an in-process HTTP
server for testing: register expected requests with matchers (URL, method,
headers, body), return configured responses, support request ordering
verification, simulate latency/errors/timeouts, stream response bodies,
and assert on unmatched requests. Changes span a new `reqwest-mock`
crate, a mock HTTP server built on hyper, request matching engine, a
builder API for expectations, TLS test certificate generation, integration
with the blocking client, and WASM-compatible test support.

### W10: Implement cross-platform HTTP client with unified WASM and native API

Unify the native and WASM clients behind a single type-erased
`UniversalClient` that provides identical API across platforms, with
platform-appropriate implementation. Support feature parity for: headers,
cookies, redirects, timeouts, JSON/form/multipart bodies, streaming
responses, and error types. Add compile-time platform detection and
runtime capability queries. Changes span `async_impl/`, `blocking/`,
`wasm/`, a new unified client module, conditional compilation
refactoring, trait-based abstraction for platform-specific behavior,
and a comprehensive cross-platform test suite.
