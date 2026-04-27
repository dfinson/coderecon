"""Tests for endpoint detection module."""

from __future__ import annotations

from coderecon.index._internal.analysis.endpoint_detection import (
    detect_endpoints_in_source,
)

class TestPythonEndpoints:
    def test_flask_route(self) -> None:
        src = '''
@app.route("/api/users")
def get_users():
    pass
'''
        eps = detect_endpoints_in_source(src, "python")
        assert len(eps) >= 1
        assert any(e.url_pattern == "/api/users" for e in eps)
        assert any(e.kind == "server" for e in eps)

    def test_fastapi_get(self) -> None:
        src = '''
@app.get("/api/items/{item_id}")
async def get_item(item_id: int):
    pass
'''
        eps = detect_endpoints_in_source(src, "python")
        assert any(e.url_pattern == "/api/items/{item_id}" for e in eps)
        assert any(e.http_method == "GET" for e in eps)

    def test_fastapi_post(self) -> None:
        src = '''
@router.post("/api/users")
async def create_user():
    pass
'''
        eps = detect_endpoints_in_source(src, "python")
        assert any(e.http_method == "POST" for e in eps)

    def test_python_client(self) -> None:
        src = '''
response = requests.get("https://api.example.com/data")
'''
        eps = detect_endpoints_in_source(src, "python")
        assert any(e.kind == "client" for e in eps)

class TestJavaScriptEndpoints:
    def test_express_get(self) -> None:
        src = '''
app.get("/api/products", handler);
'''
        eps = detect_endpoints_in_source(src, "javascript")
        assert any(e.url_pattern == "/api/products" for e in eps)

    def test_nestjs_decorator(self) -> None:
        src = '''
@Get("/items")
getItems() {}
'''
        eps = detect_endpoints_in_source(src, "typescript")
        assert any(e.url_pattern == "/items" for e in eps)

class TestGoEndpoints:
    def test_gin_route(self) -> None:
        src = '''
r.GET("/api/health", healthHandler)
'''
        eps = detect_endpoints_in_source(src, "go")
        assert any(e.url_pattern == "/api/health" for e in eps)

class TestNoEndpoints:
    def test_empty_source(self) -> None:
        eps = detect_endpoints_in_source("", "python")
        assert eps == []

    def test_no_routes(self) -> None:
        src = "def hello():\n    print('hello')\n"
        eps = detect_endpoints_in_source(src, "python")
        assert eps == []

class TestClientDetection:
    def test_fetch(self) -> None:
        src = '''
const data = await fetch("/api/data");
'''
        eps = detect_endpoints_in_source(src, "javascript")
        assert any(e.kind == "client" and "/api/data" in e.url_pattern for e in eps)

    def test_ignores_non_url_strings(self) -> None:
        src = '''
response = requests.get("not a url")
'''
        eps = detect_endpoints_in_source(src, "python")
        # Should not detect non-URL strings
        assert not any(e.url_pattern == "not a url" for e in eps)
