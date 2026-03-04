# vapor/vapor

| Field | Value |
|-------|-------|
| **URL** | https://github.com/vapor/vapor |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Medium (multi-module project) |
| **Category** | Server-side web framework |

## Why this repo

- **Multi-module with clear boundaries**: HTTP server (built on SwiftNIO),
  routing, middleware pipeline, content encoding/decoding (JSON, URL-encoded,
  multipart), authentication, sessions, validation, WebSocket support,
  client HTTP — each a distinct subsystem requiring navigation between them.
- **Well-structured**: Source under `Sources/Vapor/` with clear per-concern
  directories. The request pipeline (routing → middleware → handler →
  response) is reflected in code organization.
- **Rich history**: 7K+ commits, the primary Swift server-side framework.
  Active development with PRs covering SwiftNIO integration, async/await
  migration, and API improvements.
- **Permissive**: MIT license.

## Structure overview

```
Sources/Vapor/
├── Application.swift        # Application entry point
├── Routing/                 # Route registration and matching
│   ├── Router.swift         # Route tree
│   ├── Route.swift          # Route definition
│   └── RoutesBuilder.swift  # Route builder DSL
├── HTTP/                    # HTTP layer
│   ├── Server/              # HTTP server (SwiftNIO-based)
│   ├── Client/              # HTTP client
│   └── Headers/             # Header utilities
├── Middleware/               # Middleware pipeline
│   ├── Middleware.swift      # Protocol
│   ├── CORSMiddleware.swift  # CORS handling
│   ├── ErrorMiddleware.swift # Error handling
│   └── FileMiddleware.swift  # Static file serving
├── Content/                  # Content negotiation
│   ├── ContentEncoder.swift  # JSON, URL-encoded, multipart
│   └── ContentDecoder.swift
├── Auth/                     # Authentication
├── Sessions/                 # Session management
├── Validation/               # Input validation
├── WebSocket/                # WebSocket support
├── Request.swift             # Request model
├── Response.swift            # Response model
└── Utilities/                # Shared utilities
```

## Scale indicators

- ~150 Swift source files
- ~30K lines of code
- 2-3 levels of module nesting
- Clear pipeline architecture
