# fastapi/fastapi

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fastapi/fastapi |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium (multi-module project) |
| **Category** | Web framework |

## Why this repo

- **Multi-module with clear boundaries**: Routing, dependency injection,
  security (OAuth2, API keys), middleware, background tasks, WebSockets,
  OpenAPI schema generation — each a distinct subsystem requiring navigation
  between them.
- **Well-structured**: All source under `fastapi/` with logical subpackages
  (`security/`, `middleware/`, `openapi/`). Not a monorepo.
- **Rich history**: 10K+ commits, very active PR flow, strong code review
  culture. One of the most popular Python frameworks.
- **Permissive**: MIT license.

## Structure overview

```
fastapi/
├── applications.py      # FastAPI app class
├── routing.py           # APIRouter, route handling
├── dependencies/        # Dependency injection system
├── security/            # OAuth2, HTTP Basic/Bearer, API keys
├── middleware/           # CORS, GZip, HTTPSRedirect
├── openapi/             # OpenAPI schema generation
├── params.py            # Query, Path, Header, Cookie params
├── responses.py         # Response classes
├── encoders.py          # JSON encoding
├── exceptions.py        # HTTP exception handling
├── background.py        # Background tasks
├── websockets.py        # WebSocket support
└── testclient.py        # Test utilities
```

## Scale indicators

- ~80 Python source files
- ~25K lines of code
- Requires navigating between routing, DI, security, and schema subsystems
- Moderate dependency tree (Starlette, Pydantic)
