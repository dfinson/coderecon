"""Smoke-test the deployed gpt-4.1-mini with all 3 call types."""

import json
import os
import subprocess
import urllib.request

# ── Credentials ──────────────────────────────────────────────────

ENDPOINT = subprocess.run(
    ["terraform", "output", "-raw", "ai_services_endpoint"],
    cwd=os.path.dirname(__file__),
    capture_output=True, text=True, check=True, timeout=30,
).stdout.rstrip("/")

TOKEN = subprocess.run(
    ["az", "account", "get-access-token",
     "--resource", "https://cognitiveservices.azure.com",
     "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, check=True, timeout=30,
).stdout.strip()

URL = f"{ENDPOINT}/openai/deployments/gpt-4.1-mini/chat/completions?api-version=2024-12-01-preview"

# ── Shared context (real coderecon issue) ────────────────────────

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


def _call(system: str, user: str, max_tokens: int = 300) -> dict:
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(URL, data=payload, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }, method="POST")
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    text = resp["choices"][0]["message"]["content"].strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text.strip())


# ── Test 1: Classify ─────────────────────────────────────────────

CLASSIFY_SYSTEM = """\
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
- wide: touches 8+ functions or crosses multiple subsystems"""


def test_classify():
    r = _call(CLASSIFY_SYSTEM, CONTEXT)
    print("=== TEST 1: Classify ===")
    print(json.dumps(r, indent=2))
    assert r["task_complexity"] in ("narrow", "medium", "wide")
    assert r["confidence"] in ("high", "medium", "low")
    assert isinstance(r["solve_notes"], str) and len(r["solve_notes"]) > 5
    assert isinstance(r["tier_difference_reasoning"], str)
    tc, conf = r["task_complexity"], r["confidence"]
    print(f"  -> {tc}/{conf}")
    print()


# ── Test 2: One OK query (Q_IDENTIFIER) ─────────────────────────

OK_SYSTEM = """\
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
- query_text should be realistic"""


def test_ok_query():
    user = (
        CONTEXT + "\n\n---\n\n"
        "Write ONE Q_IDENTIFIER query for the issue above.\n\n"
        "What Q_IDENTIFIER means: A query that names specific "
        "function/class/variable identifiers the developer would search for.\n"
    )
    r = _call(OK_SYSTEM, user)
    print("=== TEST 2: Q_IDENTIFIER query ===")
    print(json.dumps(r, indent=2))
    assert isinstance(r["query_text"], str) and len(r["query_text"]) > 5
    assert isinstance(r["seeds"], list)
    assert isinstance(r["pins"], list)
    print(f"  -> query: {r['query_text'][:80]}")
    print(f"  -> seeds: {r['seeds']}")
    print(f"  -> pins:  {r['pins']}")
    print()


# ── Test 3: One NON-OK query (BROAD) ────────────────────────────

NON_OK_SYSTEM = """\
You write ONE search query that should NOT return good results from a code retrieval system.
Return ONLY a JSON object with exactly these fields:
{
  "query_text": "the bad search query",
  "seeds": [],
  "pins": []
}

The query should be realistic (something a user might type) but should fail for the specified reason."""


def test_non_ok_query():
    user = (
        CONTEXT + "\n\n---\n\n"
        "Write ONE BROAD query (variant 1) about this repository.\n\n"
        "What BROAD means: A query that is too vague — it would match "
        "hundreds of files with no clear focus.\n"
    )
    r = _call(NON_OK_SYSTEM, user, max_tokens=200)
    print("=== TEST 3: BROAD query ===")
    print(json.dumps(r, indent=2))
    assert isinstance(r["query_text"], str) and len(r["query_text"]) > 5
    print(f"  -> query: {r['query_text'][:80]}")
    print()


# ── Run ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_classify()
    test_ok_query()
    test_non_ok_query()
    print("=== ALL 3 TESTS PASSED ===")
