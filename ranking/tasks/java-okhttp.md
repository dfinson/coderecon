# Tasks — square/okhttp

8 tasks (3 narrow, 3 medium, 2 wide) for the Java/Kotlin HTTP client.

## Narrow

### N1: Fix connection pool eviction race under high concurrency

Under high concurrency, idle connections are sometimes evicted from the
pool while another thread is in the process of acquiring them. This
causes a `ConnectionShutdownException` on the acquiring thread. Fix the
eviction logic to check for in-flight acquisitions before closing
idle connections.

### N2: Add `DNS-over-HTTPS` cache TTL support

The built-in `DnsOverHttps` client does not cache DNS responses. Each
request performs a fresh DoH lookup even when the previous response
included a TTL. Implement a response cache in the DoH client that
respects the DNS record TTL from the response. Add a configuration
option for maximum cache size.

### N3: Fix `EventListener.callEnd` not called on cancelled calls

When a `Call` is cancelled via `call.cancel()`, the `EventListener`
receives `callFailed` but not `callEnd`. The contract says `callEnd`
is always the last event. Fix the cancellation path to emit `callEnd`
after `callFailed` when the call is cancelled.

## Medium

### M1: Implement automatic retry with exponential backoff

Add configurable automatic retry for failed requests. Support
retry on connection failures, 5xx responses, and configurable
status codes. Use exponential backoff with jitter. Add a
`RetryPolicy` interface with a default implementation, configurable
via `OkHttpClient.Builder.retryPolicy()`. Respect `Retry-After`
headers. Track retry count in the response for observability.

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

## Wide

### W1: Add comprehensive request/response logging interceptor

Implement a new `HttpLoggingInterceptor` with structured logging
output. Support logging levels: basic (method, URL, status, duration),
headers (basic + request/response headers with sensitive header
masking), body (headers + request/response body with size limits and
content type filtering). Add format options: text, JSON, and custom
formatters. Log multipart request bodies with per-part metadata.
Support async logging to avoid blocking the request thread.

### W2: Implement connection health monitoring dashboard

Add an internal monitoring system that tracks: connection pool
utilization (active/idle/total per host), connection lifetimes, TLS
handshake durations, DNS resolution times, request durations
(percentiles), retry rates, and cache hit rates. Expose metrics
through a `MetricsListener` interface and provide a JMX MBean
implementation. Add a diagnostic `dump()` method that produces a
human-readable report of all connection pool state.
