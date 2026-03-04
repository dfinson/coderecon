# rack/rack

| Field | Value |
|-------|-------|
| **URL** | https://github.com/rack/rack |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Small (focused library) |
| **Category** | HTTP server interface |

## Why this repo

- **Single-purpose**: The minimal interface between Ruby web servers and web
  frameworks. Defines the request/response contract and provides a small set
  of middleware and utilities. One developer can easily understand the entire
  codebase.
- **Well-structured**: All source under `lib/rack/` with flat organization.
  Core handler, request/response objects, and built-in middleware (session,
  static, sendfile, etc.) are cleanly separated.
- **Rich history**: 4K+ commits, foundational to the Ruby web ecosystem
  (Rails, Sinatra, etc. all run on Rack). Steady maintenance with careful
  backward-compatibility considerations.
- **Permissive**: MIT license.

## Structure overview

```
lib/rack/
├── handler.rb           # Server handler interface
├── request.rb           # Request object
├── response.rb          # Response object
├── builder.rb           # Rack::Builder (config.ru DSL)
├── utils.rb             # URL encoding, header parsing
├── multipart/           # Multipart form parsing
├── session/             # Session middleware
│   ├── abstract/        # Abstract session store
│   ├── cookie.rb        # Cookie-based sessions
│   └── pool.rb          # In-memory session pool
├── auth/                # Basic/digest authentication
├── static.rb            # Static file serving
├── deflater.rb          # Response compression
├── etag.rb              # ETag generation
├── sendfile.rb          # X-Sendfile support
└── mock/                # Mock request/response for testing
```

## Scale indicators

- ~50 Ruby source files
- ~10K lines of code
- Flat structure (1-2 levels)
- Zero external dependencies
