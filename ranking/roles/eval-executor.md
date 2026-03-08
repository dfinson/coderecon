# Role: Eval Task Executor

> **This role extends the training task executor with trace capture
> for evaluation.**
>
> Read and follow ALL instructions in
> `../../../roles/executor.md` first.
> Everything below is IN ADDITION to those instructions — not a
> replacement.

---

## Additional eval-only instructions

### Trace markers

For every task, you MUST bracket the SOLVE phase with terminal echo
markers. The markers use the full task ID including the repo name.

**Before** starting STEP 1 — SOLVE for each task:

```bash
echo "START_EVAL_TASK-{repo_id}/{heading_id}"
```

**After** completing STEP 1 — SOLVE (after the commit + revert, before
STEP 1b — TEST COVERAGE):

```bash
echo "END_EVAL_TASK-{repo_id}/{heading_id}"
```

Example for task N1 in python-pydantic:

```bash
echo "START_EVAL_TASK-python-pydantic/N1"
# ... solve the task: read files, make edits, run tests ...
# ... git add -A && git diff --cached ...
# ... git commit -m "task N1: ..." ...
# ... git revert HEAD --no-edit ...
echo "END_EVAL_TASK-python-pydantic/N1"
```

**CRITICAL RULES for markers:**

1. Each marker MUST be its own separate `echo` command — never
   combine with `&&` or `;`
2. The task ID MUST be `{repo_id}/{heading_id}` — e.g.,
   `python-pydantic/N1`, not just `N1`
3. Do NOT put any tool calls between `END_EVAL_TASK` and the start
   of STEP 1b (coverage) — the marker must cleanly end the solve
   phase
4. Do NOT echo markers during STEP 2 (REFLECT) — only STEP 1 (SOLVE)
   is traced

### Task sequence with markers

The full per-task sequence for eval is:

```
echo "START_EVAL_TASK-{repo_id}/{heading_id}"
  STEP 1 — SOLVE (read, edit, test, commit, revert)
echo "END_EVAL_TASK-{repo_id}/{heading_id}"
  STEP 1b — TEST COVERAGE (run with --coverage, save report)
  STEP 2 — REFLECT (write ground truth JSON)
```

### Everything else is identical

All other instructions — the JSON format, query types, def tiers,
non-OK queries file, coverage collection — are exactly as specified
in `executor.md`. Follow them verbatim.

## When you are done

After all tasks AND the non-OK queries file:

### Export and parse your chat traces

**Step 1 — Export the chat session:**

Run the VS Code command to export this chat:

```
workbench.action.chat.export
```

> This will prompt the user to save the file. Wait for confirmation.

**Step 2 — Run the trace parser:**

```bash
python3 ../../../infra/parse_traces.py \
  <path_to_exported_chat.json> \
  --output ../../../data/{repo_id}/traces.jsonl
```

Replace `{repo_id}` with the repo name (e.g., `python-pydantic`).
Replace `<path_to_exported_chat.json>` with the path the user saved
the export to.

Verify the output:
- The script should report how many tasks were extracted
- The count should match the number of tasks with markers (should be 33)
- If any tasks are missing, note which ones

**Step 3 — Clean up:**

Delete the chat export JSON — it's large and the traces JSONL has
everything we need:

```bash
rm <path_to_exported_chat.json>
```

**Step 4 — Report:**

```
ALL EVAL TASKS COMPLETE.
Traces extracted: <count>/33 tasks
Traces output: ../../../data/{repo_id}/traces.jsonl
```
