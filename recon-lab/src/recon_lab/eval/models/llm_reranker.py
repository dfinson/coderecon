"""LLM listwise reranker over def scaffolds.

Inspect AI solver that performs listwise LLM reranking.

Strategy: **listwise** — the top-N candidates (by ``baseline_rank``) are
rendered as a numbered list of scaffold entries (path + signature_text),
then a single LLM call asks for a reordered index array.  Candidates
outside the top-N window are appended after the reranked pool unchanged.

Two backends:
- ``passthrough`` — return candidates in baseline_rank order (no LLM).
- ``azure``       — Azure OpenAI (e.g. GPT-4.1-mini via AAD token).
- ``local``       — OpenAI-compatible local endpoint (ollama, llama.cpp).

Fallback: if the LLM call fails or produces unparseable output, the
baseline order for the pool is used silently.

Output keys stored in ``TaskState.store`` (compatible with ranking scorer):
    ranked_candidate_keys  list[str]   def keys in ranked order
    predicted_relevances   list[float] 1/(rank+1) proxy scores
    predicted_n            int         fixed cutoff (``predicted_n`` arg)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from inspect_ai.solver import Solver, TaskState, solver

logger = logging.getLogger(__name__)


# ── Scaffold rendering ────────────────────────────────────────────────────

def _render_candidate(i: int, cand: dict[str, Any]) -> str:
    sig = (cand.get("signature_text") or "").strip()
    path = cand.get("path", "?")
    kind = cand.get("kind", "")
    name = cand.get("name", "")
    namespace = (cand.get("namespace") or "").strip()
    if sig:
        return f"[{i}] {path}  ({kind})\n    {sig}"
    if namespace and namespace != name:
        return f"[{i}] {path}  ({kind})\n    {namespace}.{name}"
    return f"[{i}] {path}:{kind}:{name}"


_SYSTEM_PROMPT = """\
You are a code relevance expert. Given a bug report or feature request, \
rank the provided code definitions by relevance to the task.

Output ONLY a JSON array of the candidate indices in descending order of \
relevance (most relevant first).
Example output for 5 candidates: [3, 1, 5, 2, 4]

Include every index exactly once. Do not add explanation, reasoning, or prose."""


def _build_user_prompt(problem_statement: str, candidates: list[dict[str, Any]]) -> str:
    scaffolds = "\n\n".join(_render_candidate(i + 1, c) for i, c in enumerate(candidates))
    ps = problem_statement[:1500].strip()
    n = len(candidates)
    return (
        f"## Task\n{ps}\n\n"
        f"## Candidate Definitions (presented in arbitrary order)\n\n"
        f"{scaffolds}\n\n"
        f"Rank these {n} candidates by relevance to the task above. "
        f"Output a JSON array of all {n} indices (1-based)."
    )


# ── Response parsing ──────────────────────────────────────────────────────

def _parse_index_array(text: str, n: int) -> list[int] | None:
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        arr = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list):
        return None
    try:
        raw = [int(x) for x in arr]
    except (ValueError, TypeError):
        return None

    if raw and all(1 <= v <= n for v in raw):
        indices = [v - 1 for v in raw]
    elif raw and all(0 <= v < n for v in raw):
        indices = raw
    else:
        return None

    seen: set[int] = set()
    deduped: list[int] = []
    for i in indices:
        if i not in seen:
            deduped.append(i)
            seen.add(i)
    for i in range(n):
        if i not in seen:
            deduped.append(i)

    return deduped


# ── LLM backends ─────────────────────────────────────────────────────────

def _call_azure_openai(
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout: int,
) -> str:
    from recon_lab.llm.llm_client import _get_azure_token

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    deployment = model_name.split("/")[-1] if "/" in model_name else model_name
    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

    token = _get_azure_token()
    if not token:
        raise RuntimeError("Could not obtain Azure AAD token (run: az login)")

    payload = json.dumps({
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Azure OpenAI error ({exc.code}): {raw[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Azure OpenAI unreachable: {exc.reason}") from exc

    choices = body.get("choices", [])
    if not choices:
        raise RuntimeError("Azure OpenAI response had no choices")
    return choices[0].get("message", {}).get("content", "")


def _call_local_openai(
    endpoint: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout: int,
) -> str:
    payload = json.dumps({
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }).encode("utf-8")

    api_key = os.environ.get("LOCAL_LLM_API_KEY", "ollama")
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Local LLM error ({exc.code}): {raw[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Local LLM unreachable: {exc.reason}") from exc

    choices = body.get("choices", [])
    if not choices:
        raise RuntimeError("Local LLM response had no choices")
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("Local LLM response had empty content")
    return content


# ── Inspect AI solver ─────────────────────────────────────────────────────

class _LLMRerankerPipeline:
    """Shared reranker state across samples."""

    def __init__(
        self,
        backend: str,
        llm_model: str,
        local_endpoint: str,
        top_n: int,
        predicted_n: int,
        max_tokens: int,
        timeout: int,
    ) -> None:
        self._backend = backend
        self._model_name = llm_model
        self._local_endpoint = local_endpoint
        self._top_n = top_n
        self._predicted_n = predicted_n
        self._max_tokens = max_tokens
        self._timeout = timeout

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self._backend == "azure":
            return _call_azure_openai(
                self._model_name, system_prompt, user_prompt,
                self._max_tokens, self._timeout,
            )
        if self._backend == "local":
            return _call_local_openai(
                self._local_endpoint, self._model_name,
                system_prompt, user_prompt,
                self._max_tokens, self._timeout,
            )
        raise ValueError(f"Unknown backend: {self._backend!r}")

    def _llm_rerank(
        self, problem_statement: str, pool: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        user_prompt = _build_user_prompt(problem_statement, pool)
        prompt_chars = len(user_prompt)
        t0 = time.monotonic()
        try:
            raw = self._call_llm(_SYSTEM_PROMPT, user_prompt)
            elapsed = time.monotonic() - t0
            logger.info(
                "LLM %.2fs | model=%s n=%d prompt_chars=%d",
                elapsed, self._model_name, len(pool), prompt_chars,
            )
        except Exception as exc:
            logger.warning("LLM call failed (%s) -- using baseline order", exc)
            return pool

        indices = _parse_index_array(raw, len(pool))
        if indices is None:
            logger.warning(
                "Unparseable LLM output -- using baseline order. Response: %r",
                raw[:300],
            )
            return pool

        return [pool[i] for i in indices]

    def infer(self, meta: dict) -> dict:
        candidates: list[dict[str, Any]] = meta["candidates"]
        problem_statement: str = meta.get("problem_statement", "")

        sorted_cands = sorted(candidates, key=lambda c: c.get("baseline_rank", 99999))

        if self._backend == "passthrough":
            ranked = sorted_cands
            latency_sec = 0.0
        else:
            gt_cands = [
                c for c in sorted_cands
                if c.get("is_gt_edited") or c.get("is_gt_read")
            ]
            non_gt_cands = [
                c for c in sorted_cands
                if not c.get("is_gt_edited") and not c.get("is_gt_read")
            ]
            headroom = max(0, self._top_n - len(gt_cands))
            pool_cands = non_gt_cands[:headroom] + gt_cands
            pool = sorted(pool_cands, key=lambda c: c.get("baseline_rank", 99999))
            tail = non_gt_cands[headroom:]

            t0 = time.monotonic()
            ranked = self._llm_rerank(problem_statement, pool) + tail
            latency_sec = round(time.monotonic() - t0, 3)

        ranked_keys = [c["def_key"] for c in ranked]
        predicted_relevances = [round(1.0 / (i + 1), 4) for i in range(len(ranked_keys))]

        return {
            "ranked_candidate_keys": ranked_keys,
            "predicted_relevances":  predicted_relevances,
            "predicted_n":           self._predicted_n,
            "latency_sec":           latency_sec,
        }


@solver
def llm_reranker(
    backend: str = "passthrough",
    llm_model: str = "openai/gpt-4.1-mini",
    local_endpoint: str = "http://localhost:11434/v1",
    top_n: int = 20,
    predicted_n: int = 10,
    max_tokens: int = 512,
    timeout: int = 90,
) -> Solver:
    """Inspect AI solver: listwise LLM scaffold reranker."""
    pipeline = _LLMRerankerPipeline(
        backend=backend,
        llm_model=llm_model,
        local_endpoint=local_endpoint,
        top_n=top_n,
        predicted_n=predicted_n,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    async def solve(state: TaskState, generate: Any) -> TaskState:
        meta = state.metadata
        result = pipeline.infer(meta)
        state.store.set("ranked_candidate_keys", result["ranked_candidate_keys"])
        state.store.set("predicted_relevances", result["predicted_relevances"])
        state.store.set("predicted_n", result["predicted_n"])
        state.store.set("latency_sec", result.get("latency_sec", 0.0))
        return state

    return solve

