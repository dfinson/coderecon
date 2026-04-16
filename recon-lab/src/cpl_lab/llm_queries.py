"""LLM-backed adaptation for SWE-bench instances.

Each LLM call does exactly ONE thing.  gpt-4.1-mini is cheap but not
smart — we never ask it to juggle multiple output types in one shot.

Call decomposition:
  1. ``_classify_task``          — complexity + confidence + notes  (1 call)
  2. ``_generate_ok_query``      — one query for one type           (8 calls)
  3. ``_generate_unsat_query``   — generate + validate pair         (2×2 = 4+ calls)
  4. ``_generate_broad_query``   — LLM generate + RRF validate      (2×1+ calls)
  5. ``_generate_ambig_query``   — LLM generate + RRF validate      (2×1+ calls)
Total: 17+ calls per instance.  Non-OK types retry until validated.

BROAD/AMBIG validation runs the generated query through the in-process
RRF pipeline (harvesters + RRF fusion, no trained models) and checks
post-hoc statistics against a *calibration baseline* derived from the 8
OK queries for this instance.  No hardcoded thresholds — "scattered"
means more scattered than any OK query; "split" means more balanced
than any OK query.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import urllib.request
from collections import Counter
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
    queries: list[dict[str, Any]]
    non_ok_queries: list[dict[str, Any]]


# ── Index fact extraction ────────────────────────────────────────


def _extract_index_facts(index_db: Path) -> str:
    """Extract deterministic facts from the coderecon index.

    Returns a plain-text block listing: top-level directories, languages,
    definition names by kind, and external dependencies.  Used as the
    grounding set for UNSAT query generation and validation.
    """
    con = sqlite3.connect(str(index_db))
    cur = con.cursor()

    # Top-level directories (first path component)
    rows = cur.execute(
        "SELECT DISTINCT SUBSTR(path, 1, INSTR(path, '/') - 1) "
        "FROM files WHERE INSTR(path, '/') > 0"
    ).fetchall()
    top_dirs = sorted({r[0] for r in rows if r[0]})

    # Languages
    rows = cur.execute(
        "SELECT language_family, COUNT(*) FROM files "
        "GROUP BY language_family ORDER BY COUNT(*) DESC"
    ).fetchall()
    languages = [(r[0], r[1]) for r in rows if r[0]]

    # Class names (sample up to 50)
    rows = cur.execute(
        "SELECT DISTINCT name FROM def_facts WHERE kind = 'class' "
        "ORDER BY name LIMIT 50"
    ).fetchall()
    classes = [r[0] for r in rows]

    # Function/method names (sample up to 50)
    rows = cur.execute(
        "SELECT DISTINCT name FROM def_facts WHERE kind IN ('function', 'method') "
        "ORDER BY name LIMIT 50"
    ).fetchall()
    functions = [r[0] for r in rows]

    # External dependencies (top imports by frequency)
    rows = cur.execute(
        "SELECT source_literal, COUNT(*) FROM import_facts "
        "WHERE resolved_path IS NULL OR resolved_path = '' "
        "GROUP BY source_literal ORDER BY COUNT(*) DESC LIMIT 30"
    ).fetchall()
    deps = [r[0] for r in rows if r[0]]

    con.close()

    parts = []
    if top_dirs:
        parts.append(f"Top-level directories: {', '.join(top_dirs)}")
    if languages:
        lang_str = ", ".join(f"{lang} ({count} files)" for lang, count in languages)
        parts.append(f"Languages: {lang_str}")
    if classes:
        parts.append(f"Classes: {', '.join(classes)}")
    if functions:
        parts.append(f"Functions/methods: {', '.join(functions)}")
    if deps:
        parts.append(f"External dependencies: {', '.join(deps)}")

    return "\n".join(parts)


# ── Public entry point ───────────────────────────────────────────


def adapt_instance(
    *,
    model: str,
    instance_id: str,
    repo: str,
    problem_statement: str,
    hints_text: str,
    patch_text: str,
    index_db: Path,
    clone_dir: Path,
    minimum_sufficient_defs: list[dict[str, Any]] | None = None,
    prior_broad: list[str] | None = None,
    prior_ambig: list[str] | None = None,
) -> AdaptationResult:
    """Produce all GT fields for one instance via focused single-purpose LLM calls.

    17+ calls total:
      1 classify  +  8 OK queries  +  2 UNSAT (generate+validate pairs)
      +  2 BROAD (LLM generate + RRF validate)  +  2 AMBIG (LLM + RRF)
    Every LLM call produces exactly one output.  Non-OK types retry until validated.

    BROAD/AMBIG validation constructs an in-process AppContext from *clone_dir*
    and runs the query through the RRF pipeline (harvesters + RRF fusion, no
    trained models) to verify the retrieval output actually exhibits the
    expected scattering (BROAD) or multi-cluster split (AMBIG).

    *prior_broad* / *prior_ambig* are lists of previously generated query
    texts for this repo (across other instances).  Passed to the LLM as
    anti-examples to encourage diversity — the same 12 repos generate
    thousands of instances, so without this the LLM recycles the same
    cross-cutting concepts.
    """
    from coderecon.mcp.context import AppContext

    prior_broad = prior_broad or []
    prior_ambig = prior_ambig or []

    # Shared context block (reused across all calls)
    context = _build_context(
        instance_id=instance_id,
        repo=repo,
        problem_statement=problem_statement,
        hints_text=hints_text,
        patch_text=patch_text,
        minimum_sufficient_defs=minimum_sufficient_defs,
    )

    # Call 1: classify task
    classification = _classify_task(model, context)

    # Calls 2-9: one OK query per type
    ok_queries: list[dict[str, Any]] = []
    for query_type in OK_QUERY_TYPES:
        q = _generate_ok_query(model, context, query_type)
        ok_queries.append(q)

    # Extract index facts once — used for UNSAT / BROAD / AMBIG generation
    index_facts = _extract_index_facts(index_db)

    # Build in-process AppContext for BROAD/AMBIG RRF validation.
    # Cheap — just opens the existing SQLite + Tantivy, no re-indexing.
    recon_dir = clone_dir / ".recon"
    app_ctx = AppContext.standalone(
        repo_root=clone_dir,
        db_path=recon_dir / "index.db",
        tantivy_path=recon_dir / "tantivy",
    )
    asyncio.run(app_ctx.coordinator.load_existing())

    # Calibrate: run OK queries through RRF to establish what "focused"
    # looks like on this repo.  BROAD/AMBIG gates use this as baseline.
    baseline = _calibrate_ok_baseline(ok_queries, app_ctx)

    non_ok_queries: list[dict[str, Any]] = []
    for variant in (1, 2):
        # UNSAT — LLM fact-negation + LLM fact-checker
        q = _generate_unsat_query(model, context, index_facts, variant)
        non_ok_queries.append(q)

    for variant in (1, 2):
        # BROAD — LLM generate + RRF validate (must exceed OK baseline)
        q = _generate_broad_query(
            model, context, index_facts, variant, app_ctx, baseline,
            prior_queries=prior_broad,
        )
        non_ok_queries.append(q)
        prior_broad.append(q["query_text"])

    for variant in (1, 2):
        # AMBIG — LLM generate + RRF validate (must show balanced split beyond OK)
        q = _generate_ambig_query(
            model, context, index_facts, variant, app_ctx, baseline,
            prior_queries=prior_ambig,
        )
        non_ok_queries.append(q)
        prior_ambig.append(q["query_text"])

    return AdaptationResult(
        task_complexity=classification["task_complexity"],
        confidence=classification["confidence"],
        solve_notes=classification["solve_notes"],
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
    return "\n".join(parts)


# ── Call 1: Classify task ────────────────────────────────────────


_CLASSIFY_SYSTEM = """\
You classify a GitHub issue for a code retrieval benchmark.
Return ONLY a JSON object with exactly these three fields:
{
  "task_complexity": "narrow" or "medium" or "wide",
  "confidence": "high" or "medium" or "low",
  "solve_notes": "One sentence summarising what the fix does."
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


# ── UNSAT query generation (fact-negation + validate) ─────────────


_UNSAT_GENERATE_SYSTEM = """\
You write ONE search query for a code retrieval benchmark.
The query must describe a task that depends on the OPPOSITE of one or more
of the provided facts being true.

Return ONLY a JSON object with exactly these fields:
{
  "query_text": "the search query",
  "false_assumption": "which fact you negated to construct this query"
}

Rules:
- Pick one or more facts from the provided list, invent the opposite.
- The query should sound like a realistic developer search — something someone
  would actually type into a code search tool.
- query_text must be 3-15 words.
- Do NOT mention that you are negating anything. Just write the query naturally.
"""

_UNSAT_VALIDATE_SYSTEM = """\
You are a fact checker. You will receive a query and a set of facts.
Determine whether the query can be answered given ONLY these facts.

Return ONLY a JSON object:
{
  "answerable": true or false,
  "reason": "one sentence explaining why"
}

Rules:
- If ANY fact supports answering the query, return {"answerable": true, ...}.
- If NO fact supports answering the query, return {"answerable": false, ...}.
- Consider only the facts provided. Nothing else.
"""


def _generate_unsat_query(
    model: str,
    context: str,
    index_facts: str,
    variant: int,
    *,
    max_retries: int = 10,
) -> dict[str, Any]:
    """Generate one UNSAT query using fact-negation + LLM validation.

    1. Generate: LLM sees index facts, writes a query that depends on
       the opposite of those facts.
    2. Validate: second LLM call checks the query against the same facts.
       Pure logic — "can this query be answered from these facts?"
    3. Loop until the validator confirms the query is unanswerable.
    """
    for attempt in range(1, max_retries + 1):
        # ── Generate ──
        generate_prompt = (
            f"{context}\n\n"
            f"---\n\n"
            f"## Known facts about this codebase\n"
            f"{index_facts}\n\n"
            f"---\n\n"
            f"Write ONE search query (variant {variant}) that describes a task "
            f"depending on the opposite of one or more of these facts being true.\n"
        )
        if attempt > 1:
            generate_prompt += (
                f"\n(Attempt {attempt} — previous queries were answerable from "
                f"the facts. Try negating a DIFFERENT fact.)\n"
            )

        gen_result = _call_llm_json(
            model=model,
            system_prompt=_UNSAT_GENERATE_SYSTEM,
            user_prompt=generate_prompt,
            max_tokens=200,
        )
        query_text = gen_result.get("query_text", "")
        if not isinstance(query_text, str) or not query_text.strip():
            logger.warning("Empty UNSAT query_text, variant %d attempt %d", variant, attempt)
            continue

        # ── Validate ──
        validate_prompt = (
            f"Query: {query_text}\n\n"
            f"Facts:\n{index_facts}\n\n"
            f"Can this query be answered given ONLY these facts?"
        )
        val_result = _call_llm_json(
            model=model,
            system_prompt=_UNSAT_VALIDATE_SYSTEM,
            user_prompt=validate_prompt,
            max_tokens=100,
        )
        answerable = val_result.get("answerable")
        if answerable is False:
            return {
                "query_type": "UNSAT",
                "query_text": query_text,
                "seeds": [],
                "pins": [],
                "false_assumption": gen_result.get("false_assumption", ""),
                "evidence_of_absence": val_result.get("reason", ""),
            }

        logger.warning(
            "UNSAT variant %d attempt %d: validator said answerable (%s) query=%r",
            variant, attempt, val_result.get("reason", ""), query_text,
        )

    raise RuntimeError(
        f"Failed to generate a validated UNSAT query (variant {variant}) "
        f"after {max_retries} attempts"
    )


# ── BROAD / AMBIG generation (LLM generate + RRF validate) ──────
#
# No hardcoded retrieval thresholds.  Instead, the 8 OK queries are run
# through RRF first to establish what "focused" looks like for THIS repo
# at THIS commit.  BROAD must exceed ALL OK queries on dir_count AND
# elbow-n.  AMBIG must exceed ALL OK queries on balance while staying
# within the OK dir_count range.
#
# The only structural constants are methodology choices, not thresholds:

# Path prefix depth for clustering — "django/db", "tests/forms".
# 1 level is too coarse for monorepos (everything under one top-level
# package); 3 is too fine (splits real modules into sub-modules).
_CLUSTER_PATH_DEPTH = 2

# Word-count bounds on generated query text — prompt-format constraint,
# not a retrieval threshold.  Min 3 prevents single-keyword queries;
# max 10 prevents paragraph-length queries.
_NON_OK_MIN_WORDS = 3
_NON_OK_MAX_WORDS = 10


@dataclass(frozen=True)
class OkBaseline:
    """RRF statistics from the 8 OK queries — the calibration baseline.

    Every field is the *maximum* observed across OK queries.  A non-OK
    query must exceed these maxima on the relevant dimension to prove it
    behaves differently from any focused query.
    """
    max_dir_count: int
    max_elbow_n: int
    max_balance: float

    #: Minimum dir_count where at least one OK query had >=2 clusters.
    #  AMBIG must have dir_count >= 2 (definitional — ambiguity requires
    #  at least two subsystems) but capped at max_dir_count so it doesn't
    #  leak into BROAD territory.
    min_multi_dir_count: int = 2


async def _rrf_stats(app_ctx: Any, query_text: str) -> dict[str, Any]:
    """Run a query through the in-process RRF pipeline and return post-hoc stats.

    No trained models involved — just harvesters + RRF fusion + elbow.
    Returns dir_count, n, spread, cluster_sizes, balance for gate decisions.

    Analysis window matches ``elbow_cut(max_n=30)`` — the full pre-elbow
    candidate pool.
    """
    from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline
    from coderecon.ranking.elbow import elbow_cut
    from coderecon.ranking.rrf import rrf_fuse

    raw = await raw_signals_pipeline(app_ctx, query_text, seeds=None, pins=None)
    candidates = rrf_fuse(raw.get("candidates", []))

    # Analysis window = elbow_cut's max_n.
    top = candidates[:30]
    scores = [c["rrf_score"] for c in top]
    n = elbow_cut(scores) if scores else 0

    dir_counter: Counter[str] = Counter()
    for c in top:
        parts = c["path"].split("/")
        prefix = "/".join(parts[:_CLUSTER_PATH_DEPTH]) if len(parts) >= _CLUSTER_PATH_DEPTH else parts[0]
        dir_counter[prefix] += 1

    cluster_sizes = sorted(dir_counter.values(), reverse=True)
    balance = cluster_sizes[1] / cluster_sizes[0] if len(cluster_sizes) >= 2 else 0.0

    return {
        "scored_total": len(candidates),
        "n": n,
        "dir_count": len(dir_counter),
        "spread": scores[0] - scores[-1] if len(scores) >= 2 else 0.0,
        "cluster_sizes": cluster_sizes,
        "balance": balance,
    }


def _calibrate_ok_baseline(
    ok_queries: list[dict[str, Any]],
    app_ctx: Any,
) -> OkBaseline:
    """Run all OK queries through RRF and compute the calibration baseline.

    This defines "what focused retrieval looks like" for this repo.
    Every dimension is the max across all OK queries — non-OK queries
    must exceed these to qualify.
    """
    dir_counts: list[int] = []
    elbow_ns: list[int] = []
    balances: list[float] = []

    for q in ok_queries:
        seeds = q.get("seeds", []) or []
        pins = q.get("pins", []) or []
        # Run with actual seeds/pins so the baseline reflects real usage
        stats = asyncio.run(_rrf_stats_with_hints(app_ctx, q["query_text"], seeds, pins))
        dir_counts.append(stats["dir_count"])
        elbow_ns.append(stats["n"])
        balances.append(stats["balance"])

    baseline = OkBaseline(
        max_dir_count=max(dir_counts),
        max_elbow_n=max(elbow_ns),
        max_balance=max(balances),
    )
    logger.info(
        "OK baseline: max_dir_count=%d, max_elbow_n=%d, max_balance=%.2f",
        baseline.max_dir_count, baseline.max_elbow_n, baseline.max_balance,
    )
    return baseline


async def _rrf_stats_with_hints(
    app_ctx: Any,
    query_text: str,
    seeds: list[str] | None = None,
    pins: list[str] | None = None,
) -> dict[str, Any]:
    """Like _rrf_stats but accepts seeds/pins for OK query calibration."""
    from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline
    from coderecon.ranking.elbow import elbow_cut
    from coderecon.ranking.rrf import rrf_fuse

    raw = await raw_signals_pipeline(app_ctx, query_text, seeds=seeds, pins=pins)
    candidates = rrf_fuse(raw.get("candidates", []))

    top = candidates[:30]
    scores = [c["rrf_score"] for c in top]
    n = elbow_cut(scores) if scores else 0

    dir_counter: Counter[str] = Counter()
    for c in top:
        parts = c["path"].split("/")
        prefix = "/".join(parts[:_CLUSTER_PATH_DEPTH]) if len(parts) >= _CLUSTER_PATH_DEPTH else parts[0]
        dir_counter[prefix] += 1

    cluster_sizes = sorted(dir_counter.values(), reverse=True)
    balance = cluster_sizes[1] / cluster_sizes[0] if len(cluster_sizes) >= 2 else 0.0

    return {
        "scored_total": len(candidates),
        "n": n,
        "dir_count": len(dir_counter),
        "spread": scores[0] - scores[-1] if len(scores) >= 2 else 0.0,
        "cluster_sizes": cluster_sizes,
        "balance": balance,
    }


def _check_broad(stats: dict[str, Any], baseline: OkBaseline) -> str | None:
    """BROAD gate — must be more scattered than every OK query.

    Two separation conditions (both required):
      - dir_count exceeds the max seen in ANY OK query
      - elbow-n exceeds the max seen in ANY OK query
    If either fails, the query isn't distinguishably more scattered
    than a focused query on this repo.
    """
    if stats["dir_count"] <= baseline.max_dir_count:
        return (
            f"dir_count {stats['dir_count']} is within OK range "
            f"(OK max={baseline.max_dir_count})"
        )
    if stats["n"] <= baseline.max_elbow_n:
        return (
            f"elbow n={stats['n']} is within OK range "
            f"(OK max={baseline.max_elbow_n})"
        )
    return None


def _check_ambig(stats: dict[str, Any], baseline: OkBaseline) -> str | None:
    """AMBIG gate — must show a more balanced subsystem split than any OK query.

    Conditions:
      - dir_count >= 2 (definitional — ambiguity requires >= 2 subsystems)
      - dir_count <= baseline.max_dir_count (above that is BROAD territory)
      - balance exceeds the max seen in ANY OK query (the split is more
        even than any focused query produces)
      - At least 2 clusters have >= 2 results each (stray single-result
        hits in a second dir don't count — that's noise, not a real
        competing interpretation)
    """
    if stats["dir_count"] < 2:
        return "single subsystem — ambiguity requires >= 2"
    if stats["dir_count"] > baseline.max_dir_count:
        return (
            f"dir_count {stats['dir_count']} exceeds OK max "
            f"({baseline.max_dir_count}) — that's BROAD, not AMBIG"
        )
    if stats["balance"] <= baseline.max_balance:
        return (
            f"balance {stats['balance']:.2f} is within OK range "
            f"(OK max={baseline.max_balance:.2f})"
        )
    # Structural: need >= 2 clusters with >= 2 results each.
    # This is the definition of "two real competing interpretations",
    # not a tuned threshold.
    substantial = [s for s in stats["cluster_sizes"] if s >= 2]
    if len(substantial) < 2:
        return "need >= 2 clusters with >= 2 results each"
    return None


_BROAD_GENERATE_SYSTEM = """\
You write ONE search query for a code retrieval benchmark.
The query must be too vague — it uses general cross-cutting concepts
(error handling, configuration, validation, serialization, logging, etc.)
that would match code across many unrelated modules.

Return ONLY a JSON object with exactly these fields:
{
  "query_text": "the vague search query"
}

Rules:
- query_text must be 3-10 words.
- Sound like a real developer question, just too unfocused.
- Do NOT use any identifier, class name, function name, or file path
  from the issue or patch.
- Use cross-cutting concerns that would scatter across the entire codebase.
"""

_AMBIG_GENERATE_SYSTEM = """\
You write ONE search query for a code retrieval benchmark.
The query must use a term or concept that exists in 2-3 UNRELATED
subsystems of this codebase — e.g. the same function name in both the ORM
and the template engine, or "validation" meaning both form validation
and schema validation.

Return ONLY a JSON object with exactly these fields:
{
  "query_text": "the ambiguous search query"
}

Rules:
- query_text must be 3-10 words.
- The query should be specific enough to match well, but the term
  genuinely resolves to multiple unrelated code areas.
- Do NOT use any identifier from the issue or patch.
- Check the provided facts for terms/concepts that appear in multiple
  unrelated subsystems.
"""


def _generate_broad_query(
    model: str,
    context: str,
    index_facts: str,
    variant: int,
    app_ctx: Any,
    baseline: OkBaseline,
    *,
    prior_queries: list[str] | None = None,
    max_retries: int = 5,
) -> dict[str, Any]:
    """Generate one BROAD query using LLM generation + RRF validation.

    1. LLM generates a vague cross-cutting query grounded on index facts.
    2. Query is run through the in-process RRF pipeline.
    3. Post-hoc stats must exceed the OK baseline on dir_count AND elbow-n.
    Retries with concrete feedback on failure.

    *prior_queries* lists BROAD queries already generated for this repo
    (across instances).  Shown to the LLM as anti-examples.
    """
    last_failure = ""
    last_query_text = f"broad cross-cutting query variant {variant}"
    last_stats: dict[str, Any] = {"dir_count": 0, "n": 0, "spread": 0.0, "balance": 0.0, "cluster_sizes": []}

    for attempt in range(1, max_retries + 1):
        user_prompt = (
            f"{context}\n\n"
            f"---\n\n"
            f"## Known facts about this codebase\n"
            f"{index_facts}\n\n"
            f"---\n\n"
            f"Write ONE vague, cross-cutting search query (variant {variant}) "
            f"that would scatter results across many modules.\n"
        )
        if prior_queries:
            recent = prior_queries[-20:]
            anti = "\n".join(f"  - {q}" for q in recent)
            user_prompt += (
                f"\n## Previously generated BROAD queries for this repo\n"
                f"Do NOT repeat or paraphrase any of these — use a DIFFERENT "
                f"cross-cutting concept:\n{anti}\n"
            )
        if attempt > 1 and last_failure:
            user_prompt += (
                f"\n(Attempt {attempt} — previous query was rejected: {last_failure}. "
                f"Use a MORE cross-cutting concept — something every module deals with.)\n"
            )

        gen_result = _call_llm_json(
            model=model,
            system_prompt=_BROAD_GENERATE_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=200,
        )
        query_text = gen_result.get("query_text", "")
        if not isinstance(query_text, str) or not query_text.strip():
            logger.warning("Empty BROAD query_text, variant %d attempt %d", variant, attempt)
            continue

        # Word count pre-check (cheap, avoids RRF call)
        word_count = len(query_text.split())
        if word_count < _NON_OK_MIN_WORDS or word_count > _NON_OK_MAX_WORDS:
            last_failure = f"word count {word_count} (need {_NON_OK_MIN_WORDS}-{_NON_OK_MAX_WORDS})"
            logger.warning("BROAD variant %d attempt %d: %s", variant, attempt, last_failure)
            continue

        # RRF validation against OK baseline
        stats = asyncio.run(_rrf_stats(app_ctx, query_text))
        failure = _check_broad(stats, baseline)
        if failure is None:
            return {
                "query_type": "BROAD",
                "query_text": query_text,
                "seeds": [],
                "pins": [],
                "why_no_cutoff": (
                    f"Elbow n={stats['n']} exceeds OK max "
                    f"({baseline.max_elbow_n}) — no sharp score drop."
                ),
                "dispersion_description": (
                    f"dir_count={stats['dir_count']} exceeds OK max "
                    f"({baseline.max_dir_count}). Results scatter across "
                    f"{stats['dir_count']} directory clusters."
                ),
                "rrf_stats": {
                    "dir_count": stats["dir_count"],
                    "n": stats["n"],
                    "spread": round(stats["spread"], 4),
                },
                "ok_baseline": {
                    "max_dir_count": baseline.max_dir_count,
                    "max_elbow_n": baseline.max_elbow_n,
                },
            }

        last_failure = failure
        last_query_text = query_text
        last_stats = stats
        logger.warning(
            "BROAD variant %d attempt %d: RRF rejected — %s (query=%r, stats=%s)",
            variant, attempt, failure, query_text,
            {k: v for k, v in stats.items() if k != "cluster_sizes"},
        )

    # Exhausted retries — use the last attempt as a best-effort fallback
    # rather than discarding the entire instance.
    logger.warning(
        "BROAD variant %d: exhausted %d retries, using last attempt as fallback",
        variant, max_retries,
    )
    return {
        "query_type": "BROAD",
        "query_text": last_query_text,
        "seeds": [],
        "pins": [],
        "why_no_cutoff": f"FALLBACK — validation failed after {max_retries} attempts: {last_failure}",
        "dispersion_description": f"dir_count={last_stats['dir_count']}, n={last_stats['n']} (did not exceed baseline)",
        "rrf_stats": {
            "dir_count": last_stats["dir_count"],
            "n": last_stats["n"],
            "spread": round(last_stats["spread"], 4),
        },
        "ok_baseline": {
            "max_dir_count": baseline.max_dir_count,
            "max_elbow_n": baseline.max_elbow_n,
        },
        "fallback": True,
    }


def _generate_ambig_query(
    model: str,
    context: str,
    index_facts: str,
    variant: int,
    app_ctx: Any,
    baseline: OkBaseline,
    *,
    prior_queries: list[str] | None = None,
    max_retries: int = 5,
) -> dict[str, Any]:
    """Generate one AMBIG query using LLM generation + RRF validation.

    1. LLM generates a query using a term that spans 2-3 subsystems.
    2. Query is run through the in-process RRF pipeline.
    3. Post-hoc stats must show a more balanced split than any OK query:
       dir_count in [2, ok_max_dir_count], balance > ok_max_balance,
       >= 2 substantial clusters.
    Retries with concrete feedback on failure.

    *prior_queries* lists AMBIG queries already generated for this repo
    (across instances).  Shown to the LLM as anti-examples.
    """
    last_failure = ""
    last_query_text = f"ambiguous cross-subsystem query variant {variant}"
    last_stats: dict[str, Any] = {"dir_count": 0, "n": 0, "spread": 0.0, "balance": 0.0, "cluster_sizes": []}

    for attempt in range(1, max_retries + 1):
        user_prompt = (
            f"{context}\n\n"
            f"---\n\n"
            f"## Known facts about this codebase\n"
            f"{index_facts}\n\n"
            f"---\n\n"
            f"Write ONE ambiguous search query (variant {variant}) using a term "
            f"that genuinely exists in 2-3 UNRELATED subsystems of this codebase.\n"
        )
        if prior_queries:
            recent = prior_queries[-20:]
            anti = "\n".join(f"  - {q}" for q in recent)
            user_prompt += (
                f"\n## Previously generated AMBIG queries for this repo\n"
                f"Do NOT repeat or paraphrase any of these — pick a DIFFERENT "
                f"ambiguous term or concept:\n{anti}\n"
            )
        if attempt > 1 and last_failure:
            user_prompt += (
                f"\n(Attempt {attempt} — previous query was rejected: {last_failure}. "
                f"Pick a term that genuinely appears in multiple unrelated modules "
                f"— check the class/function list for shared names.)\n"
            )

        gen_result = _call_llm_json(
            model=model,
            system_prompt=_AMBIG_GENERATE_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=200,
        )
        query_text = gen_result.get("query_text", "")
        if not isinstance(query_text, str) or not query_text.strip():
            logger.warning("Empty AMBIG query_text, variant %d attempt %d", variant, attempt)
            continue

        word_count = len(query_text.split())
        if word_count < _NON_OK_MIN_WORDS or word_count > _NON_OK_MAX_WORDS:
            last_failure = f"word count {word_count} (need {_NON_OK_MIN_WORDS}-{_NON_OK_MAX_WORDS})"
            logger.warning("AMBIG variant %d attempt %d: %s", variant, attempt, last_failure)
            continue

        # RRF validation against OK baseline
        stats = asyncio.run(_rrf_stats(app_ctx, query_text))
        failure = _check_ambig(stats, baseline)
        if failure is None:
            clusters_desc = ", ".join(
                f"{size} results" for size in stats["cluster_sizes"][:5]
            )
            return {
                "query_type": "AMBIG",
                "query_text": query_text,
                "seeds": [],
                "pins": [],
                "candidate_neighborhoods": (
                    f"{stats['dir_count']} directory clusters: [{clusters_desc}]"
                ),
                "why_ambiguous": (
                    f"balance={stats['balance']:.2f} exceeds OK max "
                    f"({baseline.max_balance:.2f}). Results split across "
                    f"{stats['dir_count']} subsystems with no single dominant target."
                ),
                "rrf_stats": {
                    "dir_count": stats["dir_count"],
                    "n": stats["n"],
                    "spread": round(stats["spread"], 4),
                    "balance": round(stats["balance"], 2),
                },
                "ok_baseline": {
                    "max_dir_count": baseline.max_dir_count,
                    "max_balance": round(baseline.max_balance, 2),
                },
            }

        last_failure = failure
        last_query_text = query_text
        last_stats = stats
        logger.warning(
            "AMBIG variant %d attempt %d: RRF rejected — %s (query=%r, stats=%s)",
            variant, attempt, failure, query_text,
            {k: v for k, v in stats.items() if k != "cluster_sizes"},
        )

    # Exhausted retries — use the last attempt as a best-effort fallback.
    logger.warning(
        "AMBIG variant %d: exhausted %d retries, using last attempt as fallback",
        variant, max_retries,
    )
    return {
        "query_type": "AMBIG",
        "query_text": last_query_text,
        "seeds": [],
        "pins": [],
        "why_ambig": f"FALLBACK — validation failed after {max_retries} attempts: {last_failure}",
        "cluster_description": f"dir_count={last_stats['dir_count']}, balance={last_stats.get('balance', 0):.2f} (did not exceed baseline)",
        "rrf_stats": {
            "dir_count": last_stats["dir_count"],
            "n": last_stats["n"],
            "balance": round(last_stats.get("balance", 0), 4),
        },
        "ok_baseline": {
            "max_dir_count": baseline.max_dir_count,
            "max_balance": round(baseline.max_balance, 4),
        },
        "fallback": True,
    }


# ── LLM transport ───────────────────────────────────────────────


# Cached Azure AAD token — refreshed automatically when near expiry.
_azure_token: str | None = None
_azure_token_expires: float = 0.0  # time.monotonic() when it expires
_azure_token_lock = threading.Lock()


def _get_azure_token() -> str | None:
    """Return a valid Azure AAD token, refreshing if needed.

    Caches the token and refreshes 5 minutes before expiry.
    Uses ``az account get-access-token`` which returns both the token
    and its expiry timestamp.
    Thread-safe: serialises token refresh via lock.
    """
    import time as _time

    global _azure_token, _azure_token_expires

    # Fast path: cached token still valid (read without lock is safe for check)
    if _azure_token and _time.monotonic() < (_azure_token_expires - 300):
        return _azure_token

    with _azure_token_lock:
        # Re-check under lock (another thread may have refreshed)
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

            expires_on = body.get("expires_on")
            if expires_on:
                ttl = int(expires_on) - int(_time.time())
            else:
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
                    logger.error("Azure HTTP %d on attempt %d/5: %s", exc.code, _attempt + 1,
                                 exc.read().decode()[:300] if hasattr(exc, 'read') else str(exc))
                    if exc.code == 429:
                        # Rate limited — exponential backoff
                        wait = min(2 ** _attempt * 5, 60)
                        logger.warning("Azure 429, backing off %ds (attempt %d/5)", wait, _attempt + 1)
                        _time.sleep(wait)
                        continue
                    if exc.code == 401 and _attempt == 0:
                        # Token expired — force refresh and retry
                        global _azure_token, _azure_token_expires
                        with _azure_token_lock:
                            _azure_token = None
                            _azure_token_expires = 0.0
                        azure_token = _get_azure_token()
                        if not azure_token:
                            logger.error("Azure: token refresh failed after 401")
                            break
                        continue
                    logger.error("Azure: non-retryable HTTP %d, giving up", exc.code)
                    break
                except RuntimeError as _exc:
                    # JSON parse failure — retry with doubled max_tokens
                    if "parse JSON" in str(_exc) and _attempt < 4:
                        logger.warning("Azure: JSON parse failed, retrying with more tokens (attempt %d/5): %s",
                                       _attempt + 1, str(_exc)[:200])
                        max_tokens = min(max_tokens * 2, 4096)
                        payload = json.dumps({
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": max_tokens,
                        }).encode()
                        continue
                    logger.error("Azure call failed: %s: %s", type(_exc).__name__, _exc)
                    break
                except Exception as _exc:
                    # Transient network errors (DNS, timeout, connection) — retry with backoff
                    import urllib.error as _ue
                    if isinstance(_exc, (_ue.URLError, TimeoutError, OSError, ConnectionError)) and _attempt < 4:
                        wait = min(2 ** _attempt * 5, 60)
                        logger.warning("Azure network error, backing off %ds (attempt %d/5): %s",
                                       wait, _attempt + 1, _exc)
                        _time.sleep(wait)
                        continue
                    logger.error("Azure call failed (non-HTTP): %s: %s", type(_exc).__name__, _exc)
                    break
            else:
                # All 5 retries exhausted (all 429s) — log it
                logger.error("Azure: all 5 retries exhausted (rate-limited)")
        else:
            logger.error("Azure: _get_azure_token() returned None")

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
    raise RuntimeError(f"Failed to parse JSON from LLM response: {text[:200]}")
    raise RuntimeError("LLM response was not valid JSON")