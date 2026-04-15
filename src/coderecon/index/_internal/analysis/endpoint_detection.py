"""HTTP/RPC endpoint detection — extract route declarations and client calls.

Detects server-side route declarations (decorators like @app.route, @Get, etc.)
and client-side HTTP calls (fetch, requests.get, etc.) using tree-sitter AST
patterns. Persists as EndpointFact for cross-language edge resolution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# ============================================================================
# Regex-based detection patterns (lightweight; no tree-sitter required)
# ============================================================================

# Python: Flask, FastAPI, Django, Starlette
_PY_ROUTE_PATTERNS = [
    # @app.route("/path"), @app.get("/path"), @router.post("/path")
    re.compile(
        r"@\w+\.(route|get|post|put|delete|patch|head|options)\(\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    ),
    # Django path("url", view)
    re.compile(r"path\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    # FastAPI APIRouter with prefix
    re.compile(r"APIRouter\([^)]*prefix\s*=\s*['\"]([^'\"]+)['\"]"),
]

# JavaScript/TypeScript: Express, Nest, Fastify
_JS_ROUTE_PATTERNS = [
    # app.get("/path", handler), router.post("/path", ...)
    re.compile(
        r"\.(get|post|put|delete|patch|all)\(\s*['\"`]([^'\"`]+)['\"`]",
        re.IGNORECASE,
    ),
    # NestJS decorators: @Get("/path"), @Post("/path")
    re.compile(r"@(Get|Post|Put|Delete|Patch)\(\s*['\"]([^'\"]+)['\"]"),
    # @Controller("/prefix")
    re.compile(r"@Controller\(\s*['\"]([^'\"]+)['\"]"),
]

# Go: net/http, Gin, Echo, Chi
_GO_ROUTE_PATTERNS = [
    # r.GET("/path", handler), r.POST("/path", handler)
    re.compile(r"\.(GET|POST|PUT|DELETE|PATCH|Handle|HandleFunc)\(\s*\"([^\"]+)\""),
    # http.HandleFunc("/path", handler)
    re.compile(r"HandleFunc\(\s*\"([^\"]+)\""),
]

# Client-side HTTP calls
_CLIENT_PATTERNS = [
    # Python: requests.get("url"), httpx.post("url")
    re.compile(r"(?:requests|httpx)\.(get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]"),
    # JavaScript: fetch("url"), axios.get("url")
    re.compile(r"(?:fetch|axios)\.(get|post|put|delete|patch)?\(\s*['\"`]([^'\"`]+)['\"`]"),
    re.compile(r"fetch\(\s*['\"`]([^'\"`]+)['\"`]"),
]

# Method extraction from decorator patterns
_METHOD_MAP = {
    "route": "*",
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
    "all": "*",
    "handle": "*",
    "handlefunc": "*",
}


@dataclass(frozen=True, slots=True)
class DetectedEndpoint:
    """An endpoint detected from source code."""

    kind: str  # "server" or "client"
    http_method: str | None
    url_pattern: str
    line: int
    framework: str | None = None


def detect_endpoints_in_source(
    source: str,
    language: str,
) -> list[DetectedEndpoint]:
    """Detect HTTP endpoints in source code.

    Args:
        source: File content.
        language: Language identifier ("python", "javascript", "typescript", "go").

    Returns:
        List of detected endpoints.
    """
    endpoints: list[DetectedEndpoint] = []

    # Select patterns based on language
    server_patterns: list[re.Pattern[str]] = []
    if language in ("python", "py"):
        server_patterns = _PY_ROUTE_PATTERNS
    elif language in ("javascript", "typescript", "js", "ts", "jsx", "tsx"):
        server_patterns = _JS_ROUTE_PATTERNS
    elif language in ("go",):
        server_patterns = _GO_ROUTE_PATTERNS

    lines = source.splitlines()
    for line_no, line in enumerate(lines, 1):
        # Server-side detection
        for pattern in server_patterns:
            for match in pattern.finditer(line):
                groups = match.groups()
                if len(groups) == 2:  # noqa: PLR2004
                    method_raw, url = groups
                    method = _METHOD_MAP.get(method_raw.lower(), method_raw.upper())
                elif len(groups) == 1:
                    url = groups[0]
                    method = "*"
                else:
                    continue

                framework = _detect_framework(line, language)
                endpoints.append(DetectedEndpoint(
                    kind="server",
                    http_method=method,
                    url_pattern=url,
                    line=line_no,
                    framework=framework,
                ))

        # Client-side detection
        for pattern in _CLIENT_PATTERNS:
            for match in pattern.finditer(line):
                groups = match.groups()
                if len(groups) == 2:  # noqa: PLR2004
                    method_raw, url = groups
                    method = (method_raw or "GET").upper()
                elif len(groups) == 1:
                    url = groups[0]
                    method = "GET"
                else:
                    continue

                # Skip non-URL strings
                if not url.startswith(("/", "http://", "https://")):
                    continue

                endpoints.append(DetectedEndpoint(
                    kind="client",
                    http_method=method,
                    url_pattern=url,
                    line=line_no,
                    framework=None,
                ))

    return endpoints


def persist_endpoints(
    engine: Engine,
    file_id: int,
    endpoints: list[DetectedEndpoint],
    handler_def_uid: str | None = None,
) -> int:
    """Persist detected endpoints as EndpointFact rows.

    Returns number of facts written.
    """
    if not endpoints:
        return 0

    written = 0
    with engine.connect() as conn:
        # Clear existing endpoints for this file
        conn.execute(
            text("DELETE FROM endpoint_facts WHERE file_id = :fid"),
            {"fid": file_id},
        )

        for ep in endpoints:
            conn.execute(
                text(
                    "INSERT INTO endpoint_facts "
                    "(file_id, kind, http_method, url_pattern, handler_def_uid, "
                    "start_line, end_line, framework) "
                    "VALUES (:fid, :kind, :method, :url, :handler, :start, :end, :fw)"
                ),
                {
                    "fid": file_id,
                    "kind": ep.kind,
                    "method": ep.http_method,
                    "url": ep.url_pattern,
                    "handler": handler_def_uid,
                    "start": ep.line,
                    "end": ep.line,
                    "fw": ep.framework,
                },
            )
            written += 1

        conn.commit()

    return written


def find_endpoint_edges(engine: Engine) -> list[dict[str, str]]:
    """Find server↔client endpoint matches by URL pattern.

    Returns list of edges: [{server_file, server_url, client_file, client_url}].
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT s.url_pattern, sf.path AS server_path, "
                "  c.url_pattern AS client_url, cf.path AS client_path "
                "FROM endpoint_facts s "
                "JOIN files sf ON sf.id = s.file_id "
                "JOIN endpoint_facts c ON c.kind = 'client' "
                "JOIN files cf ON cf.id = c.file_id "
                "WHERE s.kind = 'server' "
                "AND (c.url_pattern LIKE '%' || s.url_pattern || '%' "
                "  OR s.url_pattern LIKE '%' || c.url_pattern || '%')"
            )
        ).fetchall()

    return [
        {
            "server_file": row[1],
            "server_url": row[0],
            "client_file": row[3],
            "client_url": row[2],
        }
        for row in rows
    ]


def _detect_framework(line: str, language: str) -> str | None:
    """Best-effort framework detection from line content."""
    lower = line.lower()
    if language in ("python", "py"):
        if "fastapi" in lower or "apirouter" in lower:
            return "fastapi"
        if "flask" in lower or "blueprint" in lower:
            return "flask"
        if "starlette" in lower:
            return "starlette"
        if "django" in lower or "path(" in lower:
            return "django"
    elif language in ("javascript", "typescript", "js", "ts"):
        if "@get" in lower or "@post" in lower or "@controller" in lower:
            return "nestjs"
        if "express" in lower:
            return "express"
        if "fastify" in lower:
            return "fastify"
    elif language == "go":
        if "gin" in lower:
            return "gin"
        if "echo" in lower:
            return "echo"
        if "chi" in lower:
            return "chi"
    return None
