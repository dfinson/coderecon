# Tasks — rack/rack

8 tasks (3 narrow, 3 medium, 2 wide) for the Ruby HTTP server interface.

## Narrow

### N1: Fix `Rack::Utils.parse_nested_query` quadratic behavior on deep nesting

Deeply nested query parameters like `a[b][c][d][e][f]=1` cause quadratic
time complexity in `parse_nested_query` due to repeated hash traversal.
Add a configurable maximum nesting depth (default 32) and raise
`Rack::QueryParser::ParamsTooDeepError` when exceeded.

### N2: Add `SameSite=None` default for cross-site cookie detection

When `Rack::Response#set_cookie` does not specify a `SameSite` attribute,
browsers default to `Lax`, which breaks cross-site scenarios. Add
automatic `SameSite=None; Secure` for cookies set on cross-origin
requests (detected via the `Sec-Fetch-Site` header). Add a configuration
option to disable this behavior.

### N3: Fix `Rack::Static` not setting `Cache-Control` for `.woff2` files

The `Rack::Static` middleware sets appropriate `Cache-Control` headers
for common static file types but misses `.woff2` font files. Requests
for web fonts get no-cache behavior. Add `.woff2`, `.woff`, and `.ttf`
to the default static file content types with immutable cache headers.

## Medium

### M1: Implement request body streaming with Rack 3 compatible API

Add a streaming request body interface that supports reading the request
body in chunks without buffering the entire body in memory. Implement
`rack.input` as an `Enumerable`-like interface with `#each_chunk` that
yields fixed-size chunks. Support back-pressure by pausing reading when
the consumer is slow. Add a `content_length_limit` configuration to
reject oversized requests early.

### M2: Add structured logging middleware

Implement `Rack::StructuredLogger` middleware that produces JSON-formatted
request logs with fields: timestamp, method, path, status, duration_ms,
request_id, user_agent, client_ip, and response_size. Support log
enrichment via a callback that adds custom fields (user_id, tenant, etc.).
Add correlation with upstream request IDs from `X-Request-Id` headers.

### M3: Implement ETag generation with content-aware hashing

Replace the current `Rack::ETag` middleware (which hashes the entire
response body) with content-aware ETag generation. For HTML responses,
hash only the meaningful content (strip whitespace changes). For JSON
responses, hash the semantically-sorted content (key order independent).
For binary responses, use the current body hash. Add weak ETag support
for semantically-equivalent responses.

## Wide

### W1: Implement HTTP/2 support in Rack's interface

Extend the Rack specification to support HTTP/2 features: server push
(via a `rack.push_promise` lambda), stream prioritization, and
multiplexed responses. The `rack.response` should support a streaming
body that can send headers and data frames independently. Add a
compatibility layer so existing Rack 2/3 apps work unchanged on
HTTP/2 servers. Update the mock request/response for testing HTTP/2.

### W2: Add comprehensive security middleware suite

Implement a suite of security middleware as a cohesive package:
`Rack::ContentSecurityPolicy` (CSP headers with nonce generation),
`Rack::PermissionsPolicy` (Permissions-Policy headers),
`Rack::CORSPolicy` (with preflight caching),
`Rack::RateLimiter` (configurable rate limiting),
and `Rack::RequestSanitizer` (input sanitization with configurable
rules). Each middleware should be usable independently or as a bundle
via `Rack::SecureHeaders` that applies sensible defaults.
