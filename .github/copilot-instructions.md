# Copilot Instructions

Authority: SPEC.md wins. If unsure or there is a spec conflict, stop and ask.

---

## 1) No Hacks (Root Cause Only)

If something fails, diagnose and fix it properly. Do not "make it pass".

Forbidden:
- # type: ignore, Any, dishonest cast()
- try/except or inline imports to dodge module issues
- regex or string parsing for structured data
- raw SQL to bypass ORM or typing
- empty except blocks or silent fallbacks
- "for now" workarounds

If you cannot solve it correctly with available tools or information, say so and ask.

## 2) All Checks Must Pass

Lint, typecheck, tests, and CI must be green.

## 3) GitHub Remote Actions Must Be Exact

When asked to perform a specific remote action (merge, resolve threads, release, etc.):
- do exactly that action, or
- state it is not possible with available tools

No substitutions.

## 4) Change Discipline (Minimal)

- Before coding: read the issue, relevant SPEC.md sections, and match repo patterns
- Prefer minimal code; do not invent abstractions or reimplement libraries
- Tests should be small, behavioral, and parameterized when appropriate

## 5) NEVER Reset Hard Without Approval

**ABSOLUTE PROHIBITION**: Never execute `git reset --hard` under any circumstances without explicit user approval.

This applies to:
- `git reset --hard` (any ref)
- Any equivalent destructive operation that discards uncommitted changes

If you believe a hard reset is needed:
1. STOP and explain why you think it's necessary
2. List what uncommitted work will be lost
3. Wait for explicit user confirmation before proceeding

Violating this rule destroys work irreversibly and may affect parallel agent workflows.
