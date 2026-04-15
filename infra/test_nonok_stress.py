"""Stress-test non-OK query generation: 5 runs × 3 types = 15 calls."""

import json
import os
import subprocess
import sys
import urllib.request

# ── Credentials ──────────────────────────────────────────────────

ENDPOINT = subprocess.run(
    ["terraform", "output", "-raw", "ai_services_endpoint"],
    cwd=os.path.dirname(__file__),
    capture_output=True, text=True, check=True,
).stdout.rstrip("/")

TOKEN = subprocess.run(
    ["az", "account", "get-access-token",
     "--resource", "https://cognitiveservices.azure.com",
     "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, check=True,
).stdout.strip()

URL = f"{ENDPOINT}/openai/deployments/gpt-4.1-mini/chat/completions?api-version=2024-12-01-preview"

# ── Shared context ───────────────────────────────────────────────

CONTEXT = """\
Repository: coderecon/coderecon
Instance: coderecon__lcov_warnings

## Issue
The LcovParser.parse() method silently swallows ValueError via 4x except ValueError: pass.
Add a parse_warnings counter to CoverageReport and increment it instead of silently passing.

## Patch (excerpt)
diff --git a/src/coderecon/testing/parsers/lcov.py b/src/coderecon/testing/parsers/lcov.py
@@ -60,7 +60,8 @@ class LcovParser:
-            except ValueError:
-                pass
+            except ValueError:
+                self.parse_warnings += 1
diff --git a/src/coderecon/testing/models.py b/src/coderecon/testing/models.py
@@ -129,6 +129,7 @@ class CoverageReport:
+    parse_warnings: int = 0"""

NON_OK_SYSTEM = """\
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

NON_OK_TYPES = {
    "BROAD": (
        "Write ONE BROAD query about this repository.\n\n"
        "What BROAD means: A query that is too vague — it would match "
        "hundreds of files with no clear focus. The query MUST be 3-10 "
        "words long (not a single word). It should sound like a real "
        "developer question, just too unfocused."
    ),
    "UNSAT": (
        "Write ONE UNSAT query about this repository.\n\n"
        "What UNSAT means: A query that CANNOT be answered from this "
        "repository — it asks about something that doesn't exist here. "
        "CRITICAL: Do NOT reuse any identifier, class name, function name, "
        "or file path that appears in the issue or patch. Invent "
        "plausible-sounding but fictional names."
    ),
    "WRONG_CONTEXT": (
        "Write ONE WRONG_CONTEXT query about this repository.\n\n"
        "What WRONG_CONTEXT means: A query that is topically related to the "
        "issue but would lead a retrieval system to the wrong part of the "
        "codebase — wrong file, wrong class, wrong subsystem."
    ),
}

RUNS_PER_TYPE = 5


def _call(system: str, user: str, temperature: float = 1.0) -> dict:
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 200,
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(URL, data=payload, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }, method="POST")
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    text = resp["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text.strip())


# ── Quality checks ───────────────────────────────────────────────

def check_broad(query: str) -> list[str]:
    """A good BROAD query should be short-ish and vague."""
    issues = []
    words = query.split()
    if any(w in query.lower() for w in ["lcovparser", "parse_warnings", "coveragereport"]):
        issues.append(f"TOO SPECIFIC — mentions exact identifiers: {query!r}")
    if len(words) < 3:
        issues.append(f"TOO SHORT — only {len(words)} word(s), need 3-10: {query!r}")
    if len(words) > 15:
        issues.append(f"suspiciously long ({len(words)} words)")
    return issues


def check_unsat(query: str) -> list[str]:
    """A good UNSAT query should reference things that don't exist."""
    issues = []
    # If it mentions the real identifiers from the patch, it's not unsatisfiable
    real = ["lcovparser", "parse_warnings", "coveragereport", "lcov.py", "models.py"]
    hits = [r for r in real if r in query.lower()]
    if hits:
        issues.append(f"mentions REAL identifiers {hits} — should be fictional")
    return issues


def check_wrong_context(query: str) -> list[str]:
    """A good WRONG_CONTEXT query should sound related but target wrong area."""
    issues = []
    if len(query.split()) < 2:
        issues.append(f"too short to be a meaningful misdirection: {query!r}")
    return issues


CHECKERS = {
    "BROAD": check_broad,
    "UNSAT": check_unsat,
    "WRONG_CONTEXT": check_wrong_context,
}


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    total_calls = 0
    total_issues = 0
    all_queries: dict[str, list[str]] = {}

    for nok_type, description in NON_OK_TYPES.items():
        print(f"\n{'='*60}")
        print(f"  {nok_type}  ({RUNS_PER_TYPE} runs)")
        print(f"{'='*60}")
        queries = []
        for i in range(RUNS_PER_TYPE):
            total_calls += 1
            user = CONTEXT + "\n\n---\n\n" + description + f"\n\nThis is variant {i+1}. Be creative — make it different from obvious choices."
            try:
                r = _call(NON_OK_SYSTEM, user, temperature=1.0)
                q = r["query_text"]
                queries.append(q)
                issues = CHECKERS[nok_type](q)
                status = "WARN" if issues else "OK"
                total_issues += len(issues)
                print(f"  [{i+1}] {status:4s}  {q!r}")
                for iss in issues:
                    print(f"         ⚠ {iss}")
            except Exception as e:
                total_issues += 1
                print(f"  [{i+1}] FAIL  {e}")

        all_queries[nok_type] = queries
        # Check diversity: how many unique queries?
        unique = len(set(q.lower().strip() for q in queries))
        print(f"  --- diversity: {unique}/{len(queries)} unique")
        if unique < len(queries) * 0.6:
            print(f"  ⚠ LOW DIVERSITY — model is repeating itself")
            total_issues += 1

    print(f"\n{'='*60}")
    print(f"  SUMMARY: {total_calls} calls, {total_issues} issues")
    print(f"{'='*60}")

    # Dump all queries for review
    for nok_type, queries in all_queries.items():
        print(f"\n{nok_type}:")
        for i, q in enumerate(queries, 1):
            print(f"  {i}. {q}")

    sys.exit(1 if total_issues > 3 else 0)
