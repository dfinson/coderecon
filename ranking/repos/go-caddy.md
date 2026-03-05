# caddyserver/caddy

| Field | Value |
|-------|-------|
| **URL** | https://github.com/caddyserver/caddy |
| **License** | Apache-2.0 |
| **Language** | Go |
| **Scale** | Medium (multi-module project) |
| **Category** | Web server with plugin architecture |

## Why this repo

- **Multi-module with clear boundaries**: HTTP server, TLS automation (ACME),
  reverse proxy, file server, Caddyfile config parser, JSON config API,
  logging, metrics — each a distinct subsystem requiring navigation between
  them for cross-cutting changes.
- **Well-structured**: Clean Go package layout. `modules/` contains pluggable
  components (caddyhttp, caddytls, caddylog). Core admin API and config
  loading are separate from module implementations.
- **Rich history**: 5K+ commits, regular major feature PRs (HTTP/3, ACME
  improvements, new modules). Strong code review culture.
- **Permissive**: Apache-2.0.

## Structure overview

```
.
├── caddy.go             # Core Caddy type, module registry
├── admin.go             # Admin API server
├── config.go            # Configuration loading
├── modules/
│   ├── caddyhttp/       # HTTP server, handlers, middleware
│   │   ├── server.go    # HTTP server implementation
│   │   ├── routes.go    # Route matching
│   │   ├── reverseproxy/ # Reverse proxy module
│   │   ├── fileserver/  # Static file serving
│   │   └── headers/     # Header manipulation
│   ├── caddytls/        # TLS automation, ACME client
│   ├── caddyevents/     # Event system
│   ├── caddypki/        # PKI management
│   └── logging/         # Structured logging
├── caddyconfig/         # Caddyfile parser and adapters
│   ├── caddyfile/       # Caddyfile syntax parser
│   └── httpcaddyfile/   # HTTP-specific Caddyfile directives
└── cmd/                 # CLI entry point
```

## Scale indicators

- ~200 Go source files
- ~60K lines of code
- Clear plugin/module boundaries
- 2-3 levels of package nesting

---

## Tasks

8 tasks (3 narrow, 3 medium, 2 wide) for the Go web server.

## Narrow

### N1: Fix Caddyfile `handle_path` not stripping prefix on error routes

When using `handle_path /api/*` with an error handler inside, the error
handler receives the original un-stripped path instead of the stripped
path. The path prefix is restored before the error handler runs. Fix
the error handling to preserve the stripped path context within
`handle_path` blocks.

### N2: Add `max_header_size` directive to HTTP server

The HTTP server does not expose a way to configure the maximum HTTP
header size. Large headers from corporate proxies or OAuth tokens can
exceed Go's default 1MB limit. Add a `max_header_size` Caddyfile
directive and JSON config option that sets `http.Server.MaxHeaderBytes`.

### N3: Fix reverse proxy health check not respecting `tls_server_name`

When a reverse proxy upstream uses TLS with a custom `tls_server_name`,
the active health check sends requests without the configured SNI value.
The health check TLS handshake uses the upstream's IP address as the
server name, causing certificate verification failures. Fix the health
checker to use the configured `tls_server_name`.

## Medium

### M1: Implement request rate limiting module

Add a `rate_limit` HTTP handler module that supports per-client (by IP)
and per-route token bucket rate limiting. Support configurable burst
size, refill rate, and distributed rate limiting via a shared storage
interface (with an embedded implementation for single-instance). Return
proper `429` responses with `Retry-After` headers. Add Caddyfile
shortcut syntax.

### M2: Add WebSocket reverse proxy improvements

Implement WebSocket-specific features in the reverse proxy module:
connection timeout (separate from HTTP timeout), ping/pong health
monitoring for idle connections, automatic reconnection on upstream
failure, and WebSocket compression (permessage-deflate) negotiation
passthrough. Add per-connection metrics to the Prometheus endpoint.

### M3: Implement on-demand TLS certificate rotation

Add support for on-demand TLS certificate rotation that doesn't require
a server restart. When a certificate is about to expire, the ACME client
should obtain a new certificate and hot-swap it into the TLS config
without dropping any active connections. Add logging and metrics for
certificate rotation events.

## Wide

### W1: Add a web-based admin dashboard

Implement a web UI for Caddy's admin API. The dashboard should display
the current configuration, active sites, TLS certificate status,
upstream health for reverse proxies, request metrics, and access logs.
Support live configuration editing with validation and rollback.
The UI should be a single embedded module served from the admin
endpoint.

### W2: Implement request tracing with OpenTelemetry

Add native OpenTelemetry support across the request lifecycle. Create
spans for the listener accept, TLS handshake, route matching, each
handler in the chain, reverse proxy upstream selection, upstream request,
and response writing. Propagate trace context across reverse proxy
hops. Add a `tracing` Caddyfile directive and JSON config. Support
OTLP export (gRPC and HTTP).

### N4: Fix `respond` directive not setting Content-Type for JSON bodies

When using `respond 200 {"ok":true}` in a Caddyfile, the response has
no Content-Type header. The respond handler does not detect JSON content.
Fix the respond handler to auto-detect and set `application/json` when
the body starts with `{` or `[`.

### N5: Fix `file_server` browse mode not HTML-escaping filenames

The directory listing in `file_server browse` renders filenames as raw
HTML. A file named `<script>alert(1)</script>.txt` creates an XSS
vulnerability. Fix the browse template to HTML-escape filenames.

### N6: Add `acme_dns_challenge` directive for DNS-01 ACME challenges

The Caddyfile supports HTTP-01 and TLS-ALPN-01 ACME challenges but has
no shorthand for DNS-01. Add an `acme_dns_challenge` directive that
configures DNS-01 with a specified provider (Cloudflare, Route53, etc.)
and credentials.

### N7: Fix `reverse_proxy` not forwarding trailer headers

When the upstream response includes HTTP trailers, the reverse proxy
strips them before forwarding to the client. The proxy does not set up
trailer header forwarding. Fix the proxy handler to declare and forward
trailers using `http.ResponseWriter`'s trailer support.

### N8: Fix `log` directive custom format not applying to error responses

Custom access log formats configured via `log { format ... }` are not
applied to responses generated by Caddy's error handler (404 pages,
redirect responses). The error handler writes its own log entry bypassing
the configured format. Fix error logging to use the configured format.

### N9: Add `request_id` directive for correlation

Add a `request_id` directive that generates a unique ID per request,
adds it to the response headers (`X-Request-ID`), injects it into the
request context for use by other handlers, and includes it in access
log entries. Support accepting an existing request ID from an incoming
header.

### N10: Fix `basicauth` not constant-time comparing passwords

The `basicauth` handler compares bcrypt-hashed passwords but the
username comparison is not constant-time, leaking timing information
about valid usernames. Fix the auth handler to use constant-time
comparison for both username and password verification.

### M4: Implement request body buffering with size limits

Add a `request_body` directive that controls request body handling:
maximum size (reject with 413), buffering mode (buffer to memory vs
stream), and timeout for reading the request body. Support per-route
configuration. Add metrics for request body sizes.

### M5: Add gRPC reverse proxy support

Implement gRPC-aware reverse proxy handling. Support gRPC metadata
(headers/trailers) forwarding, gRPC status code mapping, bidirectional
streaming, gRPC-Web protocol translation, and gRPC health checking
for load balancing. Add Caddyfile syntax `reverse_proxy grpc://backend`.

### M6: Implement IP geolocation handler

Add a `geoip` handler module that resolves client IP addresses to
geographic data (country, city, ASN) using a MaxMind GeoIP2 database.
Make geolocation data available as placeholders (`{geo.country_code}`)
for use in matchers, headers, and log entries. Support auto-updating
the GeoIP database.

### M7: Add canary deployment support in reverse proxy

Implement traffic splitting in the reverse proxy for canary deployments.
Support weight-based routing (`canary weight 10%` sends 10% of traffic
to the canary upstream), cookie-based stickiness (once a user hits the
canary, they stay), and header-based routing for internal testing.

### M8: Implement structured access log with rich fields

Extend the access log with per-request structured fields: request body
size, response body size, TLS version and cipher, upstream response time
(for proxy), cache hit/miss status, compression ratio, and client
certificate subject (for mTLS). Support JSON and Common Log Format output.

### M9: Add connection limiting per client

Implement `connection_limit` handler that limits concurrent connections
per client IP. Support configurable limit, custom response on limit
exceeded, whitelisting for trusted IPs, and metrics for rejected
connections. Add Caddyfile syntax and JSON config.

### M10: Implement dynamic upstreams from service discovery

Add reverse proxy upstream discovery from external sources: DNS SRV
records, Consul service catalog, and static file. Upstreams are
refreshed periodically (configurable interval). Support health-aware
discovery where unhealthy upstreams are excluded from the refresh result.

### W3: Add Prometheus metrics and Grafana dashboard

Implement native Prometheus metrics export: request count/duration/size
by status code and route, active connections, TLS certificate expiry
times, reverse proxy upstream health/latency, cache hit ratios, and
Go runtime metrics. Add a bundled Grafana dashboard JSON. Serve metrics
via a dedicated admin endpoint. This spans metric collection points
across all handler modules, the admin API, and adds a metrics module.

### W4: Implement edge-side includes (ESI) handler

Add an ESI handler that processes `<esi:include src="..."/>` tags in
HTML responses. The handler fetches included fragments from upstream
services, caches them independently (per-fragment TTL), handles errors
per fragment (fallback content), and supports parallel fragment
fetching. Changes span the response pipeline (body interception),
caching module, upstream fetching, and error handling.

### W5: Add WAF (Web Application Firewall) module

Implement a WAF handler module with: OWASP Core Rule Set support, SQL
injection detection, XSS detection, path traversal detection, rate-based
rules (too many 404s from one IP), and custom rule definitions in
Caddyfile syntax. Support allow/deny/log actions per rule. Include a
learning mode that logs violations without blocking. This spans request
parsing, response inspection, rule evaluation, and adds a WAF module.

### W6: Implement configuration version control and rollback

Add configuration versioning to the admin API. Every config change is
persisted with a version number and timestamp. Support `GET /config/versions`
(list versions), `GET /config/versions/:id` (get specific version),
`POST /config/rollback/:id` (roll back to a version), and automatic
rollback on startup failure. Add a config diff endpoint. This spans
the admin API, config management, storage, and lifecycle management.

### W7: Add HTTP/3 Alt-Svc discovery and zero-RTT support

Implement full HTTP/3 support with: Alt-Svc header advertisement for
HTTP/3 availability, 0-RTT connection resumption with replay protection,
QUIC connection migration (handle client IP changes), and HTTP/3 QPACK
header compression. Add HTTP/3-specific metrics and admin visibility.
This spans the listener, TLS configuration, HTTP/3 transport, and
metrics collection.

### W8: Implement zero-downtime config reload with connection draining

Add graceful configuration reloading that: loads and validates the new
config, starts new listeners, drains existing connections with a
configurable timeout, migrates persistent connections (WebSocket, SSE)
to the new config, and rolls back if the new config fails health checks.
Currently config reload is instantaneous but drops in-flight requests.
This spans the config loader, listener management, connection tracking,
and admin API.

### W9: Add multi-site deployment templating

Implement a Caddyfile templating system for managing many similar sites.
Support `site_template` definitions with parameterized blocks, `import`
with variable substitution, conditional blocks based on environment
variables, and template inheritance (base template + per-site overrides).
Add validation that template expansion produces valid Caddyfile syntax.
This spans the Caddyfile parser, adapter, and adds a template module.

### W10: Implement request recording and replay for testing

Add a record/replay system. A `record` handler captures full request/
response pairs to disk in HAR format. A `replay` handler serves
previously recorded responses without contacting upstreams, matching
by URL, method, and headers. Support fuzzy matching (ignore timestamps
in requests), response modification (update dates), and a management
API for listing and managing recordings. This spans the handler chain,
file I/O, request matching, and adds record/replay modules.
