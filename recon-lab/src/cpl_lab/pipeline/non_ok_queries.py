"""Non-OK query generation — per-repo agentic pipeline stage.

Generates UNSAT, BROAD, and AMBIG queries for each repo via a Copilot SDK
agent session.  The agent explores the repository using its native file,
grep, and terminal tools — no custom tools are registered.

Unlike the per-instance OK query generation (which uses cheap nano calls),
non-OK queries require whole-repo understanding.

Output: ``data/{repo_id}/ground_truth/non_ok_queries.json`` — consumed
by ``collect/merge_ground_truth.py`` as ``{repo_id}__non_ok`` rows.

This stage runs AFTER pr-import (needs the per-instance OK queries as
calibration baseline) and BEFORE collect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import click
from copilot import CopilotClient
from copilot.session import PermissionRequestResult

from cpl_lab.pipeline.clone import clone_dir_for

logger = logging.getLogger(__name__)

# ── Limits ──────────────────────────────────────────────────────

_MIN_UNSAT = 3
_MIN_BROAD = 3
_MIN_AMBIG = 3

# ── System prompt ───────────────────────────────────────────────

_NON_OK_SYSTEM = """\
You are a **non-OK query author** for a code retrieval benchmark.
Your job: write search queries that a code retrieval system CANNOT
serve well — queries where the correct answer is empty (UNSAT),
results scatter across too many modules (BROAD), or the query is
genuinely ambiguous between unrelated subsystems (AMBIG).

You have tools to explore the repository (file listing, grep, file
reading, terminal).  **USE THEM** — do not guess about what exists
or doesn't exist.

## Workflow

1. **Explore** the repo: list directories, read key files, grep for
   patterns.  Build a mental model of the codebase structure.
2. **Draft** candidate queries for each category.
3. **Verify EVERY query** using the tools:
   - UNSAT: grep the repo to CONFIRM the thing does not exist.
     If grep finds it, discard and try again.
   - BROAD: grep for the concept and check how matches distribute
     across top-level directories.  Must hit 4+ directories.
   - AMBIG: confirm the term resolves to 2+ genuinely UNRELATED
     subsystems by reading the relevant code.
4. When all queries are verified, output the final JSON.

## Categories

### UNSAT — the correct answer set is EMPTY

The query asks about something that **does not exist** in this repo.
A developer unfamiliar with this specific codebase might plausibly
ask it, but search would return zero relevant results.

Rules:
- Do NOT reuse any identifier, class, function, or file path that
  actually exists.  Invent plausible-sounding but fictional names,
  or reference technologies/frameworks the repo does not use.
- The assumption must be **plausible** (not trivially absurd).
- VERIFY by grepping the repo before including.

Required fields per query:
  "query_type": "UNSAT",
  "query_text": "...",
  "seeds": [], "pins": [],
  "false_assumption": "what the query assumes that isn't true",
  "evidence_of_absence": "tool results that confirm it doesn't exist"

### BROAD — results scatter with no useful cutoff

The query uses vague, cross-cutting concepts that match code across
many unrelated modules.  No cutoff yields good precision AND recall.

Rules:
- 3-10 words.  Sounds like a real developer question, just unfocused.
- Do NOT use any identifier from the repo — use generic concepts.
- VERIFY by grepping and counting which directories match.

Required fields per query:
  "query_type": "BROAD",
  "query_text": "...",
  "seeds": [], "pins": [],
  "why_no_cutoff": "why no sharp boundary exists",
  "dispersion_description": "which modules/dirs would match"

### AMBIG — resolves to 2+ unrelated subsystems

The query uses a term that genuinely exists in 2-3 UNRELATED parts
of the codebase.  The retrieval system can't tell which one the
developer means.

Rules:
- 3-10 words.  Specific enough to match well, but the term has
  multiple unrelated meanings.
- VERIFY by reading code in the candidate locations.

Required fields per query:
  "query_type": "AMBIG",
  "query_text": "...",
  "seeds": [], "pins": [],
  "candidate_neighborhoods": "which 2-3 subsystems match",
  "why_ambiguous": "why the query is genuinely ambiguous"

## Quality bar

- Minimum {min_unsat} UNSAT + {min_broad} BROAD + {min_ambig} AMBIG.
- More is better IF genuinely distinct.  Don't pad with variants.
- Every query MUST be verified with tools before inclusion.

## Output format

When done, respond with ONLY a JSON object (no markdown fences):
{{
  "non_ok_queries": [ ... ]
}}
"""

# ── User prompt template ────────────────────────────────────────

_NON_OK_USER = """\
## Repository: {repo_id}

### Sample OK queries (for calibration — these queries WORK well)
{ok_samples}

---

Generate non-OK queries for this repository.
Start by exploring the repo structure, then draft and verify each
query before outputting the final JSON.
"""


# ── Permission handler (auto-approve built-in tools) ───────────


def _auto_approve(_request: Any, _context: dict[str, str]) -> PermissionRequestResult:
    return PermissionRequestResult(kind="approved")


# ── Copilot SDK session runner ──────────────────────────────────

_SESSION_TIMEOUT = 600.0  # seconds
_NUDGE_TIMEOUT = 120.0  # extra time for nudge after initial timeout
_MAX_NUDGES = 2  # max follow-up nudges before giving up


def _on_session_event(event: Any) -> None:
    """Log interesting session events for visibility."""
    etype = getattr(event, "type", None)
    ename = getattr(etype, "value", str(etype)) if etype else "?"
    if ename in ("tool.execution_start", "tool.execution_complete"):
        data = event.data
        tool_name = getattr(data, "tool_name", None) or ""
        logger.debug("  %s: %s", ename, tool_name)
    elif ename == "assistant.message":
        content = getattr(event.data, "content", "") or ""
        logger.debug("  assistant message (%d chars)", len(content))


_NUDGE_PROMPT = (
    "Time is up. Output the final JSON now with whatever queries you have "
    "verified so far. Respond with ONLY the JSON object, no other text."
)


async def _run_copilot_session(
    system_prompt: str,
    user_prompt: str,
    working_directory: str,
) -> str:
    """Run a Copilot SDK agent session and return the final assistant text."""
    client = CopilotClient()
    try:
        await client.start()

        session = await client.create_session(
            on_permission_request=_auto_approve,
            system_message={"mode": "append", "content": system_prompt},
            working_directory=working_directory,
            streaming=False,
            on_event=_on_session_event,
        )
        try:
            event = await session.send_and_wait(
                user_prompt, timeout=_SESSION_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                "Session timed out after %.0fs, will nudge for JSON",
                _SESSION_TIMEOUT,
            )
            event = await _last_assistant_message(session)

        # If the response isn't JSON, nudge the agent to just output it
        content = _extract_content(event)
        if content and not _looks_like_json(content):
            for attempt in range(1, _MAX_NUDGES + 1):
                logger.info("Nudge %d/%d — asking agent to output JSON", attempt, _MAX_NUDGES)
                try:
                    event = await session.send_and_wait(
                        _NUDGE_PROMPT, timeout=_NUDGE_TIMEOUT,
                    )
                except TimeoutError:
                    logger.warning("Nudge %d timed out, extracting last content", attempt)
                    event = await _last_assistant_message(session)
                content = _extract_content(event)
                if content and _looks_like_json(content):
                    break

        await session.destroy()
    finally:
        await client.stop()

    if not content:
        raise RuntimeError("Copilot session returned no response")

    return content


def _extract_content(event: Any) -> str | None:
    """Safely extract text content from a session event."""
    if event is None:
        return None
    c = getattr(getattr(event, "data", None), "content", None)
    return c if c else None


def _looks_like_json(text: str) -> bool:
    """Quick check whether text contains a JSON object with non_ok_queries."""
    stripped = text.strip()
    if stripped.startswith("{") and "non_ok_queries" in stripped:
        return True
    # Also handle markdown-fenced JSON
    if "non_ok_queries" in stripped and "{" in stripped:
        return True
    return False


async def _last_assistant_message(session: Any) -> Any | None:
    """Walk messages in reverse to find the last assistant message with content."""
    messages = await session.get_messages()
    for msg in reversed(messages):
        etype = getattr(msg, "type", None)
        ename = getattr(etype, "value", "") if etype else ""
        if ename == "assistant.message":
            content = getattr(msg.data, "content", None)
            if content:
                return msg
    return None


# ── OK-query sample collector ───────────────────────────────────


def _collect_ok_samples(
    data_dir: Path, repo_id: str, max_samples: int = 12,
) -> str:
    """Gather sample OK queries from completed pr-import instances."""
    samples: list[str] = []
    repo_prefix = f"{repo_id}_"

    for inst_dir in sorted(data_dir.iterdir()):
        if not inst_dir.name.startswith(repo_prefix):
            continue
        gt_dir = inst_dir / "ground_truth"
        task_json = gt_dir / f"{inst_dir.name}.json"
        if not task_json.exists():
            continue
        try:
            task = json.loads(task_json.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for q in task.get("queries", []):
            qt = q.get("query_type", "")
            qtext = q.get("query_text", "")
            if qt and qtext:
                samples.append(f"  [{qt}] {qtext[:120]}")
        if len(samples) >= max_samples:
            break

    if not samples:
        return "(no OK query samples available yet)"
    return "\n".join(samples[:max_samples])


# ── Per-repo generation ─────────────────────────────────────────


async def generate_non_ok_for_repo(
    repo_id: str,
    data_dir: Path,
    clones_dir: Path,
    llm_model: str,
) -> dict[str, Any]:
    """Generate non-OK queries for a single repo via agentic session.

    Runs an agentic session where the LLM explores the repo using its
    native tools, drafts queries, verifies them, and returns the final JSON.
    """
    main_dir = clone_dir_for(repo_id, clones_dir)
    if main_dir is None:
        raise click.ClickException(f"Unknown repo_id: {repo_id}")

    ok_samples = _collect_ok_samples(data_dir, repo_id)

    system = _NON_OK_SYSTEM.format(
        min_unsat=_MIN_UNSAT,
        min_broad=_MIN_BROAD,
        min_ambig=_MIN_AMBIG,
    )
    user = _NON_OK_USER.format(
        repo_id=repo_id,
        ok_samples=ok_samples,
    )

    content = await _run_copilot_session(
        system_prompt=system,
        user_prompt=user,
        working_directory=str(main_dir),
    )

    # Parse JSON from the final content
    result = _parse_json_object(content)
    queries = result.get("non_ok_queries", [])
    if not isinstance(queries, list):
        queries = []

    # Validate and normalize
    validated: list[dict[str, Any]] = []
    for q in queries:
        qt = q.get("query_type", "")
        qtext = q.get("query_text", "")
        if qt not in ("UNSAT", "BROAD", "AMBIG"):
            logger.warning("Skipping query with invalid type %r", qt)
            continue
        if not qtext or not isinstance(qtext, str):
            logger.warning("Skipping query with empty text")
            continue
        q.setdefault("seeds", [])
        q.setdefault("pins", [])
        validated.append(q)

    counts: dict[str, int] = {}
    for q in validated:
        counts[q["query_type"]] = counts.get(q["query_type"], 0) + 1

    logger.info(
        "Repo %s: generated %d non-OK queries (UNSAT=%d, BROAD=%d, AMBIG=%d)",
        repo_id,
        len(validated),
        counts.get("UNSAT", 0),
        counts.get("BROAD", 0),
        counts.get("AMBIG", 0),
    )

    return {
        "repo_id": repo_id,
        "model": llm_model,
        "non_ok_queries": validated,
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    """Extract and parse a JSON object from LLM output."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # Find outermost { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"Failed to parse JSON from agent response: {text[:300]}")


# ── Orchestrator ────────────────────────────────────────────────


def run_non_ok_queries(
    data_dir: Path,
    clones_dir: Path,
    llm_model: str = "openai/gpt-4.1-mini",
    repo_set: str = "all",
    repo: str | None = None,
    force: bool = False,
    verbose: bool = False,
    workers: int = 1,
) -> None:
    """Generate non-OK queries for all repos (or a single repo).

    Runs one agentic LLM session per repo using a capable model that
    can explore the repository index with tools.  Output is written to
    ``data/{repo_id}/ground_truth/non_ok_queries.json``.
    """
    from cpl_lab.pipeline.clone import REPO_MANIFEST

    if repo:
        repos = [repo]
    else:
        repos = [
            rid
            for rid, entry in REPO_MANIFEST.items()
            if repo_set == "all" or entry["set"] == repo_set
        ]

    # Filter to repos that have completed pr-import instances
    active_repos: list[str] = []
    for rid in repos:
        has_instances = any(
            d.name.startswith(f"{rid}_")
            and (d / "ground_truth").is_dir()
            for d in data_dir.iterdir()
            if d.is_dir()
        )
        if not has_instances:
            if verbose:
                click.echo(f"  Skipping {rid}: no pr-import data yet")
            continue
        active_repos.append(rid)

    if not active_repos:
        click.echo("No repos with pr-import data found.")
        return

    # Skip repos that already have non_ok_queries.json (unless --force)
    todo: list[str] = []
    for rid in active_repos:
        out_path = _non_ok_output_path(data_dir, rid)
        if out_path.exists() and not force:
            if verbose:
                click.echo(
                    f"  Skipping {rid}: non_ok_queries.json exists "
                    f"(use --force to overwrite)"
                )
            continue
        todo.append(rid)

    click.echo(
        f"Generating non-OK queries for {len(todo)} repos "
        f"({len(active_repos) - len(todo)} already done), "
        f"model={llm_model}, timeout={_SESSION_TIMEOUT}s, "
        f"workers={workers}"
    )

    asyncio.run(_run_all(todo, data_dir, clones_dir, llm_model, workers))


async def _run_all(
    todo: list[str],
    data_dir: Path,
    clones_dir: Path,
    llm_model: str,
    workers: int,
) -> None:
    """Run non-OK generation for all repos with bounded concurrency."""
    sem = asyncio.Semaphore(workers)
    ok = 0
    errors = 0
    completed = 0
    t0 = time.monotonic()

    async def _process(rid: str) -> None:
        nonlocal ok, errors, completed
        async with sem:
            repo_t0 = time.monotonic()
            try:
                result = await generate_non_ok_for_repo(
                    rid, data_dir, clones_dir, llm_model,
                )

                out_path = _non_ok_output_path(data_dir, rid)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(result, indent=2, ensure_ascii=False),
                )

                counts: dict[str, int] = {}
                for q in result["non_ok_queries"]:
                    counts[q["query_type"]] = counts.get(q["query_type"], 0) + 1

                elapsed_repo = time.monotonic() - repo_t0
                completed += 1
                click.echo(
                    f"  [{completed}/{len(todo)}] {rid}: "
                    f"{len(result['non_ok_queries'])} queries "
                    f"(U={counts.get('UNSAT', 0)} "
                    f"B={counts.get('BROAD', 0)} "
                    f"A={counts.get('AMBIG', 0)}) "
                    f"[{elapsed_repo:.0f}s]"
                )
                ok += 1
            except Exception as exc:
                elapsed_repo = time.monotonic() - repo_t0
                completed += 1
                click.echo(
                    f"  [{completed}/{len(todo)}] {rid}: ERROR [{elapsed_repo:.0f}s] — {exc}"
                )
                logger.exception("Failed to generate non-OK queries for %s", rid)
                errors += 1

    tasks = [asyncio.create_task(_process(rid)) for rid in todo]
    await asyncio.gather(*tasks)

    elapsed = time.monotonic() - t0
    click.echo(f"\nDone in {elapsed:.0f}s: {ok} ok, {errors} errors")


def _non_ok_output_path(data_dir: Path, repo_id: str) -> Path:
    """Canonical output path for a repo's non-OK queries.

    The collect/merge stage reads from
    ``data/{repo_id}/ground_truth/non_ok_queries.json``.
    """
    return data_dir / repo_id / "ground_truth" / "non_ok_queries.json"
