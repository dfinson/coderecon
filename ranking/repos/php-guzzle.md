# guzzle/guzzle

| Field | Value |
|-------|-------|
| **URL** | https://github.com/guzzle/guzzle |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Small (focused library) |
| **Category** | HTTP client library |

## Why this repo

- **Single-purpose**: PHP HTTP client with PSR-7/PSR-18 compliance.
  Middleware/handler pipeline, async requests, and cookie/redirect handling.
  One developer can hold the full codebase in their head.
- **Well-structured**: Source under `src/` with flat organization. Middleware
  stack, handler system, cookie handling, retry logic, and PSR-compliant
  request/response are clearly separated.
- **Rich history**: 4K+ commits, maintained since 2011. The standard HTTP
  client for PHP (Laravel, Symfony, WordPress all use it). Steady PRs with
  a mix of features and fixes.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── Client.php              # Main HTTP client
├── ClientInterface.php     # Client contract
├── HandlerStack.php        # Middleware/handler pipeline
├── Handler/                # HTTP handlers
│   ├── CurlHandler.php     # cURL-based handler
│   ├── CurlMultiHandler.php # Async cURL handler
│   ├── StreamHandler.php   # PHP stream handler
│   └── MockHandler.php     # Testing mock
├── Middleware.php           # Built-in middleware (retry, redirect, cookies)
├── Cookie/                  # Cookie jar and storage
│   ├── CookieJar.php
│   └── SetCookie.php
├── Exception/               # Exception hierarchy
├── RetryMiddleware.php      # Retry logic
├── RedirectMiddleware.php   # Redirect following
└── TransferStats.php        # Transfer statistics
```

## Scale indicators

- ~30 PHP source files
- ~8K lines of code
- Flat structure (1-2 levels)
- Depends on PSR packages (psr/http-message, psr/http-client)
