#!/usr/bin/env python3
"""Ground truth pipeline — local Copilot SDK orchestrator (v2).

Trace-based pipeline: one task per executor session, passive trace
capture, deterministic candidate extraction, analyst-produced GT.

Workspace layout (controlled by CPL_LAB_WORKSPACE env var,
default: ~/.recon/recon-lab):

    $CPL_LAB_WORKSPACE/
    ├── clones/{set}/{repo}/       # cloned repos
    ├── data/{repo_id}/            # ground truth, signals
    │   ├── ground_truth/
    │   ├── traces/
    │   ├── candidates/
    │   └── signals/
    ├── data/merged/               # training parquets
    ├── data/gt_state.json         # pipeline state
    └── data/logs/                 # session transcripts

Pipeline source (repos/, roles/, infra/) stays in the git repo under
recon-lab/.

Usage:
    cpl-lab generate                        # run all stages
    cpl-lab generate --stage audit          # only audit stage
    cpl-lab generate --repo cpp-abseil      # one repo
    cpl-lab generate --concurrency 10       # override default
    cpl-lab status                          # show state table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

# ── Paths ──

LAB_SRC = Path(__file__).resolve().parent.parent  # in-repo pipeline source
WORKSPACE = Path(
    os.environ.get("CPL_LAB_WORKSPACE", Path.home() / ".recon" / "recon-lab")
)

REPOS_DIR = LAB_SRC / "repos"   # task definitions (versioned, in-repo)
ROLES_DIR = LAB_SRC / "roles"   # agent role prompts (versioned, in-repo)
CLONES_DIR = WORKSPACE / "clones"   # cloned repos (mutable, outside repo)
DATA_DIR = WORKSPACE / "data"       # ground truth + signals (mutable, outside repo)
STATE_FILE = DATA_DIR / "gt_state.json"
LOGS_DIR = DATA_DIR / "logs"

# ── Pipeline stages ──
# setup (1/repo: prework + agent env setup) → audit (33/repo) → exec (33) → analyze (33) → non_ok (1) → review (33)
# setup/non_ok are single-session; audit/exec/analyze/review are per-task (33 each).
STAGES = ["setup", "audit", "exec", "analyze", "non_ok", "review"]
TASK_PREFIXES = ["N", "M", "W"]
TASKS_PER_PREFIX = 11
ALL_HEADINGS = [f"{p}{i}" for p in TASK_PREFIXES for i in range(1, TASKS_PER_PREFIX + 1)]

DEFAULT_CONCURRENCY = 12
MAX_ATTEMPTS = 3
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF = [30, 60, 120, 300]  # seconds between retries

MODELS = {
    "setup": "claude-sonnet-4.6",
    "audit": "claude-sonnet-4.6",
    "exec": "claude-opus-4.6",
    "analyze": "claude-opus-4.6",
    "non_ok": "claude-opus-4.6",
    "review": "claude-sonnet-4.6",
}

# ── Helpers ──


def headings_for_stage(stage: str) -> list[str]:
    """Return task headings relevant to a per-task stage."""
    if stage in ("audit", "exec", "analyze", "review"):
        return ALL_HEADINGS
    return []


def task_artifact_exists(repo_id: str, heading_id: str, kind: str) -> bool:
    """Check if a specific artifact exists for a heading.
    kind: 'audit', 'trace', 'candidates', 'ground_truth', 'review'
    """
    if kind == "audit":
        return (DATA_DIR / repo_id / "audit" / f"{heading_id}.json").exists()
    if kind == "trace":
        return (DATA_DIR / repo_id / "traces" / f"{heading_id}.jsonl").exists()
    if kind == "candidates":
        return (DATA_DIR / repo_id / "candidates" / f"{heading_id}.json").exists()
    if kind == "ground_truth":
        return (DATA_DIR / repo_id / "ground_truth" / f"{heading_id}.json").exists()
    if kind == "review":
        return (DATA_DIR / repo_id / "review" / f"{heading_id}.json").exists()
    return False


def all_tasks_have_artifact(repo_id: str, kind: str) -> tuple[bool, int, int]:
    """Check if all 33 headings have a given artifact.
    Returns (all_done, done_count, total).
    """
    done = sum(1 for h in ALL_HEADINGS if task_artifact_exists(repo_id, h, kind))
    return done == len(ALL_HEADINGS), done, len(ALL_HEADINGS)


def stage_artifacts_satisfied(repo_id: str, stage: str) -> tuple[bool, str, int]:
    """Check if a stage's output artifacts are complete."""
    if stage == "setup":
        # Setup is done if the setup marker exists
        marker = DATA_DIR / repo_id / "audit" / "_setup_done.json"
        return marker.exists(), "setup marker", int(marker.exists())
    if stage == "audit":
        ok, done, total = all_tasks_have_artifact(repo_id, "audit")
        if ok:
            return True, "ok", done
        return False, f"audit: {done}/{total}", done
    if stage == "exec":
        ok, done, total = all_tasks_have_artifact(repo_id, "trace")
        if ok:
            return True, "ok", done
        return False, f"traces: {done}/{total}", done
    if stage == "analyze":
        ok, done, total = all_tasks_have_artifact(repo_id, "ground_truth")
        if ok:
            return True, "ok", done
        return False, f"ground_truth: {done}/{total}", done
    if stage == "non_ok":
        gt_dir = DATA_DIR / repo_id / "ground_truth"
        if (gt_dir / "non_ok_queries.json").exists():
            return True, "ok", 1
        return False, "non_ok_queries.json missing", 0
    if stage == "review":
        ok, done, total = all_tasks_have_artifact(repo_id, "review")
        if ok:
            return True, "ok", done
        return False, f"review: {done}/{total}", done
    return True, "unknown stage", 0


# ── State management ──


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"global_stage": "audit", "repos": {}}


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.fsync(fd)
        os.close(fd)
        os.rename(tmp, path)
    except BaseException:
        os.close(fd)
        os.unlink(tmp)
        raise


def save_state(state: dict) -> None:
    _atomic_write(STATE_FILE, json.dumps(state, indent=2) + "\n")


def next_stage_for_repo(rd: dict) -> str | None:
    """Return the next stage this repo is eligible to run, or None if all done."""
    def _done(stage: str) -> bool:
        return rd.get(stage, {}).get("status") in ("merged", "done")

    def _pending(stage: str) -> bool:
        return rd.get(stage, {}).get("status") in ("pending", "") or stage not in rd

    # Stages must run in order
    for stage in STAGES:
        if not _done(stage):
            return stage if _pending(stage) else None
    return None


def all_eligible_tasks(
    state: dict,
    stage_filter: str | None = None,
    repo_filter: str | None = None,
) -> list[tuple[str, str]]:
    """Return list of (repo_id, stage) pairs ready to run."""
    tasks = []
    for rid, rd in state["repos"].items():
        if repo_filter and rid != repo_filter:
            continue
        ns = next_stage_for_repo(rd)
        if ns is None:
            continue
        if stage_filter and ns != stage_filter:
            continue
        tasks.append((rid, ns))
    return tasks


def recover_orphaned_active(state: dict) -> int:
    """Reset any 'active' statuses left from a previous interrupted run."""
    recovered = 0
    for rid, rd in state["repos"].items():
        for stage in STAGES:
            if rd.get(stage, {}).get("status") != "active":
                continue
            recovered += 1
            ok, reason, count = stage_artifacts_satisfied(rid, stage)
            if ok:
                rd[stage] = {
                    "status": "done",
                    "note": "recovered from interrupted run",
                }
            else:
                attempts = rd[stage].get("attempts", 0)
                _archive_log(rid, stage, attempts)
                rd[stage] = {"status": "pending", "attempts": attempts}
    if recovered:
        save_state(state)
    return recovered


def pipeline_done(state: dict) -> bool:
    """True when every repo has completed all stages."""
    for rd in state["repos"].values():
        for stage in STAGES:
            if rd.get(stage, {}).get("status") not in ("merged", "done"):
                return False
    return True


def find_clone(repo_id: str) -> Path | None:
    """Find the clone directory for a repo_id."""
    for set_dir in CLONES_DIR.iterdir():
        if not set_dir.is_dir():
            continue
        for d in set_dir.iterdir():
            if not d.is_dir():
                continue
            config = d / ".gt-pipeline.json"
            if config.exists():
                try:
                    c = json.loads(config.read_text())
                    if c.get("repo_id") == repo_id:
                        return d
                except Exception:
                    pass
    # Fallback: match by repo name from state
    state = load_state()
    rd = state.get("repos", {}).get(repo_id, {})
    fork = rd.get("fork", "")
    if "/" in fork:
        repo_name = fork.split("/")[1]
        for set_dir in CLONES_DIR.iterdir():
            if not set_dir.is_dir():
                continue
            candidate = set_dir / repo_name
            if candidate.is_dir():
                return candidate
    # Fallback: strip language prefix from repo_id (e.g. "python-fastapi" → "fastapi")
    if "-" in repo_id:
        short_name = repo_id.split("-", 1)[1]
        for set_dir in CLONES_DIR.iterdir():
            if not set_dir.is_dir():
                continue
            candidate = set_dir / short_name
            if candidate.is_dir():
                return candidate
    return None


def find_task_file(repo_id: str) -> Path | None:
    for set_dir in REPOS_DIR.iterdir():
        if not set_dir.is_dir():
            continue
        p = set_dir / f"{repo_id}.md"
        if p.exists():
            return p
    return None


def _parse_task_headings(task_file: Path) -> dict[str, str]:
    """Parse a tasks markdown file and extract heading_id → task_text."""
    content = task_file.read_text()
    headings: dict[str, str] = {}
    current_id = None
    current_lines: list[str] = []

    for line in content.split("\n"):
        m = re.match(r"^###\s+(N\d+|M\d+|W\d+):\s*(.*)", line)
        if m:
            if current_id:
                headings[current_id] = "\n".join(current_lines).strip()
            current_id = m.group(1)
            current_lines = [m.group(2).strip()] if m.group(2).strip() else []
        elif current_id:
            # Stop at next heading of same or higher level
            if re.match(r"^#{1,3}\s", line):
                headings[current_id] = "\n".join(current_lines).strip()
                current_id = None
                current_lines = []
            else:
                current_lines.append(line)

    if current_id:
        headings[current_id] = "\n".join(current_lines).strip()

    return headings


# ── Schema validation ──


GT_REQUIRED_KEYS = {
    "task_id", "task_complexity", "task_text", "diff", "solve_notes",
    "confidence", "minimum_sufficient_defs",
    "thrash_preventing_defs", "tier_difference_reasoning",
    "excluded_defs", "queries", "test_selection", "reviewer_corrections",
}

NON_OK_REQUIRED_KEYS = {"repo_id", "reviewer_corrections", "non_ok_queries"}


def validate_gt_schema(data: dict) -> list[str]:
    errors = []
    missing = GT_REQUIRED_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing keys: {missing}")
    if not isinstance(data.get("queries"), list):
        errors.append("'queries' must be a list")
    elif len(data.get("queries", [])) < 6:
        errors.append(f"Need ≥6 queries, got {len(data.get('queries', []))}")
    if not isinstance(data.get("minimum_sufficient_defs"), list):
        errors.append("'minimum_sufficient_defs' must be a list")
    if data.get("minimum_sufficient_defs") and not data["minimum_sufficient_defs"][0].get("path"):
        errors.append("Each def must have 'path', 'name', 'kind', 'start_line', 'reason'")
    return errors


def validate_non_ok_schema(data: dict) -> list[str]:
    errors = []
    missing = NON_OK_REQUIRED_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing keys: {missing}")
    queries = data.get("non_ok_queries", [])
    if len(queries) < 6:
        errors.append(f"Need ≥6 non-ok queries, got {len(queries)}")
    return errors


# ── Retryable error detection ──


def _is_retryable(exc: Exception) -> bool:
    """Determine if an exception is a transient/rate-limit error worth retrying."""
    try:
        from copilot.jsonrpc import JsonRpcError, ProcessExitedError
        if isinstance(exc, JsonRpcError):
            if isinstance(exc.code, int) and exc.code in (429, -32000, -32001, -32603):
                return True
        if isinstance(exc, ProcessExitedError):
            return True
    except ImportError:
        pass

    err = str(exc).lower()
    return any(
        k in err for k in (
            "rate limit", "429", "too many", "throttl", "capacity",
            "overloaded", "try again later", "service unavailable", "503",
        )
    )


# ── Tool parameter models ──


class WriteGTParams(BaseModel):
    repo_id: str = Field(description="Repository ID (e.g. 'cpp-abseil')")
    task_id: str = Field(description="Task ID (e.g. 'N1', 'M3', 'W11')")
    data: dict = Field(description="Complete ground truth JSON object")


class WriteNonOKParams(BaseModel):
    repo_id: str = Field(description="Repository ID")
    data: dict = Field(description="Complete non-ok queries JSON object")


class WriteAuditResultParams(BaseModel):
    heading_id: str = Field(description="Task heading ID (e.g. 'N1', 'M3')")
    status: str = Field(description="'ok' or 'corrected'")
    corrections: str = Field(description="What was corrected, or empty string if ok")


class WriteReviewResultParams(BaseModel):
    heading_id: str = Field(description="Task heading ID (e.g. 'N1', 'M3')")
    status: str = Field(description="'ok' or 'corrected'")
    corrections: str = Field(description="What was corrected, or empty string if ok")


class WriteSetupResultParams(BaseModel):
    language: str = Field(description="Primary language detected (e.g. 'python', 'typescript')")
    test_framework: str = Field(description="Test framework used (e.g. 'pytest', 'vitest', 'go test')")
    tests_pass: bool = Field(description="Whether the test suite passes")
    coverage_generated: bool = Field(description="Whether a coverage report was produced")
    notes: str = Field(description="Notable setup details, workarounds, or issues encountered")


class ReportCompleteParams(BaseModel):
    summary: str = Field(description="Brief summary of what was accomplished")


# ── Session runner ──


class SessionRunner:
    """Runs a single Copilot SDK session for one stage/task combination."""

    def __init__(
        self,
        repo_id: str,
        stage: str,
        state: dict,
        heading_id: str | None = None,
    ):
        self.repo_id = repo_id
        self.stage = stage
        self.state = state
        self.heading_id = heading_id  # Set for exec/analyze tasks
        self.start_time = time.time()
        self.status_line = "starting..."
        self.jsons_written = 0
        self.done_event = asyncio.Event()
        self.error: str | None = None

        # Logs
        session_log_dir = LOGS_DIR / "sessions"
        session_log_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{heading_id}" if heading_id else ""
        self.log_path = session_log_dir / f"{repo_id}_{stage}{suffix}.log"

    def log(self, msg: str, level: str = "INFO") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {
            "INFO": "│",
            "TOOL": "├─🔧",
            "WRITE": "├─📝",
            "ERROR": "├─❌",
            "DONE": "└─✅",
            "START": "┌─▶",
            "PROMPT": "├─📨",
        }.get(level, "│")
        with open(self.log_path, "a") as f:
            f.write(f"[{ts}] {prefix} {msg}\n")

    def elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m"

    def build_tools(self):
        from copilot import define_tool

        runner = self

        @define_tool(description="Write a validated ground truth JSON for a completed task.")
        async def write_ground_truth(params: WriteGTParams) -> str:
            errors = validate_gt_schema(params.data)
            if errors:
                runner.log(f"Schema validation failed for {params.task_id}: {errors}", "ERROR")
                return f"VALIDATION FAILED — fix these errors and call again:\n" + "\n".join(
                    f"  - {e}" for e in errors
                )
            path = DATA_DIR / params.repo_id / "ground_truth" / f"{params.task_id}.json"
            _atomic_write(path, json.dumps(params.data, indent=2) + "\n")
            runner.jsons_written += 1
            runner.log(f"{params.task_id}.json written", "WRITE")
            runner.status_line = f"wrote {params.task_id}.json"
            return f"Written successfully: {path}"

        @define_tool(description="Write the non-OK queries JSON file.")
        async def write_non_ok_queries(params: WriteNonOKParams) -> str:
            errors = validate_non_ok_schema(params.data)
            if errors:
                runner.log(f"Non-OK validation failed: {errors}", "ERROR")
                return f"VALIDATION FAILED:\n" + "\n".join(f"  - {e}" for e in errors)
            path = DATA_DIR / params.repo_id / "ground_truth" / "non_ok_queries.json"
            _atomic_write(path, json.dumps(params.data, indent=2) + "\n")
            runner.log(
                f"non_ok_queries.json written ({len(params.data.get('non_ok_queries', []))} queries)",
                "WRITE",
            )
            return f"Written: {path}"

        @define_tool(description="Signal that you have completed your assigned work for this session.")
        async def report_complete(params: ReportCompleteParams) -> str:
            runner.log(f"{params.summary}", "DONE")
            runner.status_line = "✅ done"
            runner.done_event.set()
            return "Session marked complete."

        # Select tools based on stage
        if self.stage == "exec":
            return [report_complete]  # Executor only signals completion
        if self.stage == "audit":
            @define_tool(description="Record the audit result for a single task.")
            async def write_audit_result(params: WriteAuditResultParams) -> str:
                path = DATA_DIR / runner.repo_id / "audit" / f"{params.heading_id}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, json.dumps({
                    "heading_id": params.heading_id,
                    "status": params.status,
                    "corrections": params.corrections,
                }, indent=2) + "\n")
                runner.log(f"audit/{params.heading_id}.json → {params.status}", "WRITE")
                runner.status_line = f"audited {params.heading_id}"
                return f"Audit result recorded: {params.heading_id} → {params.status}"
            return [write_audit_result, report_complete]
        if self.stage == "analyze":
            return [write_ground_truth, report_complete]
        if self.stage == "non_ok":
            return [write_non_ok_queries, report_complete]
        if self.stage == "review":
            @define_tool(description="Record the review result for a single task.")
            async def write_review_result(params: WriteReviewResultParams) -> str:
                path = DATA_DIR / runner.repo_id / "review" / f"{params.heading_id}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, json.dumps({
                    "heading_id": params.heading_id,
                    "status": params.status,
                    "corrections": params.corrections,
                }, indent=2) + "\n")
                runner.log(f"review/{params.heading_id}.json → {params.status}", "WRITE")
                runner.status_line = f"reviewed {params.heading_id}"
                return f"Review recorded: {params.heading_id} → {params.status}"
            return [write_ground_truth, write_review_result, report_complete]
        if self.stage == "setup":
            @define_tool(description="Record the environment setup result for this repository.")
            async def write_setup_result(params: WriteSetupResultParams) -> str:
                marker = DATA_DIR / runner.repo_id / "audit" / "_setup_done.json"
                marker.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(marker, json.dumps({
                    "repo_id": runner.repo_id,
                    "language": params.language,
                    "test_framework": params.test_framework,
                    "tests_pass": params.tests_pass,
                    "coverage_generated": params.coverage_generated,
                    "notes": params.notes,
                }, indent=2) + "\n")
                runner.log(
                    f"setup done: lang={params.language} tests={'pass' if params.tests_pass else 'fail'} "
                    f"cov={'yes' if params.coverage_generated else 'no'}",
                    "WRITE",
                )
                runner.status_line = "setup recorded"
                return f"Setup result recorded for {runner.repo_id}"
            return [write_setup_result, report_complete]
        return [report_complete]

    def _get_role_content(self) -> str:
        """Load and prepare role file content."""
        role_file = {
            "setup": ROLES_DIR / "setup.md",
            "audit": ROLES_DIR / "auditor.md",
            "exec": ROLES_DIR / "executor.md",
            "analyze": ROLES_DIR / "analyst.md",
            "non_ok": ROLES_DIR / "non_ok_author.md",
            "review": ROLES_DIR / "reviewer.md",
        }[self.stage]

        role_content = role_file.read_text()

        # Adapt relative paths to absolute paths
        role_content = (
            role_content
            .replace("../../../roles/", f"{ROLES_DIR}/")
            .replace("../../../repos/", f"{REPOS_DIR}/")
            .replace("../../../infra/", f"{LAB_SRC / 'infra'}/")
            .replace("../../../data/", f"{DATA_DIR}/")
            .replace("../../data/", f"{DATA_DIR}/")
        )
        role_content = role_content.replace("{repo_id}", self.repo_id)
        role_content = role_content.replace("{REPO_NAME}", self.repo_id)

        return role_content

    def build_prompt(self) -> str:
        role_content = self._get_role_content()

        if self.stage == "setup":
            clone_dir = find_clone(self.repo_id)
            base_prompt = (
                f"The repo_id is: {self.repo_id}\n"
                f"You are working inside: {clone_dir}\n\n"
                f"Set up this repository's development environment, run its "
                f"test suite, and generate a baseline coverage report.\n"
                f"When done, call write_setup_result and then report_complete.\n"
            )
            return role_content + "\n\n---\n\n" + base_prompt

        task_file = find_task_file(self.repo_id)
        if not task_file:
            raise FileNotFoundError(f"No task file for {self.repo_id}")

        base_prompt = f"Your tasks file is: {task_file}\nThe repo_id is: {self.repo_id}\n\n"

        if self.stage == "audit":
            heading_id = self.heading_id
            task_headings = _parse_task_headings(task_file)
            task_text = task_headings.get(heading_id, f"(Task {heading_id} — read from tasks file)")
            base_prompt += (
                f"You are auditing task **{heading_id}** only.\n\n"
                f"Task description:\n{task_text}\n\n"
                f"Verify this task's grounding, coherence, and solvability.\n"
                f"If the task needs correction, edit the tasks markdown, then "
                f"call write_audit_result with status='corrected'.\n"
                f"If it is fine, call write_audit_result with status='ok'.\n"
                f"Then call report_complete.\n"
            )

        elif self.stage == "exec":
            # One task per session
            heading_id = self.heading_id
            task_headings = _parse_task_headings(task_file)
            task_text = task_headings.get(heading_id, f"(Task {heading_id} — read from tasks file)")
            base_prompt += (
                f"You are solving task **{heading_id}** only.\n\n"
                f"Task description:\n{task_text}\n\n"
                f"Solve this one task, then call report_complete.\n"
            )

        elif self.stage == "analyze":
            heading_id = self.heading_id
            # Load the trace-derived candidates
            candidates_path = DATA_DIR / self.repo_id / "candidates" / f"{heading_id}.json"
            if candidates_path.exists():
                candidates = json.loads(candidates_path.read_text())
            else:
                candidates = []

            # Get the diff from git history
            clone_dir = find_clone(self.repo_id)
            diff_text = "(diff not available)"
            if clone_dir:
                import subprocess
                # Find the task commit by message pattern
                result = subprocess.run(
                    ["git", "log", "--all", "--oneline", f"--grep=task {heading_id}:"],
                    cwd=clone_dir, capture_output=True, text=True, check=False,
                )
                if result.stdout.strip():
                    commit_hash = result.stdout.strip().split("\n")[0].split()[0]
                    diff_result = subprocess.run(
                        ["git", "diff", f"{commit_hash}~1..{commit_hash}"],
                        cwd=clone_dir, capture_output=True, text=True, check=False,
                    )
                    if diff_result.stdout:
                        diff_text = diff_result.stdout

            task_headings = _parse_task_headings(task_file)
            task_text = task_headings.get(heading_id, f"(Task {heading_id})")

            # Get executor's completion summary from log
            exec_log = LOGS_DIR / "sessions" / f"{self.repo_id}_exec_{heading_id}.log"
            exec_summary = ""
            if exec_log.exists():
                log_content = exec_log.read_text()
                # Extract the last DONE message
                for line in reversed(log_content.split("\n")):
                    if "✅" in line or "DONE" in line.upper():
                        exec_summary = line.strip()
                        break

            base_prompt += (
                f"You are analyzing task **{heading_id}**.\n\n"
                f"Task description:\n{task_text}\n\n"
                f"## Diff\n```\n{diff_text}\n```\n\n"
                f"## Exploration map ({len(candidates)} candidate defs)\n"
                f"```json\n{json.dumps(candidates, indent=2)}\n```\n\n"
            )
            if exec_summary:
                base_prompt += f"## Executor summary\n{exec_summary}\n\n"
            base_prompt += (
                "Classify these candidates, write queries, and call "
                "write_ground_truth with the complete JSON.\n"
                "Then call report_complete.\n"
            )

        elif self.stage == "non_ok":
            base_prompt += (
                "Write non-OK queries (UNSAT, BROAD, AMBIG) for this repository.\n"
                "Explore the codebase thoroughly, then call write_non_ok_queries.\n"
                "When done, call report_complete.\n"
            )

        elif self.stage == "review":
            heading_id = self.heading_id
            gt_path = DATA_DIR / self.repo_id / "ground_truth" / f"{heading_id}.json"
            gt_content = ""
            if gt_path.exists():
                gt_content = gt_path.read_text()

            task_headings = _parse_task_headings(task_file)
            task_text = task_headings.get(heading_id, f"(Task {heading_id})")

            # Get the diff from git history
            clone_dir = find_clone(self.repo_id)
            diff_text = "(diff not available)"
            if clone_dir:
                import subprocess
                result = subprocess.run(
                    ["git", "log", "--all", "--oneline", f"--grep=task {heading_id}:"],
                    cwd=clone_dir, capture_output=True, text=True, check=False,
                )
                if result.stdout.strip():
                    commit_hash = result.stdout.strip().split("\n")[0].split()[0]
                    diff_result = subprocess.run(
                        ["git", "diff", f"{commit_hash}~1..{commit_hash}"],
                        cwd=clone_dir, capture_output=True, text=True, check=False,
                    )
                    if diff_result.stdout:
                        diff_text = diff_result.stdout

            base_prompt += (
                f"You are reviewing task **{heading_id}** only.\n\n"
                f"Task description:\n{task_text}\n\n"
                f"## Diff\n```\n{diff_text}\n```\n\n"
                f"## Ground truth JSON\n```json\n{gt_content}\n```\n\n"
                f"Review this task's ground truth. If corrections are needed, "
                f"call write_ground_truth with the corrected JSON.\n"
                f"Then call write_review_result with your findings.\n"
                f"Then call report_complete.\n"
            )

        return role_content + "\n\n---\n\n" + base_prompt

    async def run(self) -> None:
        from copilot import CopilotClient

        clone_dir = find_clone(self.repo_id)
        if not clone_dir:
            raise FileNotFoundError(f"No clone found for {self.repo_id}")

        suffix = f"/{self.heading_id}" if self.heading_id else ""
        self.log(f"{self.repo_id}/{self.stage}{suffix} cwd={clone_dir}", "START")
        self.status_line = "initializing..."

        for attempt in range(RATE_LIMIT_RETRIES):
            try:
                await self._run_session(clone_dir)
                return
            except Exception as e:
                if _is_retryable(e) and attempt < RATE_LIMIT_RETRIES - 1:
                    wait = RATE_LIMIT_BACKOFF[attempt]
                    self.log(
                        f"Retryable error, waiting {wait}s (attempt {attempt + 1}/{RATE_LIMIT_RETRIES}): {e}",
                        "ERROR",
                    )
                    self.status_line = f"⏳ retry in {wait}s"
                    await asyncio.sleep(wait)
                    continue
                raise

    async def _run_session(self, clone_dir: Path) -> None:
        from copilot import CopilotClient, PermissionHandler
        from trace_collector import TraceCollector

        client = CopilotClient({
            "cwd": str(clone_dir),
            "log_level": "warning",
        })
        await client.start()

        # Set up trace collector for exec sessions
        collector = None
        if self.stage == "exec" and self.heading_id:
            traces_dir = DATA_DIR / self.repo_id / "traces"
            collector = TraceCollector(self.repo_id, self.heading_id, traces_dir)

        try:
            session = await client.create_session({
                "model": MODELS[self.stage],
                "tools": self.build_tools(),
                "infinite_sessions": {"enabled": True},
                "on_permission_request": PermissionHandler.approve_all,
            })

            def on_event(event):
                etype = event.type.value if hasattr(event.type, "value") else str(event.type)
                if etype == "assistant.message":
                    content = getattr(event.data, "content", "") or ""
                    self.log(f"ASSISTANT: {content[:300]}")
                    for line in reversed(content.split("\n")):
                        line = line.strip()
                        if line and len(line) > 5:
                            self.status_line = line[:60]
                            break
                elif etype == "session.idle":
                    if not self.done_event.is_set():
                        self.log("Session ended naturally", "DONE")
                        self.done_event.set()

            session.on(on_event)

            # Attach trace collector (passive — before first send)
            if collector:
                session.on(collector.handle_event)

            prompt = self.build_prompt()
            self.log(f"PROMPT ({len(prompt)} chars)", "PROMPT")
            self.status_line = "prompt sent..."

            await session.send({"prompt": prompt})
            await asyncio.wait_for(self.done_event.wait(), timeout=5400)

            await session.disconnect()
        finally:
            # Flush trace if we were tracing
            if collector:
                trace_path = collector.flush()
                self.log(f"Trace written: {trace_path} ({collector.record_count} records)", "WRITE")

                # Run trace_to_candidates immediately
                self._run_candidate_extraction(clone_dir, trace_path)

            await client.stop()

        self.log(f"Completed in {self.elapsed()}", "DONE")

    def _run_candidate_extraction(self, clone_dir: Path, trace_path: Path) -> None:
        """Run deterministic candidate extraction after an exec session."""
        from trace_to_candidates import trace_to_candidates, write_candidates

        index_db = clone_dir / ".recon" / "index.db"
        if not index_db.exists():
            self.log(f"Index DB not found at {index_db} — skipping candidate extraction", "ERROR")
            return

        candidates = trace_to_candidates(
            trace_path=trace_path,
            index_db_path=index_db,
            clone_root=clone_dir,
        )

        output_path = DATA_DIR / self.repo_id / "candidates" / f"{self.heading_id}.json"
        write_candidates(candidates, output_path)
        self.log(f"Candidates written: {output_path} ({len(candidates)} defs)", "WRITE")


# ── Programmatic repo pre-work ──


def _run_repo_prework(repo_id: str) -> None:
    """Fast, deterministic pre-work before the setup agent session.

    1. Create output directories
    2. Remove git remotes
    3. Clean copilot-instructions.md
    4. Commit the changes

    Environment setup, dependency installation, and coverage are
    handled by the setup agent session that runs after this.
    """
    import subprocess

    clone_dir = find_clone(repo_id)
    if not clone_dir:
        raise FileNotFoundError(f"No clone found for {repo_id}")

    # Create output directories
    for subdir in ("ground_truth", "traces", "candidates", "audit", "review"):
        (DATA_DIR / repo_id / subdir).mkdir(parents=True, exist_ok=True)

    # Remove all git remotes
    result = subprocess.run(
        ["git", "remote"], cwd=clone_dir,
        capture_output=True, text=True, check=False,
    )
    for remote in result.stdout.strip().split("\n"):
        remote = remote.strip()
        if remote:
            subprocess.run(
                ["git", "remote", "remove", remote], cwd=clone_dir,
                capture_output=True, check=False,
            )

    # Clean copilot-instructions.md
    ci_path = clone_dir / ".github" / "copilot-instructions.md"
    enforcement = (
        "# MANDATORY INSTRUCTIONS — READ BEFORE DOING ANYTHING\n\n"
        "You MUST follow ALL instructions in the role file you were given.\n"
        "Every field in the JSON output MUST be completed — no nulls, no\n"
        "empty arrays, no skipped sections. Incomplete outputs will be\n"
        "rejected by the reviewer.\n"
    )
    if ci_path.exists():
        content = ci_path.read_text()
        import re as _re
        content = _re.sub(
            r"<!-- coderecon-instructions -->.*?<!-- /coderecon-instructions -->",
            "", content, flags=_re.DOTALL,
        )
        content = enforcement + "\n" + content.strip() + "\n"
    else:
        ci_path.parent.mkdir(parents=True, exist_ok=True)
        content = enforcement
    ci_path.write_text(content)

    subprocess.run(
        ["git", "add", "-A"], cwd=clone_dir,
        capture_output=True, check=False,
    )
    subprocess.run(
        ["git", "commit", "-m", "setup: clean copilot instructions + remove remotes",
         "--allow-empty"],
        cwd=clone_dir, capture_output=True, check=False,
    )


# ── Repo pipeline ──


async def run_repo_pipeline(
    repo_id: str,
    state: dict,
    sem: asyncio.Semaphore,
    stage_filter: str | None,
    runners: dict[str, SessionRunner],
    repo_locks: dict[str, asyncio.Lock],
) -> None:
    """Run all remaining stages for one repo, serialized via per-repo lock."""
    if repo_id not in repo_locks:
        repo_locks[repo_id] = asyncio.Lock()
    lock = repo_locks[repo_id]

    async with lock:
        while True:
            state_snap = load_state()
            rd = state_snap["repos"].get(repo_id, {})
            state["repos"][repo_id] = rd

            ns = next_stage_for_repo(rd)
            if ns is None:
                break
            if stage_filter and ns != stage_filter:
                break

            if ns == "setup":
                # Mechanical pre-work (dirs, remotes, copilot-instructions)
                # then setup agent session (env, deps, coverage)
                try:
                    _run_repo_prework(repo_id)
                except Exception as e:
                    rd["setup"] = {"status": "failed", "error": f"prework: {str(e)[:180]}"}
                    save_state(state)
                    break
                # Run agent session for env setup + coverage
                await _run_single_session(repo_id, ns, state, sem, runners)
            elif ns in ("audit", "exec", "analyze", "review"):
                # Per-task: one session per heading
                await _run_per_task_stage(repo_id, ns, state, sem, runners)
            else:
                # Single session: non_ok
                await _run_single_session(repo_id, ns, state, sem, runners)


async def _run_per_task_stage(
    repo_id: str,
    stage: str,
    state: dict,
    sem: asyncio.Semaphore,
    runners: dict[str, SessionRunner],
) -> None:
    """Run one session per task heading (audit, exec, analyze, or review).

    Exec sessions serialize (shared worktree). Audit, analyze, and
    review sessions run concurrently up to the semaphore limit.
    """
    rd = state["repos"].get(repo_id, {})
    attempts = rd.get(stage, {}).get("attempts", 0) + 1
    rd[stage] = {"status": "active", "attempts": attempts}
    save_state(state)

    artifact_kind = {
        "audit": "audit",
        "exec": "trace",
        "analyze": "ground_truth",
        "review": "review",
    }[stage]
    pending_headings = [
        h for h in ALL_HEADINGS
        if not task_artifact_exists(repo_id, h, artifact_kind)
    ]

    if not pending_headings:
        rd[stage] = {"status": "done"}
        save_state(state)
        return

    errors: list[str] = []

    if stage == "exec":
        # Serialize exec sessions (shared clone worktree)
        for heading_id in pending_headings:
            try:
                async with sem:
                    await _run_task_session(repo_id, stage, heading_id, state, runners)
            except Exception as e:
                errors.append(f"{heading_id}: {e}")
                if not _is_retryable(e):
                    break  # Fatal error — stop this repo
    else:
        # Audit, analyze, review sessions can run concurrently (read-only)
        async def _run_one(heading_id: str):
            try:
                async with sem:
                    await _run_task_session(repo_id, stage, heading_id, state, runners)
            except Exception as e:
                errors.append(f"{heading_id}: {e}")

        await asyncio.gather(
            *[_run_one(h) for h in pending_headings],
            return_exceptions=True,
        )

    # Check completion
    ok, reason, count = stage_artifacts_satisfied(repo_id, stage)
    if ok:
        rd[stage] = {"status": "done", "artifacts": count}
    elif errors:
        if attempts < MAX_ATTEMPTS:
            rd[stage] = {"status": "pending", "attempts": attempts, "note": "; ".join(errors[:3])}
        else:
            rd[stage] = {"status": "failed", "error": "; ".join(errors[:3]), "attempts": attempts}
    else:
        rd[stage] = {"status": "pending", "attempts": attempts, "note": reason}
    save_state(state)


async def _run_task_session(
    repo_id: str,
    stage: str,
    heading_id: str,
    state: dict,
    runners: dict[str, SessionRunner],
) -> None:
    """Run a single executor or analyst session for one heading."""
    runner = SessionRunner(repo_id, stage, state, heading_id=heading_id)
    key = f"{repo_id}/{stage}/{heading_id}"
    runners[key] = runner
    try:
        await runner.run()
    finally:
        runners.pop(key, None)


async def _run_single_session(
    repo_id: str,
    stage: str,
    state: dict,
    sem: asyncio.Semaphore,
    runners: dict[str, SessionRunner],
) -> None:
    """Run a single agent session for a 1-session-per-repo stage (setup, non_ok)."""
    rd = state["repos"].get(repo_id, {})
    attempts = rd.get(stage, {}).get("attempts", 0) + 1
    rd[stage] = {"status": "active", "attempts": attempts}
    save_state(state)

    runner = SessionRunner(repo_id, stage, state)
    runners[repo_id] = runner

    try:
        async with sem:
            await runner.run()

        if rd.get(stage, {}).get("status") == "active":
            rd[stage] = {"status": "done"}
            save_state(state)

    except asyncio.TimeoutError:
        runner.log(f"TIMEOUT after 90min (attempt {attempts}/{MAX_ATTEMPTS})", "ERROR")
        _archive_log(repo_id, stage, attempts)
        if attempts < MAX_ATTEMPTS:
            rd[stage] = {"status": "pending", "attempts": attempts}
        else:
            rd[stage] = {"status": "failed", "error": "timeout", "attempts": attempts}
        save_state(state)
    except Exception as e:
        runner.log(f"{e}", "ERROR")
        _archive_log(repo_id, stage, attempts)
        if attempts < MAX_ATTEMPTS:
            rd[stage] = {"status": "pending", "attempts": attempts}
        else:
            rd[stage] = {"status": "failed", "error": str(e)[:200], "attempts": attempts}
        save_state(state)
    finally:
        runners.pop(repo_id, None)


# ── Progress display ──


def cmd_status(state: dict) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    console = Console()
    total = len(state.get("repos", {}))

    stage_table = Table(show_header=True, box=None, padding=(0, 2))
    stage_table.add_column("Stage", style="bold")
    stage_table.add_column("Progress", min_width=32)
    stage_table.add_column("Done", justify="right")
    stage_table.add_column("Active", justify="right", style="cyan")
    stage_table.add_column("Failed", justify="right", style="red")
    stage_table.add_column("Pending", justify="right", style="dim")

    for s in STAGES:
        counts: dict[str, int] = {}
        for rd in state["repos"].values():
            st = rd.get(s, {}).get("status", "pending")
            counts[st] = counts.get(st, 0) + 1
        done = counts.get("merged", 0) + counts.get("done", 0)
        active = counts.get("active", 0)
        failed = counts.get("failed", 0)
        pending = counts.get("pending", 0)
        pct = done / total if total else 0
        filled = int(pct * 25)
        bar = f"[green]{'━' * filled}[/green][dim]{'─' * (25 - filled)}[/dim] {done*100//total}%"
        s_display = s.replace("_", " ").title()
        stage_table.add_row(
            s_display,
            bar,
            str(done),
            str(active) if active else "·",
            str(failed) if failed else "·",
            str(pending) if pending else "·",
        )

    console.print(
        Panel(
            stage_table,
            title="[bold]Ground Truth Pipeline v2[/bold]",
            border_style="blue",
        )
    )

    # Failed details
    failed_list = []
    for rid, rd in state["repos"].items():
        for s in STAGES:
            if rd.get(s, {}).get("status") == "failed":
                err = rd[s].get("error", "?")[:50]
                att = rd[s].get("attempts", "?")
                failed_list.append(f"  [red]✗[/red] {rid}/{s} (attempt {att}): {err}")
    if failed_list:
        console.print()
        for f in failed_list:
            console.print(f)


# ── Run command ──


async def cmd_run(
    state: dict,
    stage_filter: str | None,
    repo_filter: str | None,
    concurrency: int,
) -> None:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group

    console = Console()
    sem = asyncio.Semaphore(concurrency)
    runners: dict[str, SessionRunner] = {}
    repo_locks: dict[str, asyncio.Lock] = {}
    start_wall = time.time()

    # Recover orphaned 'active' from previous interrupted run
    n_recovered = recover_orphaned_active(state)
    if n_recovered:
        console.print(
            f"[yellow]Recovered {n_recovered} orphaned 'active' session(s)[/yellow]"
        )
        state = load_state()

    targets = all_eligible_tasks(state, stage_filter, repo_filter)
    if not targets:
        if pipeline_done(state):
            console.print("[bold green]✅ All stages complete![/bold green]")
        else:
            failed_repos = {
                rid for rid, rd in state["repos"].items()
                for s in STAGES if rd.get(s, {}).get("status") == "failed"
            }
            if failed_repos:
                console.print(
                    f"[yellow]{len(failed_repos)} repo(s) have failed stages. Run 'retry' to reset.[/yellow]"
                )
            else:
                console.print("[yellow]Nothing to do.[/yellow]")
        return

    target_repos = sorted(set(rid for rid, _ in targets))
    console.print(f"[bold]━━━ Launching pipelines for {len(target_repos)} repo(s) ━━━[/bold]")

    flight = [
        asyncio.create_task(
            run_repo_pipeline(rid, state, sem, stage_filter, runners, repo_locks)
        )
        for rid in target_repos
    ]

    def render_display() -> Panel:
        total = len(state.get("repos", {}))
        fresh = load_state()

        stage_lines = []
        for s in STAGES:
            done = sum(
                1 for rd in fresh["repos"].values()
                if rd.get(s, {}).get("status") in ("merged", "done")
            )
            active = sum(
                1 for rd in fresh["repos"].values()
                if rd.get(s, {}).get("status") == "active"
            )
            pct = done / total if total else 0
            filled = int(pct * 20)
            bar = f"[green]{'━' * filled}[/green][dim]{'─' * (20 - filled)}[/dim]"
            active_str = f" [cyan]+{active}[/cyan]" if active else ""
            s_name = s.replace("_", " ").title()
            stage_lines.append(
                Text.from_markup(f"  {s_name:10} {bar} {done}/{total}{active_str}")
            )

        if runners:
            session_table = Table(
                show_header=True, box=None, padding=(0, 1), show_edge=False,
            )
            session_table.add_column("", width=1)
            session_table.add_column("Session", style="bold", min_width=32)
            session_table.add_column("Time", justify="right", style="cyan", width=5)
            session_table.add_column("Status", max_width=40, no_wrap=True)

            for key in sorted(runners.keys()):
                runner = runners[key]
                session_table.add_row("●", key, runner.elapsed(), runner.status_line[:40])
        else:
            session_table = Text("[dim]  No active sessions[/dim]")

        wall_m = int((time.time() - start_wall) / 60)
        footer = Text.from_markup(
            f"  [cyan]{len(runners)}[/cyan]/{concurrency} active │ wall: {wall_m}m"
        )

        return Panel(
            Group(*stage_lines, Text(""), session_table, Text(""), footer),
            title="[bold]Ground Truth Pipeline v2[/bold]",
            border_style="blue",
        )

    with Live(render_display(), console=console, refresh_per_second=0.5) as live:
        while not all(t.done() for t in flight):
            live.update(render_display())
            await asyncio.sleep(2)
        live.update(render_display())

    for t, rid in zip(flight, target_repos):
        exc = t.exception() if t.done() and not t.cancelled() else None
        if exc:
            console.print(f"  [red]✗ {rid}: {exc}[/red]")

    state = load_state()
    if pipeline_done(state):
        console.print("[bold green]✅ All stages complete![/bold green]")
    else:
        failed_repos = {
            rid for rid, rd in state["repos"].items()
            for s in STAGES if rd.get(s, {}).get("status") == "failed"
        }
        if failed_repos:
            console.print(
                f"[yellow]{len(failed_repos)} repo(s) failed. Run 'retry' then 'run' again.[/yellow]"
            )


def _archive_log(repo_id: str, stage: str, attempt: int) -> None:
    src = LOGS_DIR / "sessions" / f"{repo_id}_{stage}.log"
    if src.exists():
        dst_dir = LOGS_DIR / "errors"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{repo_id}_{stage}_attempt{attempt}.log"
        shutil.copy2(src, dst)


# ── Other commands ──


def cmd_retry(state: dict, target: str | None) -> None:
    """Reset failed stages to pending."""
    reset_count = 0
    for rid, rd in state["repos"].items():
        if target and rid != target:
            continue
        for stage in STAGES:
            if rd.get(stage, {}).get("status") == "failed":
                attempts = rd[stage].get("attempts", 0)
                _archive_log(rid, stage, attempts)
                rd[stage] = {"status": "pending", "attempts": attempts}
                print(f"  RESET {rid}/{stage} → pending (attempt {attempts})")
                reset_count += 1
    if not reset_count:
        print("  No failed stages found.")
    save_state(state)


def cmd_logs(repo_id: str, stage: str, heading: str | None = None) -> None:
    suffix = f"_{heading}" if heading else ""
    log_path = LOGS_DIR / "sessions" / f"{repo_id}_{stage}{suffix}.log"
    if log_path.exists():
        print(log_path.read_text())
    else:
        err_dir = LOGS_DIR / "errors"
        if err_dir.exists():
            for f in sorted(err_dir.glob(f"{repo_id}_{stage}*.log")):
                print(f"=== {f.name} ===")
                print(f.read_text())
                print()
        else:
            print(f"No logs found for {repo_id}/{stage}{suffix}")


def cmd_collect(state: dict) -> None:
    """Merge per-task JSONs into JSONL for all completed repos."""
    merge_script = LAB_SRC / "infra" / "merge_ground_truth.py"
    for rid, rd in state["repos"].items():
        if rd.get("review", {}).get("status") in ("done", "merged"):
            repo_dir = DATA_DIR / rid
            if repo_dir.exists():
                import subprocess
                r = subprocess.run(
                    f"python3 {merge_script} {repo_dir}",
                    shell=True,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if r.returncode == 0:
                    print(f"  {rid}: merged")
                else:
                    print(f"  {rid}: FAILED — {r.stderr[:100]}")


# ── Main ──


def main():
    parser = argparse.ArgumentParser(description="GT pipeline orchestrator v2 (Copilot SDK)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    run_p = sub.add_parser("run")
    run_p.add_argument("--stage", choices=STAGES, default=None)
    run_p.add_argument("--repo", default=None)
    run_p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)

    retry_p = sub.add_parser("retry")
    retry_p.add_argument("target", nargs="?", default=None)

    logs_p = sub.add_parser("logs")
    logs_p.add_argument("repo_id")
    logs_p.add_argument("stage", choices=STAGES)
    logs_p.add_argument("--heading", default=None)

    sub.add_parser("collect")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    state = load_state()

    if args.command == "status":
        cmd_status(state)
    elif args.command == "run":
        asyncio.run(cmd_run(state, args.stage, args.repo, args.concurrency))
    elif args.command == "retry":
        cmd_retry(state, args.target)
    elif args.command == "logs":
        cmd_logs(args.repo_id, args.stage, getattr(args, "heading", None))
    elif args.command == "collect":
        cmd_collect(state)


if __name__ == "__main__":
    main()
