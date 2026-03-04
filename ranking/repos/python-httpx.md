# encode/httpx

| Field | Value |
|-------|-------|
| **URL** | https://github.com/encode/httpx |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Small (focused library) |
| **Category** | HTTP client library |

## Why this repo

- **Single-purpose**: Async/sync HTTP client for Python. One developer can hold
  the entire codebase in their head.
- **Well-structured**: Clean separation between transport layer, client API,
  auth, middleware, decoders. Source in `httpx/` with ~50 core modules.
- **Rich history**: Active development since 2019, ~3K commits, regular PRs
  with meaningful code review. Used by FastAPI and many production systems.
- **Permissive**: BSD-3-Clause, fully usable for training data.

## Structure overview

```
httpx/
├── _api.py              # Top-level convenience functions
├── _client.py           # Client and AsyncClient classes
├── _auth.py             # Authentication flows
├── _transports/         # HTTP/1.1, HTTP/2, mock transports
├── _decoders.py         # Content decoders
├── _models.py           # Request/Response models
├── _urls.py             # URL parsing
├── _content.py          # Request body encoding
└── _config.py           # SSL, timeout, proxy config
```

## Scale indicators

- ~50 Python source files
- ~15K lines of code
- Clear module boundaries, no deep nesting
- Minimal dependencies (httpcore, anyio)
