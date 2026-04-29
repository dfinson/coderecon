"""Azure OpenAI REST helpers for recon-lab LLM calls."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any


# ── Azure AAD token cache ────────────────────────────────────────

_azure_token: str | None = None
_azure_token_expires: float = 0.0
_azure_token_lock = threading.Lock()

# Refresh the token this many seconds before expiry to avoid racing
# with in-flight requests that would fail with 401.
_TOKEN_REFRESH_MARGIN_SEC = 300  # 5 minutes

# Floor on cached TTL — prevents a near-expired token from being cached
# with TTL=0 and re-fetched on every call.
_TOKEN_MIN_TTL_SEC = 60


def _get_azure_token() -> str | None:
    """Return a valid Azure AAD token, refreshing if needed.

    Uses ``az account get-access-token`` which returns both the token
    and its expiry timestamp.  Thread-safe via lock.
    """
    global _azure_token, _azure_token_expires

    if _azure_token and time.monotonic() < (_azure_token_expires - _TOKEN_REFRESH_MARGIN_SEC):
        return _azure_token

    with _azure_token_lock:
        if _azure_token and time.monotonic() < (_azure_token_expires - _TOKEN_REFRESH_MARGIN_SEC):
            return _azure_token

        try:
            result = subprocess.run(
                ["az", "account", "get-access-token",
                 "--resource", "https://cognitiveservices.azure.com",
                 "--output", "json"],
                capture_output=True, text=True, timeout=30, check=True,
            )
            body = json.loads(result.stdout)
            token = body.get("accessToken", "").strip()
            if not token:
                return None

            expires_on = body.get("expires_on")
            ttl = int(expires_on) - int(time.time()) if expires_on else 3600

            _azure_token = token
            _azure_token_expires = time.monotonic() + max(ttl, _TOKEN_MIN_TTL_SEC)
            return _azure_token
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError, json.JSONDecodeError, ValueError):
            _azure_token = None
            _azure_token_expires = 0.0
            return None


# ── Helpers ──────────────────────────────────────────────────────


def _token_budget_field(model: str) -> str:
    return "max_completion_tokens" if "gpt-5" in model.lower() else "max_tokens"


def _build_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    token_budget_field: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    payload[token_budget_field] = max_tokens
    return payload


# ── Azure OpenAI transport ───────────────────────────────────────


def _resolve_endpoint_and_token() -> tuple[str, str]:
    """Resolve Azure OpenAI endpoint and auth token.

    Raises RuntimeError if AZURE_OPENAI_ENDPOINT is not set or AAD
    token cannot be obtained.
    """
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    if not endpoint:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT is not set. "
            "Export it to point at your Azure OpenAI resource."
        )

    token = _get_azure_token()
    if not token:
        raise RuntimeError(
            "Could not obtain Azure AAD token. Run: az login"
        )
    return endpoint, token


def run_chat_completion(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout: int = 90,
) -> dict[str, Any]:
    """Send a chat completion request to Azure OpenAI."""
    endpoint, token = _resolve_endpoint_and_token()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    deployment = model.split("/")[-1] if "/" in model else model
    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

    token_budget = _token_budget_field(model)
    payload = _build_payload(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        token_budget_field=token_budget,
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(body or f"Azure OpenAI request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Azure OpenAI request failed: {exc.reason}") from exc


def response_text(body: dict[str, Any]) -> str:
    """Extract text content from an OpenAI chat completion response."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Response did not include choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Response did not include a message")
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    raise RuntimeError("Response content was not text")