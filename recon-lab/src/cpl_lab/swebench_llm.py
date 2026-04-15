"""LLM-backed adaptation for SWE-bench instances.

Each LLM call does exactly ONE thing.  gpt-4.1-mini is cheap but not
smart — we never ask it to juggle multiple output types in one shot.

Call decomposition:
  1. ``_classify_task``      — complexity + confidence + notes  (1 call)
  2. ``_generate_ok_query``   — one query for one type           (8 calls)
  3. ``_generate_non_ok_query`` — one query for one non-OK type  (6 calls)
Total: 15 calls per instance.  Every call produces exactly one output.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpl_lab.llm_client import response_text, run_chat_completion

logger = logging.getLogger(__name__)

OK_QUERY_TYPES = (
    "Q_SEMANTIC",
    "Q_LEXICAL",
    "Q_IDENTIFIER",
    "Q_STRUCTURAL",
    "Q_NAVIGATIONAL",
    "Q_SEM_IDENT",
    "Q_IDENT_NAV",
    "Q_FULL",
)
NON_OK_TYPES = ("UNSAT", "BROAD", "AMBIG")

# Human-readable one-liner per query type so the model understands
# what it's supposed to produce.
_OK_TYPE_DESCRIPTIONS: dict[str, str] = {
    "Q_SEMANTIC": "A natural-language question describing the problem in plain English, with no code identifiers or file paths.",
    "Q_LEXICAL": "A keyword-heavy query using exact terms from the codebase (class names, error messages, config keys) but no full identifiers.",
    "Q_IDENTIFIER": "A query that names specific function/class/variable identifiers the developer would search for.",
    "Q_STRUCTURAL": "A query about code structure: inheritance, call chains, module layout, or interface contracts.",
    "Q_NAVIGATIONAL": "A query that specifies a file path or directory the developer would navigate to.",
    "Q_SEM_IDENT": "A query combining a natural-language description with one or two specific identifiers.",
    "Q_IDENT_NAV": "A query combining specific identifiers with a file path or directory.",
    "Q_FULL": "The ideal query: a concise description with identifiers as seeds and file paths as pins.",
}

_NON_OK_DESCRIPTIONS: dict[str, str] = {
    "UNSAT": (
        "A query that CANNOT be answered from this repository — it asks about "
        "something that doesn't exist here. CRITICAL: Do NOT reuse any "
        "identifier, class name, function name, or file path that appears in "
        "the issue or patch. Invent plausible-sounding but fictional names."
    ),
    "BROAD": (
        "A query that is too vague — it would match hundreds of files with no "
        "clear focus. The query MUST be 3-10 words long (not a single word). "
        "It should sound like a real developer question, just too unfocused."
    ),
    "AMBIG": (
        "A query that is ambiguous — the words could refer to multiple "
        "unrelated subsystems in this repo."
    ),
}


@dataclass
class AdaptationResult:
    task_complexity: str
    confidence: str
    solve_notes: str
    tier_difference_reasoning: str
    queries: list[dict[str, Any]]
    non_ok_queries: list[dict[str, Any]]


# ── Public entry point ───────────────────────────────────────────


def adapt_instance(
    *,
    model: str,
    instance_id: str,
    repo: str,
    problem_statement: str,
    hints_text: str,
    patch_text: str,
    minimum_sufficient_defs: list[dict[str, Any]] | None = None,
    thrash_preventing_defs: list[dict[str, Any]] | None = None,
) -> AdaptationResult:
    """Produce all GT fields for one instance via focused single-purpose LLM calls.

    15 calls total:
      1 classify  +  8 OK queries  +  6 non-OK queries = 15
    Every call produces exactly one output.  Never more than one query per call.
    """

    # Shared context block (reused across all calls)
    context = _build_context(
        instance_id=instance_id,
        repo=repo,
        problem_statement=problem_statement,
        hints_text=hints_text,
        patch_text=patch_text,
        minimum_sufficient_defs=minimum_sufficient_defs,
        thrash_preventing_defs=thrash_preventing_defs,
    )

    # Call 1: classify task
    classification = _classify_task(model, context)

    # Calls 2-9: one OK query per type
    ok_queries: list[dict[str, Any]] = []
    for query_type in OK_QUERY_TYPES:
        q = _generate_ok_query(model, context, query_type)
        ok_queries.append(q)

    # Calls 10-15: one non-OK query per call (2 per type × 3 types = 6 calls)
    # Extract patch identifiers once — used by the cheap Phase 1 gate
    patch_identifiers = _extract_patch_identifiers(patch_text)
    non_ok_queries: list[dict[str, Any]] = []
    for non_ok_type in NON_OK_TYPES:
        for variant in (1, 2):
            q = _generate_non_ok_query(model, context, non_ok_type, variant, patch_identifiers)
            non_ok_queries.append(q)

    return AdaptationResult(
        task_complexity=classification["task_complexity"],
        confidence=classification["confidence"],
        solve_notes=classification["solve_notes"],
        tier_difference_reasoning=classification["tier_difference_reasoning"],
        queries=ok_queries,
        non_ok_queries=non_ok_queries,
    )


# ── Context builder ─────────────────────────────────────────────


def _build_context(
    *,
    instance_id: str,
    repo: str,
    problem_statement: str,
    hints_text: str,
    patch_text: str,
    minimum_sufficient_defs: list[dict[str, Any]] | None,
    thrash_preventing_defs: list[dict[str, Any]] | None,
) -> str:
    """Build a reusable context block that gets prepended to every call."""
    parts = [
        f"Repository: {repo}",
        f"Instance: {instance_id}",
        "",
        "## Issue",
        problem_statement[:3000],
    ]
    if hints_text and hints_text.strip():
        parts += ["", "## Hints", hints_text[:1500]]
    parts += ["", "## Patch (excerpt)", patch_text[:6000]]
    if minimum_sufficient_defs:
        defs_text = "\n".join(
            f"  - {d['path']}:{d.get('start_line','')} ({d.get('kind','')}: {d.get('name','')})"
            for d in minimum_sufficient_defs[:15]
        )
        parts += ["", "## Changed definitions", defs_text]
    if thrash_preventing_defs:
        ctx_text = "\n".join(
            f"  - {d['path']}:{d.get('start_line','')} ({d.get('kind','')}: {d.get('name','')})"
            for d in thrash_preventing_defs[:20]
        )
        parts += ["", "## Related context definitions", ctx_text]
    return "\n".join(parts)


# ── Call 1: Classify task ────────────────────────────────────────


_CLASSIFY_SYSTEM = """\
You classify a GitHub issue for a code retrieval benchmark.
Return ONLY a JSON object with exactly these four fields:
{
  "task_complexity": "narrow" or "medium" or "wide",
  "confidence": "high" or "medium" or "low",
  "solve_notes": "One sentence summarising what the fix does.",
  "tier_difference_reasoning": "One sentence explaining why some defs are edited and others are just read."
}

Definitions:
- narrow: touches 1-2 functions in 1-2 files
- medium: touches 3-7 functions across a few files
- wide: touches 8+ functions or crosses multiple subsystems
- confidence: how confident you are in the classification
"""


def _classify_task(model: str, context: str) -> dict[str, Any]:
    """Classify task complexity, confidence, and notes."""
    result = _call_llm_json(
        model=model,
        system_prompt=_CLASSIFY_SYSTEM,
        user_prompt=context,
        max_tokens=300,
    )
    # Validate
    if result.get("task_complexity") not in {"narrow", "medium", "wide"}:
        result["task_complexity"] = "medium"
    if result.get("confidence") not in {"high", "medium", "low"}:
        result["confidence"] = "medium"
    if not isinstance(result.get("solve_notes"), str):
        result["solve_notes"] = ""
    if not isinstance(result.get("tier_difference_reasoning"), str):
        result["tier_difference_reasoning"] = ""
    return result


# ── Calls 2-9: One OK query per type ────────────────────────────


_OK_QUERY_SYSTEM = """\
You write ONE search query for a code retrieval system.
Return ONLY a JSON object with exactly these fields:
{
  "query_text": "the search query a developer would type",
  "seeds": ["identifier1", "identifier2"],
  "pins": ["path/to/file.py"],
  "justification": "why this query would find the right code"
}

Rules:
- seeds = concrete function/class/variable names from the repo (if appropriate for this query type)
- pins = concrete file paths from the repo (if appropriate for this query type)
- seeds and pins can be empty lists if the query type doesn't use them
- query_text should be realistic — what a developer would actually type
"""


def _generate_ok_query(
    model: str,
    context: str,
    query_type: str,
) -> dict[str, Any]:
    """Generate one OK query for the given type."""
    description = _OK_TYPE_DESCRIPTIONS[query_type]
    user_prompt = (
        f"{context}\n\n"
        f"---\n\n"
        f"Write ONE {query_type} query for the issue above.\n\n"
        f"What {query_type} means: {description}\n"
    )
    result = _call_llm_json(
        model=model,
        system_prompt=_OK_QUERY_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=300,
    )
    # Normalize
    result["query_type"] = query_type
    if not isinstance(result.get("query_text"), str) or not result["query_text"].strip():
        raise RuntimeError(f"LLM returned empty query_text for {query_type}")
    result.setdefault("seeds", [])
    result.setdefault("pins", [])
    result.setdefault("justification", "")
    if not isinstance(result["seeds"], list):
        result["seeds"] = []
    if not isinstance(result["pins"], list):
        result["pins"] = []
    return result


# ── Calls 10-15: One non-OK query per call ───────────────────────


_NON_OK_SYSTEM = """\
You write ONE search query that should NOT return good results from a code retrieval system.
Return ONLY a JSON object with exactly these fields:
{
  "query_text": "the bad search query",
  "seeds": [],
  "pins": []
}

Rules:
- The query must be realistic — something a developer might actually type.
- The query must FAIL to retrieve the correct code for the specified reason.
- query_text must be 3-10 words. Never a single word.
- Do NOT copy any identifier, class name, function name, or file path from the
  issue or patch into query_text. Paraphrase or invent new names.
- seeds and pins must always be empty lists for non-OK queries.
"""


def _generate_non_ok_query(
    model: str,
    context: str,
    non_ok_type: str,
    variant: int,
    patch_identifiers: set[str],
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Generate ONE non-OK query for the given type, with deterministic gating.

    Phase 1 gates (no coderecon required):
      - Identifier contamination: reject if any patch identifier leaks into query_text
      - Word count: reject BROAD queries with <3 or >10 words
    Retries up to ``max_retries`` times on gate failure before raising.
    """
    description = _NON_OK_DESCRIPTIONS[non_ok_type]

    for attempt in range(1, max_retries + 1):
        user_prompt = (
            f"{context}\n\n"
            f"---\n\n"
            f"Write ONE {non_ok_type} query (variant {variant}) about this repository.\n\n"
            f"What {non_ok_type} means: {description}\n"
        )
        # On retries, add explicit instruction about what went wrong
        if attempt > 1:
            user_prompt += (
                f"\n(Previous attempt was rejected. "
                f"Do NOT use any of these identifiers: {sorted(patch_identifiers)[:20]}. "
                f"Be more creative.)\n"
            )

        result = _call_llm_json(
            model=model,
            system_prompt=_NON_OK_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=200,
        )
        query_text = result.get("query_text", "")
        if not isinstance(query_text, str) or not query_text.strip():
            logger.warning("Empty query_text for %s variant %d, attempt %d", non_ok_type, variant, attempt)
            continue

        # Gate check
        failure = _gate_non_ok_query(query_text, non_ok_type, patch_identifiers)
        if failure is None:
            return {
                "query_type": non_ok_type,
                "query_text": query_text,
                "seeds": result.get("seeds", []) if isinstance(result.get("seeds"), list) else [],
                "pins": result.get("pins", []) if isinstance(result.get("pins"), list) else [],
            }

        logger.warning(
            "Gate rejected %s variant %d attempt %d: %s (query=%r)",
            non_ok_type, variant, attempt, failure, query_text,
        )

    raise RuntimeError(
        f"Failed to generate a valid {non_ok_type} query (variant {variant}) "
        f"after {max_retries} attempts"
    )


# ── Phase 1 deterministic gates ─────────────────────────────────

# Compound CamelCase identifiers — must have ≥2 CamelCase segments
# (e.g. LcovParser, CoverageReport, SelectCrawler).  Single-segment names
# like Reference, Extract, Optional are common English words and excluded.
_CAMEL_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b")

# snake_case compound identifiers (must contain at least one underscore,
# e.g. parse_warnings, base_commit).  Single words without underscores are
# excluded — they're almost always generic vocabulary.
_SNAKE_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")

# CamelCase tokens that are Python builtins / keywords, not project names.
_CAMEL_IGNORE = frozenset({
    "True", "False", "None", "ValueError", "TypeError", "KeyError",
    "AttributeError", "IndexError", "RuntimeError", "FileNotFoundError",
    "NotImplementedError", "StopIteration", "Exception", "BaseException",
    "PermissionError", "ImportError", "NameError", "SyntaxError",
})


def _extract_patch_identifiers(patch_text: str) -> set[str]:
    """Extract project-specific identifiers from patch text.

    Only captures names that are likely to be project-specific:
      - CamelCase class/type names (``LcovParser``, ``CoverageReport``)
      - snake_case compound names with underscores (``parse_warnings``)
      - File stems ≥5 chars from diff headers

    Generic single words (``sql``, ``update``, ``check``) are ignored.
    Returns lowercased identifiers.
    """
    identifiers: set[str] = set()

    for line in patch_text.splitlines():
        # File paths from diff headers — extract filename stems only
        if line.startswith("diff --git"):
            parts = line.split()
            for p in parts[2:]:
                stem = Path(p.lstrip("ab/")).stem
                if len(stem) >= 5:
                    identifiers.add(stem.lower())
            continue

        # Hunk headers + added/removed lines
        if line.startswith(("@@", "+", "-")) and not line.startswith(("+++", "---")):
            for m in _CAMEL_RE.finditer(line):
                name = m.group(1)
                if name not in _CAMEL_IGNORE:
                    identifiers.add(name.lower())
            for m in _SNAKE_RE.finditer(line):
                identifiers.add(m.group(1).lower())

    return identifiers


def _gate_non_ok_query(
    query_text: str,
    non_ok_type: str,
    patch_identifiers: set[str],
) -> str | None:
    """Check a non-OK query against deterministic Phase 1 gates.

    Returns ``None`` if the query passes, or a failure reason string.
    """
    words = query_text.split()

    # Gate 1: Word count for BROAD (3-10 words)
    if non_ok_type == "BROAD":
        if len(words) < 3:
            return f"BROAD query too short ({len(words)} words, need ≥3)"
        if len(words) > 10:
            return f"BROAD query too long ({len(words)} words, need ≤10)"

    # Gate 2: Identifier contamination (applies to UNSAT + BROAD)
    # Only checks for project-specific names (CamelCase / snake_case compound),
    # not generic single words.
    if non_ok_type in ("UNSAT", "BROAD"):
        query_lower = query_text.lower()
        # Extract CamelCase and snake_case compound tokens from the query
        query_camel = {m.group(1).lower() for m in _CAMEL_RE.finditer(query_text)}
        query_snake = {m.group(1).lower() for m in _SNAKE_RE.finditer(query_lower)}
        query_tokens = query_camel | query_snake
        leaked = query_tokens & patch_identifiers
        if leaked:
            return f"identifier contamination: {sorted(leaked)}"

    return None


# ── LLM transport ───────────────────────────────────────────────


# Cached Azure AAD token — refreshed automatically when near expiry.
_azure_token: str | None = None
_azure_token_expires: float = 0.0  # time.monotonic() when it expires


def _get_azure_token() -> str | None:
    """Return a valid Azure AAD token, refreshing if needed.

    Caches the token and refreshes 5 minutes before expiry.
    Uses ``az account get-access-token`` which returns both the token
    and its expiry timestamp.
    """
    import time as _time

    global _azure_token, _azure_token_expires

    # Return cached if still valid (with 5-minute buffer)
    if _azure_token and _time.monotonic() < (_azure_token_expires - 300):
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

        # Parse expiry — az returns "expiresOn" as a datetime string
        # and "expires_on" as a unix timestamp (seconds).
        expires_on = body.get("expires_on")
        if expires_on:
            # expires_on is unix timestamp (int or string)
            ttl = int(expires_on) - int(_time.time())
        else:
            # Fallback: assume 1-hour token lifetime
            ttl = 3600

        _azure_token = token
        _azure_token_expires = _time.monotonic() + max(ttl, 60)
        logger.info("Azure AAD token refreshed, TTL=%ds", ttl)
        return _azure_token
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, json.JSONDecodeError, ValueError):
        _azure_token = None
        _azure_token_expires = 0.0
        return None


def _call_llm_json(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 500,
) -> dict[str, Any]:
    """Call LLM and parse response as JSON.

    Transport priority:
      1. Azure OpenAI (AZURE_OPENAI_ENDPOINT + AAD token via ``az``)
      2. GitHub Models (models.github.ai)
      3. Anthropic (ANTHROPIC_API_KEY)
    """
    import time as _time

    # 1. Azure OpenAI (AAD token auth, cached + auto-refreshed)
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if azure_endpoint:
        azure_endpoint = azure_endpoint.rstrip("/")
        deployment = model.split("/")[-1] if "/" in model else model
        url = f"{azure_endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-12-01-preview"

        azure_token = _get_azure_token()
        if azure_token:
            import time as _time
            import urllib.error

            payload = json.dumps({
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
            }).encode()

            for _attempt in range(5):
                req = urllib.request.Request(url, data=payload, headers={
                    "Authorization": f"Bearer {azure_token}",
                    "Content-Type": "application/json",
                }, method="POST")
                try:
                    resp = json.loads(urllib.request.urlopen(req, timeout=90).read())
                    text = resp["choices"][0]["message"]["content"]
                    return _parse_json_object(text)
                except urllib.error.HTTPError as exc:
                    if exc.code == 429:
                        # Rate limited — exponential backoff
                        wait = min(2 ** _attempt * 5, 60)
                        logger.warning("Azure 429, backing off %ds (attempt %d/5)", wait, _attempt + 1)
                        _time.sleep(wait)
                        continue
                    if exc.code == 401 and _attempt == 0:
                        # Token expired — force refresh and retry
                        global _azure_token, _azure_token_expires
                        _azure_token = None
                        _azure_token_expires = 0.0
                        azure_token = _get_azure_token()
                        if not azure_token:
                            break
                        continue
                    break
                except Exception:
                    break

    # 2. GitHub Models (fallback, with retry + backoff for rate limits)
    for _gh_attempt in range(6):
        try:
            response = run_chat_completion(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                timeout=90,
            )
            return _parse_json_object(response_text(response))
        except RuntimeError as exc:
            msg = str(exc)
            if "429" in msg or "Too many" in msg or "rate" in msg.lower():
                wait = min(2 ** _gh_attempt * 10, 120)
                logger.warning("GitHub Models 429, backing off %ds (attempt %d/6)", wait, _gh_attempt + 1)
                _time.sleep(wait)
                continue
            break

    # 3. Anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        payload = json.dumps(
            {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
        )
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "https://api.anthropic.com/v1/messages",
                "-H", f"x-api-key: {api_key}",
                "-H", "anthropic-version: 2023-06-01",
                "-H", "content-type: application/json",
                "-d", payload,
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode == 0:
            body = json.loads(result.stdout)
            text = body.get("content", [{}])[0].get("text", "")
            return _parse_json_object(text)

    raise RuntimeError("No working LLM transport found for SWE-bench adaptation")


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
    raise RuntimeError("LLM response was not valid JSON")