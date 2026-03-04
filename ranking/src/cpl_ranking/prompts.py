"""Prompts for the ground truth collection pipeline.

Two prompts, used in sequence within a single agent session:

1. SOLVE_PROMPT — agent solves the task against the cloned repo
2. REFLECT_PROMPT — agent produces ground truth from its solution

The solve prompt is sent first. After the agent finishes (edits committed),
the reflect prompt is sent in the same session so the agent has full
context of what it just did.

Output of the reflect phase is a single JSON object matching
REFLECT_OUTPUT_SCHEMA.
"""

from __future__ import annotations

# ── Phase 1: Solve ───────────────────────────────────────────────

SOLVE_PROMPT = """\
You are working on the repository {repo_id} (cloned at {repo_path}).

Your task:

{task_text}

Solve this task. Read the code you need to understand, make the necessary
edits, and verify your changes work. Use the tools available to you
(file reads, edits, terminal commands).

When you are confident the task is complete, stop and say "DONE".
Do not explain your changes — just make them.
"""

# ── Phase 2: Reflect ─────────────────────────────────────────────

REFLECT_PROMPT = """\
You just solved a task on {repo_id}. Now produce ground truth data
for a code retrieval system. This is a separate step — do not make
any more edits.

Answer the following in a single JSON object (no markdown fencing):

1. "read_necessary": List the file paths you read that were necessary
   to understand or solve this task. Include only files that were
   genuinely needed — not files you opened and immediately closed, or
   explored out of curiosity. Edited files are tracked separately;
   do not include them here.

2. "queries": An array of query objects. Each query has:
   - "query_type": one of "L0", "L1", "L2", "UNSAT", "BROAD", "AMBIG"
   - "query_text": the query string

   Author exactly these queries:

   **Three OK queries at increasing specificity:**

   - L0: A high-level task description. No identifiers, no file paths.
     Just describe what needs to be done in domain terms. Verify that
     this repo's structure makes the query unambiguous — a developer
     familiar with the codebase would know exactly where to look.

   - L1: The L0 query plus concrete identifiers — symbol names, error
     strings, function names — drawn from the code you actually touched.

   - L2: The L1 query plus anchoring constraints — file paths, module
     names, behavioral constraints — that further narrow the scope.

   **Up to three non-OK queries (skip any that feel forced):**

   - UNSAT: A query related to the same area of this repo, but that
     makes a plausible assumption about the architecture that is
     factually wrong. A developer would say "that's not how it works."

   - BROAD: Think of a large effort this task would have been part of —
     one touching many files across subsystems. Describe that effort.
     A developer would say "that's 20 tasks, not one."

   - AMBIG: A query in the same domain, but where this repo has multiple
     subsystems that could plausibly be the target, and the query does
     not resolve between them. A developer would ask "which one?"

   You MUST produce all three OK queries (L0, L1, L2).
   For non-OK queries, produce only those that arise naturally from this
   task's neighborhood. Do not force them — fewer is better than fake.

Example output (do not copy — produce your own):

{example_output}
"""

REFLECT_EXAMPLE_OUTPUT = """\
{
  "read_necessary": [
    "src/auth/config.py",
    "src/auth/middleware.py",
    "tests/auth/test_middleware.py"
  ],
  "queries": [
    {
      "query_type": "L0",
      "query_text": "Fix the authentication middleware to handle expired tokens gracefully instead of returning a 500 error"
    },
    {
      "query_type": "L1",
      "query_text": "Fix AuthMiddleware.process_request to catch TokenExpiredError from jwt_decode and return 401 instead of propagating the exception"
    },
    {
      "query_type": "L2",
      "query_text": "Fix AuthMiddleware.process_request in src/auth/middleware.py to catch TokenExpiredError from jwt_decode (src/auth/tokens.py) and return a 401 JSON response with error code 'token_expired'"
    },
    {
      "query_type": "UNSAT",
      "query_text": "Update the OAuth2 token refresh endpoint to automatically retry with the backup auth server when the primary is down"
    },
    {
      "query_type": "BROAD",
      "query_text": "Migrate the entire authentication system from JWT tokens to session-based auth with Redis storage"
    }
  ]
}\
"""

# ── Reflect output schema ────────────────────────────────────────

REFLECT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["read_necessary", "queries"],
    "additionalProperties": False,
    "properties": {
        "read_necessary": {
            "type": "array",
            "items": {"type": "string"},
            "description": "File paths read that were necessary (not edited files).",
        },
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["query_type", "query_text"],
                "additionalProperties": False,
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["L0", "L1", "L2", "UNSAT", "BROAD", "AMBIG"],
                    },
                    "query_text": {"type": "string"},
                },
            },
            "minItems": 3,
            "maxItems": 6,
            "description": "3 OK queries (L0/L1/L2) + up to 3 non-OK queries.",
        },
    },
}


def format_solve_prompt(
    repo_id: str,
    repo_path: str,
    task_text: str,
) -> str:
    """Format the Phase 1 solve prompt with task-specific values."""
    return SOLVE_PROMPT.format(
        repo_id=repo_id,
        repo_path=repo_path,
        task_text=task_text,
    )


def format_reflect_prompt(repo_id: str) -> str:
    """Format the Phase 2 reflect prompt."""
    return REFLECT_PROMPT.format(
        repo_id=repo_id,
        example_output=REFLECT_EXAMPLE_OUTPUT,
    )
