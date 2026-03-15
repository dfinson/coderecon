# Agent Prompt Templates

> **These are reference prompts for manual sessions.**
> The orchestrator (`gt_orchestrator.py`) builds prompts automatically.
> These are only needed if running sessions by hand.

Replace `{SET}` with `ranker-gate`, `cutoff`, or `eval`.
Replace `{REPO_NAME}` with e.g. `python-fastapi`.
Replace `{HEADING}` with e.g. `N1`, `M3`, `W11`.

---

## Auditor

Run **33 separate sessions** per repo, one per task heading.
(Pre-flight setup is handled by the setup agent session.)

```
Read ../../../roles/auditor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
You are auditing task {HEADING} only.
Begin.
```

---

## Executor

Run **33 separate sessions** per repo, one per task heading.

```
Read ../../../roles/executor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
You are solving task {HEADING} only.
Begin.
```

---

## Analyst

Run **33 separate sessions** per repo, one per task heading.
Each session needs the candidate exploration map from
`data/{REPO_NAME}/candidates/{HEADING}.json`.

```
Read ../../../roles/analyst.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
You are analyzing task {HEADING}.
The exploration map is at ../../../data/{REPO_NAME}/candidates/{HEADING}.json
Begin.
```

---

## Non-OK Author

One session per repo. Run after all 33 executor+analyst sessions.

```
Read ../../../roles/non_ok_author.md — those are your instructions.
The repo_id is {REPO_NAME}.
Begin.
```

---

## Reviewer

Run **33 separate sessions** per repo, one per task heading.
Run after all executor+analyst sessions and non-OK author.

```
Read ../../../roles/reviewer.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
You are reviewing task {HEADING} only.
The ground truth JSON is at ../../../data/{REPO_NAME}/ground_truth/{HEADING}.json
Begin.
```
