"""LLM-based relevance filtering for thrash_preventing candidates.

Takes the raw structural candidate set (same-file defs, test defs, doc
files) and uses a cheap LLM to filter down to task-relevant items.

The LLM's job is classification, not exploration — each call is a focused
yes/no judgment: "Given this issue, would a developer need to READ this
file/def to solve the task?"

Supports batch processing with configurable concurrency and model.
Falls back to heuristic filtering if no LLM is available.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

DEFAULT_MODEL = "claude-haiku-4.5"

# Cap per-task candidates to avoid runaway costs on wide PRs
MAX_CANDIDATES_PER_TASK = 60

# Defs with these kinds are cheaper to evaluate (less ambiguous)
_LOW_VALUE_KINDS = frozenset({
    "variable", "constant", "pair", "key", "table",
})


# ── Data types ───────────────────────────────────────────────────

@dataclass
class FilterResult:
    """Result of filtering a candidate set."""

    kept: list[dict[str, Any]]
    dropped: list[dict[str, Any]]
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    llm_calls: int = 0
    elapsed_sec: float = 0.0


# ── Heuristic pre-filter (always applied, reduces LLM calls) ────


def _heuristic_prefilter(
    candidates: list[dict[str, Any]],
    min_suff_paths: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fast heuristic pass to trim obviously irrelevant candidates.

    Returns (keep_for_llm, auto_drop).
    """
    keep: list[dict[str, Any]] = []
    drop: list[dict[str, Any]] = []

    for c in candidates:
        path = c.get("path", "")
        kind = c.get("kind", "")
        name = c.get("name", "")

        # Always keep: defs in same file as min_suff (high chance of relevance)
        if path in min_suff_paths:
            keep.append(c)
            continue

        # Drop: GitHub config files (.github/workflows, .github/ISSUE_TEMPLATE)
        if path.startswith(".github/"):
            drop.append(c)
            continue

        # Drop: lock files, generated files
        if any(path.endswith(suffix) for suffix in (
            ".lock", "-lock.json", "-lock.yaml", ".min.js", ".min.css",
            ".generated.go", ".pb.go",
        )):
            drop.append(c)
            continue

        # Drop: low-value defs in non-changed files (e.g. random YAML keys)
        if kind in _LOW_VALUE_KINDS and path not in min_suff_paths:
            drop.append(c)
            continue

        keep.append(c)

    return keep, drop


# ── LLM batch filtering ─────────────────────────────────────────

_FILTER_SYSTEM_PROMPT = """\
You are a code relevance judge. Given a GitHub issue and a candidate \
file/definition from the repository, determine whether a developer \
would need to READ this item to understand how to solve the issue.

Respond with ONLY a JSON object: {"relevant": true/false, "reason": "..."}

Rules:
- "relevant": true means the developer would likely open/read this file \
  while working on the issue
- Documentation, configs, and test files ARE relevant if they directly \
  relate to the subsystem being changed
- Sibling functions in the same class/module ARE relevant if they share \
  state or interface with the changed code
- Generic utility functions, unrelated modules, and boilerplate are NOT relevant
- When in doubt, lean toward "relevant": true (false negatives are worse \
  than false positives for ground truth)
"""


def _build_filter_prompt(
    issue_title: str,
    issue_body: str,
    min_suff_summary: str,
    candidate: dict[str, Any],
) -> str:
    """Build the user prompt for a single candidate."""
    c_path = candidate.get("path", "")
    c_name = candidate.get("name", "")
    c_kind = candidate.get("kind", "")
    c_reason = candidate.get("reason", "")

    return f"""\
## Issue
**{issue_title}**
{issue_body[:800]}

## Files being changed (minimum_sufficient)
{min_suff_summary}

## Candidate to evaluate
- **Path**: {c_path}
- **Name**: {c_name}
- **Kind**: {c_kind}
- **Why considered**: {c_reason}

Would a developer need to READ this item to solve the issue above?"""


def _call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """Call an LLM via the `gh` CLI (Copilot API) or subprocess.

    Tries `gh copilot` first, falls back to `gh models run`.
    Returns parsed JSON response or a default.
    """
    # Try gh models run (GitHub Models API — works with gh CLI)
    try:
        result = subprocess.run(
            [
                "gh", "models", "run", model,
                "--system-prompt", system_prompt,
                user_prompt,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return _parse_llm_response(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: try via environment ANTHROPIC_API_KEY + curl
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return _call_anthropic_api(api_key, model, system_prompt, user_prompt)

    # No LLM available — default to keeping the candidate
    return {"relevant": True, "reason": "no LLM available, kept by default"}


def _call_anthropic_api(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """Direct Anthropic API call as fallback."""
    payload = json.dumps({
        "model": model,
        "max_tokens": 150,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    })

    try:
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
            timeout=30,
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            text = resp.get("content", [{}])[0].get("text", "")
            return _parse_llm_response(text)
    except Exception as e:
        logger.debug("Anthropic API call failed: %s", e)

    return {"relevant": True, "reason": "API call failed, kept by default"}


def _parse_llm_response(text: str) -> dict[str, Any]:
    """Extract JSON from LLM response text."""
    text = text.strip()

    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code block
    import re
    match = re.search(r"\{[^}]+\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: look for yes/no keywords
    lower = text.lower()
    if "not relevant" in lower or '"relevant": false' in lower or "no" in lower[:20]:
        return {"relevant": False, "reason": text[:100]}
    return {"relevant": True, "reason": text[:100]}


# ── Main filter entry point ──────────────────────────────────────


def filter_candidates(
    issue_title: str,
    issue_body: str,
    min_suff_defs: list[dict[str, Any]],
    thrash_prev_defs: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> FilterResult:
    """Filter thrash_preventing candidates using heuristics + LLM.

    Pipeline:
    1. Heuristic pre-filter (drop obvious noise — free)
    2. Cap candidates to MAX_CANDIDATES_PER_TASK
    3. LLM classification on remaining candidates (~$0.001/candidate)

    Args:
        issue_title: PR/issue title.
        issue_body: Issue body text.
        min_suff_defs: Already-confirmed minimum_sufficient defs.
        thrash_prev_defs: Raw candidate list to filter.
        model: LLM model ID for filtering.
        dry_run: If True, skip LLM calls and keep all heuristic survivors.

    Returns:
        FilterResult with kept/dropped lists and cost accounting.
    """
    t0 = time.monotonic()

    min_suff_paths = {d["path"] for d in min_suff_defs}

    # Convert to dicts if needed
    candidates = [
        d if isinstance(d, dict) else {
            "path": d.path, "name": d.name, "kind": d.kind,
            "start_line": d.start_line, "end_line": d.end_line,
            "reason": d.reason,
        }
        for d in thrash_prev_defs
    ]

    # Step 1: Heuristic pre-filter
    survivors, auto_dropped = _heuristic_prefilter(candidates, min_suff_paths)

    # Step 2: Cap candidates (prioritize same-file, then test, then docs)
    if len(survivors) > MAX_CANDIDATES_PER_TASK:
        # Sort by priority: same-file first, then tests, then docs, then rest
        def _priority(c: dict) -> int:
            p = c.get("path", "")
            if p in min_suff_paths:
                return 0
            if "test" in p.lower():
                return 1
            if any(p.endswith(ext) for ext in (".md", ".rst", ".txt", ".toml", ".yaml")):
                return 2
            return 3

        survivors.sort(key=_priority)
        overflow = survivors[MAX_CANDIDATES_PER_TASK:]
        survivors = survivors[:MAX_CANDIDATES_PER_TASK]
        auto_dropped.extend(overflow)

    # Step 3: LLM classification
    if dry_run or not survivors:
        return FilterResult(
            kept=survivors,
            dropped=auto_dropped,
            elapsed_sec=round(time.monotonic() - t0, 1),
        )

    # Build min_suff summary for context
    min_suff_summary = "\n".join(
        f"- {d['path']}:{d.get('start_line', '?')} ({d.get('kind', '?')}: {d.get('name', '?')})"
        for d in min_suff_defs[:10]
    )

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    llm_calls = 0

    # Batch candidates by file to reduce redundant context
    from itertools import groupby
    survivors_sorted = sorted(survivors, key=lambda c: c.get("path", ""))

    for file_path, file_group in groupby(survivors_sorted, key=lambda c: c.get("path", "")):
        file_candidates = list(file_group)

        # For files with many defs, batch into a single call
        if len(file_candidates) > 5:
            # Ask about the whole file at once
            batch_prompt = _build_batch_filter_prompt(
                issue_title, issue_body, min_suff_summary,
                file_path, file_candidates,
            )
            result = _call_llm(model, _FILTER_SYSTEM_PROMPT, batch_prompt)
            llm_calls += 1

            if result.get("relevant", True):
                kept.extend(file_candidates)
            else:
                dropped.extend(file_candidates)
        else:
            # Individual calls for small groups
            for c in file_candidates:
                prompt = _build_filter_prompt(
                    issue_title, issue_body, min_suff_summary, c,
                )
                result = _call_llm(model, _FILTER_SYSTEM_PROMPT, prompt)
                llm_calls += 1

                if result.get("relevant", True):
                    c["filter_reason"] = result.get("reason", "")
                    kept.append(c)
                else:
                    c["filter_reason"] = result.get("reason", "")
                    dropped.append(c)

    dropped.extend(auto_dropped)

    return FilterResult(
        kept=kept,
        dropped=dropped,
        llm_calls=llm_calls,
        elapsed_sec=round(time.monotonic() - t0, 1),
    )


def _build_batch_filter_prompt(
    issue_title: str,
    issue_body: str,
    min_suff_summary: str,
    file_path: str,
    candidates: list[dict[str, Any]],
) -> str:
    """Build a prompt to evaluate an entire file's relevance at once."""
    defs_list = "\n".join(
        f"  - {c.get('kind', '?')}: {c.get('name', '?')} (line {c.get('start_line', '?')})"
        for c in candidates[:20]
    )

    return f"""\
## Issue
**{issue_title}**
{issue_body[:800]}

## Files being changed (minimum_sufficient)
{min_suff_summary}

## File to evaluate: {file_path}
Contains {len(candidates)} definitions:
{defs_list}

Would a developer need to READ this file to solve the issue above? \
Consider whether the file contains interfaces, types, patterns, docs, \
or configuration that relates to the changed code."""


# ── Heuristic-only fallback ──────────────────────────────────────


def heuristic_filter_only(
    min_suff_defs: list[dict[str, Any]],
    thrash_prev_defs: list[dict[str, Any]],
    max_per_task: int = 30,
) -> FilterResult:
    """Filter using only heuristics (no LLM). Used with --no-filter."""
    min_suff_paths = {d["path"] for d in min_suff_defs}

    candidates = [
        d if isinstance(d, dict) else {
            "path": d.path, "name": d.name, "kind": d.kind,
            "start_line": d.start_line, "end_line": d.end_line,
            "reason": d.reason,
        }
        for d in thrash_prev_defs
    ]

    survivors, dropped = _heuristic_prefilter(candidates, min_suff_paths)

    # Additional heuristic: limit same-file defs to those near changed hunks
    # (within 50 lines of a min_suff def)
    min_suff_ranges: dict[str, list[tuple[int, int]]] = {}
    for d in min_suff_defs:
        p = d["path"]
        if p not in min_suff_ranges:
            min_suff_ranges[p] = []
        min_suff_ranges[p].append((d.get("start_line", 0), d.get("end_line", 0)))

    proximity_kept: list[dict[str, Any]] = []
    for c in survivors:
        path = c.get("path", "")
        if path in min_suff_ranges:
            # Same file as min_suff — keep if within 50 lines of any changed def
            c_start = c.get("start_line", 0)
            c_end = c.get("end_line", 0)
            near = any(
                abs(c_start - ms_end) <= 50 or abs(ms_start - c_end) <= 50
                for ms_start, ms_end in min_suff_ranges[path]
            )
            if near:
                proximity_kept.append(c)
            else:
                dropped.append(c)
        else:
            # Different file (test, doc, config) — keep as-is
            proximity_kept.append(c)

    # Cap total
    if len(proximity_kept) > max_per_task:
        dropped.extend(proximity_kept[max_per_task:])
        proximity_kept = proximity_kept[:max_per_task]

    return FilterResult(kept=proximity_kept, dropped=dropped)
