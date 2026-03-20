"""LLM-backed adaptation for SWE-bench instances."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any


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


@dataclass
class AdaptationResult:
    task_complexity: str
    confidence: str
    solve_notes: str
    tier_difference_reasoning: str
    queries: list[dict[str, Any]]
    non_ok_queries: list[dict[str, Any]]


_SYSTEM_PROMPT = """You are adapting SWE-bench issue instances into ground-truth data for a code retrieval benchmark.

Rules:
- Use the issue text, hints, changed definitions, and broader candidate context.
- Do not invent code facts that contradict the provided patch or candidate lists.
- Anything that requires judgment should be handled by you explicitly; do not defer.
- Emit exactly eight OK queries, one for each required query type.
- Emit at least two non-OK queries for each of UNSAT, BROAD, and AMBIG.
- Keep seeds to concrete identifiers and pins to concrete repo paths when possible.
- Return ONLY valid JSON.
"""


def adapt_instance(
    *,
    model: str,
    instance_id: str,
    repo: str,
    problem_statement: str,
    hints_text: str,
    patch_text: str,
    minimum_sufficient_defs: list[dict[str, Any]],
    thrash_preventing_defs: list[dict[str, Any]],
) -> AdaptationResult:
    """Use an LLM to create the reasoning-heavy GT fields for one instance."""
    user_prompt = json.dumps(
        {
            "instance_id": instance_id,
            "repo": repo,
            "problem_statement": problem_statement,
            "hints_text": hints_text,
            "patch_excerpt": patch_text[:12000],
            "minimum_sufficient_defs": minimum_sufficient_defs[:20],
            "thrash_preventing_defs": thrash_preventing_defs[:40],
            "required_ok_query_types": list(OK_QUERY_TYPES),
            "required_non_ok_types": list(NON_OK_TYPES),
            "response_schema": {
                "task_complexity": "narrow|medium|wide",
                "confidence": "high|medium|low",
                "solve_notes": "brief factual summary",
                "tier_difference_reasoning": "why edited defs are minimum and other defs are read-only context",
                "queries": [
                    {
                        "query_type": "Q_*",
                        "query_text": "string",
                        "seeds": ["identifier"],
                        "pins": ["path"],
                        "justification": "string"
                    }
                ],
                "non_ok_queries": [
                    {
                        "query_type": "UNSAT|BROAD|AMBIG",
                        "query_text": "string",
                        "seeds": [],
                        "pins": []
                    }
                ]
            },
        },
        indent=2,
    )

    payload = _call_llm_json(model=model, system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)
    _validate_payload(payload)
    return AdaptationResult(
        task_complexity=payload["task_complexity"],
        confidence=payload["confidence"],
        solve_notes=payload["solve_notes"],
        tier_difference_reasoning=payload["tier_difference_reasoning"],
        queries=payload["queries"],
        non_ok_queries=payload["non_ok_queries"],
    )


def _call_llm_json(*, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    commands = [
        ["gh", "models", "run", model, "--system-prompt", system_prompt, user_prompt],
    ]

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            return _parse_json_object(result.stdout)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        payload = json.dumps(
            {
                "model": model,
                "max_tokens": 2500,
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


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("task_complexity") not in {"narrow", "medium", "wide"}:
        raise RuntimeError("LLM adaptation returned invalid task_complexity")
    if payload.get("confidence") not in {"high", "medium", "low"}:
        raise RuntimeError("LLM adaptation returned invalid confidence")

    queries = payload.get("queries")
    if not isinstance(queries, list):
        raise RuntimeError("LLM adaptation did not return queries")
    seen_ok = {q.get("query_type") for q in queries if isinstance(q, dict)}
    missing_ok = set(OK_QUERY_TYPES) - seen_ok
    if missing_ok:
        raise RuntimeError(f"LLM adaptation missing OK query types: {sorted(missing_ok)}")

    non_ok_queries = payload.get("non_ok_queries")
    if not isinstance(non_ok_queries, list):
        raise RuntimeError("LLM adaptation did not return non_ok_queries")
    counts = {label: 0 for label in NON_OK_TYPES}
    for query in non_ok_queries:
        if not isinstance(query, dict):
            continue
        query_type = query.get("query_type")
        if query_type in counts:
            counts[query_type] += 1
    missing_non_ok = [label for label, count in counts.items() if count < 2]
    if missing_non_ok:
        raise RuntimeError(f"LLM adaptation missing non-OK coverage: {missing_non_ok}")