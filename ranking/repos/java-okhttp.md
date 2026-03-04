# square/okhttp

| Field | Value |
|-------|-------|
| **URL** | https://github.com/square/okhttp |
| **License** | Apache-2.0 |
| **Language** | Java (Kotlin) |
| **Scale** | Medium (multi-module project) |
| **Category** | HTTP client |

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
okhttp/src/main/kotlin/okhttp3/
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
