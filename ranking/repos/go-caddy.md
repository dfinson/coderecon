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
