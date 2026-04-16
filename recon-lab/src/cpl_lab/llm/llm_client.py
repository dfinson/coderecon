"""GitHub Models REST helpers for recon-lab LLM calls."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any


_API_URL = "https://models.github.ai/inference/chat/completions"
_API_VERSION = "2026-03-10"


def _token_from_env_or_gh() -> str | None:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key)
        if value:
            return value

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode == 0:
        token = result.stdout.strip()
        if token:
            return token
    return None


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
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    payload[token_budget_field] = max_tokens
    return payload


def _post_json(*, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        _API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": _API_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(body or f"GitHub Models request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub Models request failed: {exc.reason}") from exc


def run_chat_completion(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout: int = 90,
) -> dict[str, Any]:
    token = _token_from_env_or_gh()
    if not token:
        raise RuntimeError("No GitHub token available for GitHub Models")

    token_budget_field = _token_budget_field(model)
    payload = _build_payload(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        token_budget_field=token_budget_field,
    )
    try:
        return _post_json(token=token, payload=payload, timeout=timeout)
    except RuntimeError as exc:
        message = str(exc)
        if token_budget_field == "max_tokens" and "max_completion_tokens" in message:
            retry_payload = _build_payload(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                token_budget_field="max_completion_tokens",
            )
            return _post_json(token=token, payload=retry_payload, timeout=timeout)
        raise


def response_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("GitHub Models response did not include choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("GitHub Models response did not include a message")
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
    raise RuntimeError("GitHub Models response content was not text")