#!/usr/bin/env python3
"""Kick off the ground truth generation pipeline for one or more repos.

Usage:
    # Single repo:
    python kick.py python-pydantic

    # Multiple repos:
    python kick.py python-pydantic go-fiber rust-axum

    # All eval repos:
    python kick.py --set eval

    # All repos in all sets:
    python kick.py --set all

    # Dry run (show what would happen):
    python kick.py --dry-run python-pydantic

Prerequisites:
    - `gh` CLI authenticated with a token that has: repo, delete_repo, workflow scopes
    - Copilot coding agent enabled for your account
    - The GT_PAT secret must be set on each fork (the script does this automatically)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RANKING_DIR = Path(__file__).resolve().parent.parent
REPOS_DIR = RANKING_DIR / "repos"
ROLES_DIR = RANKING_DIR / "roles"
PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"

CODEPLANE_REPO = "dfinson/codeplane"
BASE_BRANCH = "gt-generation"  # branch name in the fork


def run(cmd: str, *, check: bool = True, capture: bool = True) -> str:
    """Run a shell command and return stdout."""
    result = subprocess.run(
        cmd, shell=True, capture_output=capture, text=True, check=check
    )
    return result.stdout.strip() if capture else ""


def gh_api(method: str, path: str, data: dict | None = None) -> dict:
    """Call GitHub API via gh CLI."""
    cmd = f'gh api --method {method} -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" "{path}"'
    if data is not None:
        json_str = json.dumps(data)
        cmd += f" --input - <<< '{json_str}'"
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def parse_task_file(path: Path) -> dict:
    """Extract metadata from a task markdown file."""
    content = path.read_text()

    url_match = re.search(r"\*\*URL\*\*\s*\|\s*(https://github\.com/([^/\s]+/[^/\s|]+))", content)
    commit_match = re.search(r"\*\*Commit\*\*\s*\|\s*`([a-f0-9]+)`", content)
    language_match = re.search(r"\*\*Language\*\*\s*\|\s*(\w+)", content)
    set_match = re.search(r"\*\*Set\*\*\s*\|\s*(\w[\w-]*)", content)

    if not url_match or not commit_match:
        raise ValueError(f"Cannot parse metadata from {path}")

    return {
        "url": url_match.group(1),
        "upstream": url_match.group(2),  # e.g. "pydantic/pydantic"
        "commit": commit_match.group(1),
        "language": language_match.group(1) if language_match else "unknown",
        "set": set_match.group(1) if set_match else "unknown",
        "repo_id": path.stem,  # e.g. "python-pydantic"
        "content": content,
    }


def get_gh_user() -> str:
    """Get the authenticated GitHub username."""
    return run("gh api /user -q .login")


def fork_exists(user: str, repo_name: str) -> bool:
    """Check if the fork already exists."""
    result = subprocess.run(
        f'gh api "/repos/{user}/{repo_name}" -q .full_name',
        shell=True, capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def create_fork(upstream: str, user: str) -> str:
    """Fork the upstream repo. Returns the fork's full name."""
    repo_name = upstream.split("/")[1]

    if fork_exists(user, repo_name):
        print(f"  Fork {user}/{repo_name} already exists")
        return f"{user}/{repo_name}"

    run(f"gh repo fork {upstream} --clone=false")
    # Wait for fork to be ready
    for i in range(30):
        if fork_exists(user, repo_name):
            print(f"  Fork ready: {user}/{repo_name}")
            return f"{user}/{repo_name}"
        time.sleep(2)
        print(f"  Waiting for fork... ({i+1})")

    raise RuntimeError(f"Fork {user}/{repo_name} not ready after 60s")


def setup_fork_branch(fork: str, commit: str) -> None:
    """Create the gt-generation branch at the specified commit."""
    # Check if branch already exists
    result = subprocess.run(
        f'gh api "/repos/{fork}/git/ref/heads/{BASE_BRANCH}" -q .ref',
        shell=True, capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        print(f"  Branch {BASE_BRANCH} already exists")
        return

    # Create branch at the exact commit
    data = {"ref": f"refs/heads/{BASE_BRANCH}", "sha": commit}
    run(
        f'gh api --method POST "/repos/{fork}/git/refs" '
        f'-f ref="refs/heads/{BASE_BRANCH}" -f sha="{commit}"'
    )
    print(f"  Created branch {BASE_BRANCH} at {commit[:8]}")


def inject_pipeline_files(
    fork: str, meta: dict, user: str
) -> None:
    """Push the Actions workflow, role files, task file, and config to the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Clone the fork at the gt-generation branch
        run(
            f"git clone --branch {BASE_BRANCH} --depth 1 "
            f"https://x-access-token:$(gh auth token)@github.com/{fork}.git {tmp}/repo",
            capture=False,
        )
        repo_dir = tmp / "repo"

        # 1. Copy Actions workflow
        workflows_dir = repo_dir / ".github" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            PIPELINE_DIR / ".github" / "workflows" / "gt-pipeline.yml",
            workflows_dir / "gt-pipeline.yml",
        )

        # 2. Copy role files
        roles_dest = repo_dir / ".gt-roles"
        roles_dest.mkdir(exist_ok=True)
        for role_file in ["auditor.md", "executor.md", "reviewer.md"]:
            src = ROLES_DIR / role_file
            if src.exists():
                content = src.read_text()
                # Adapt paths: ../../data/{repo_id}/ground_truth/ → ground_truth/
                content = content.replace(
                    "../../data/{repo_id}/ground_truth/",
                    "ground_truth/",
                )
                content = content.replace(
                    "`../../data/{repo_id}/ground_truth/{heading_id}.json`",
                    "`ground_truth/{heading_id}.json`",
                )
                content = content.replace(
                    "`../../data/{repo_id}/non_ok_queries.json`",
                    "`ground_truth/non_ok_queries.json`",
                )
                # Fix reviewer merge script path
                content = content.replace(
                    "python ../../../infra/merge_ground_truth.py ../../data/{REPO_NAME}",
                    "# Merge script will be run automatically by the pipeline",
                )
                (roles_dest / role_file).write_text(content)

        # 3. Copy task file
        task_path = None
        for set_dir in REPOS_DIR.iterdir():
            candidate = set_dir / f"{meta['repo_id']}.md"
            if candidate.exists():
                task_path = candidate
                break
        if task_path:
            shutil.copy2(task_path, repo_dir / ".gt-tasks.md")

        # 4. Write pipeline config
        config = {
            "repo_id": meta["repo_id"],
            "set": meta["set"],
            "upstream": meta["upstream"],
            "commit": meta["commit"],
            "language": meta["language"],
            "codeplane_repo": CODEPLANE_REPO,
            "base_branch": BASE_BRANCH,
        }
        (repo_dir / ".gt-pipeline.json").write_text(
            json.dumps(config, indent=2) + "\n"
        )

        # 5. Create ground_truth directory with .gitkeep
        gt_dir = repo_dir / "ground_truth"
        gt_dir.mkdir(exist_ok=True)
        (gt_dir / ".gitkeep").touch()

        # 6. Commit and push
        run(f"cd {repo_dir} && git add -A", capture=False)
        run(
            f'cd {repo_dir} && git -c user.name="gt-pipeline" '
            f'-c user.email="gt-pipeline@noreply.github.com" '
            f'commit -m "chore: inject GT pipeline files"',
            capture=False,
        )
        run(f"cd {repo_dir} && git push origin {BASE_BRANCH}", capture=False)
        print("  Pipeline files injected")


def set_fork_secret(fork: str, secret_name: str) -> None:
    """Set a repository secret on the fork using the current gh auth token."""
    token = run("gh auth token")
    run(f'gh secret set {secret_name} --repo {fork} --body "{token}"')
    print(f"  Secret {secret_name} set on {fork}")


def create_auditor_issue(fork: str, meta: dict) -> int:
    """Create the auditor issue to kick off the pipeline. Returns issue number."""
    import requests as req

    token = run("gh auth token")
    repo_id = meta["repo_id"]

    body = (
        "## Instructions\n\n"
        "Your role instructions are in `.gt-roles/auditor.md` in this repo. Read them thoroughly.\n"
        "Your tasks file is `.gt-tasks.md` in this repo. Read it thoroughly.\n\n"
        "**Important path adaptations:**\n"
        f"- The output directory is `ground_truth/` at repo root (not `../../data/{repo_id}/ground_truth/`)\n"
        f"- The repo_id is `{repo_id}`\n\n"
        "**Pre-flight steps to execute (in order):**\n"
        "1. Verify commit matches the tasks file metadata\n"
        "2. Remove all git remotes (safety)\n"
        "3. Create output directory: `mkdir -p ground_truth`\n"
        "4. Clean/create `.github/copilot-instructions.md` (remove codeplane MCP instructions, add enforcement text)\n"
        "5. Run baseline coverage and commit the report\n"
        "6. Audit all 33 tasks (N1-N11, M1-M11, W1-W11)\n\n"
        "Begin."
    )

    payload = {
        "title": f"GT Auditor — {repo_id}",
        "body": body,
        "labels": ["gt:auditor"],
        "assignees": ["copilot-swe-agent[bot]"],
        "agent_assignment": {
            "target_repo": fork,
            "base_branch": BASE_BRANCH,
            "custom_instructions": (
                f"You are the pre-flight auditor. Read .gt-roles/auditor.md for your full instructions. "
                f"Read .gt-tasks.md for the tasks. The repo_id is {repo_id}. "
                f"Write output to ground_truth/ at repo root (not ../../data/). "
                f"After auditing, your PR will be auto-merged and executor sessions will start automatically."
            ),
            "custom_agent": "",
            "model": "claude-sonnet-4.6",
        },
    }

    r = req.post(
        f"https://api.github.com/repos/{fork}/issues",
        headers={
            "Authorization": f"bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=payload,
    )
    r.raise_for_status()
    issue = r.json()
    print(f"  Auditor issue created: {issue['html_url']}")
    return issue["number"]


def enable_actions(fork: str) -> None:
    """Enable GitHub Actions on the fork."""
    run(
        f'gh api --method PUT "/repos/{fork}/actions/permissions" '
        f'-f enabled=true -f allowed_actions=all',
        check=False,
    )
    print("  Actions enabled")


def disable_branch_protection(fork: str) -> None:
    """Ensure no branch protection on gt-generation."""
    run(
        f'gh api --method DELETE "/repos/{fork}/branches/{BASE_BRANCH}/protection"',
        check=False,
    )


def create_labels(fork: str) -> None:
    """Create the pipeline labels if they don't exist."""
    labels = [
        ("gt:auditor", "0E8A16", "Ground truth auditor stage"),
        ("gt:executor-a", "1D76DB", "Ground truth executor session A (N tasks)"),
        ("gt:executor-b", "1D76DB", "Ground truth executor session B (M tasks)"),
        ("gt:executor-c", "1D76DB", "Ground truth executor session C (W tasks)"),
        ("gt:reviewer", "D93F0B", "Ground truth reviewer stage"),
    ]
    for name, color, description in labels:
        run(
            f'gh api --method POST "/repos/{fork}/labels" '
            f'-f name="{name}" -f color="{color}" '
            f'-f description="{description}"',
            check=False,
        )
    print("  Labels created")


def kick_repo(repo_id: str, *, dry_run: bool = False) -> None:
    """Run the full kick sequence for one repo."""
    print(f"\n{'='*60}")
    print(f"Kicking: {repo_id}")
    print(f"{'='*60}")

    # Find task file
    task_path = None
    for set_dir in REPOS_DIR.iterdir():
        if not set_dir.is_dir():
            continue
        candidate = set_dir / f"{repo_id}.md"
        if candidate.exists():
            task_path = candidate
            break

    if not task_path:
        print(f"  ERROR: No task file found for {repo_id}")
        return

    meta = parse_task_file(task_path)
    user = get_gh_user()
    print(f"  Upstream: {meta['upstream']}")
    print(f"  Commit: {meta['commit'][:8]}")
    print(f"  Language: {meta['language']}")
    print(f"  Set: {meta['set']}")

    if dry_run:
        print("  DRY RUN — would fork, setup, and create auditor issue")
        return

    # 1. Fork
    print("\n  [1/6] Forking...")
    fork = create_fork(meta["upstream"], user)

    # 2. Create branch at commit
    print("  [2/6] Creating branch...")
    setup_fork_branch(fork, meta["commit"])

    # 3. Inject pipeline files
    print("  [3/6] Injecting pipeline files...")
    inject_pipeline_files(fork, meta, user)

    # 4. Setup fork (labels, actions, secrets)
    print("  [4/6] Configuring fork...")
    create_labels(fork)
    enable_actions(fork)
    disable_branch_protection(fork)
    set_fork_secret(fork, "GT_PAT")

    # 5. Create auditor issue
    print("  [5/6] Creating auditor issue...")
    issue_num = create_auditor_issue(fork, meta)

    print(f"\n  [6/6] Pipeline kicked!")
    print(f"  Fork: https://github.com/{fork}")
    print(f"  Auditor issue: https://github.com/{fork}/issues/{issue_num}")
    print(f"  The pipeline will self-propel from here.")


def get_repo_ids_for_set(set_name: str) -> list[str]:
    """Get all repo IDs for a given set (or all sets)."""
    ids = []
    if set_name == "all":
        dirs = [d for d in REPOS_DIR.iterdir() if d.is_dir()]
    else:
        dirs = [REPOS_DIR / set_name]

    for set_dir in dirs:
        if not set_dir.exists():
            print(f"  WARNING: Set directory {set_dir} not found")
            continue
        for f in sorted(set_dir.glob("*.md")):
            ids.append(f.stem)
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kick off ground truth generation pipeline"
    )
    parser.add_argument(
        "repos",
        nargs="*",
        help="Repo IDs to process (e.g., python-pydantic go-fiber)",
    )
    parser.add_argument(
        "--set",
        choices=["eval", "ranker-gate", "cutoff", "all"],
        help="Process all repos in this set",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without doing it",
    )
    args = parser.parse_args()

    if not args.repos and not args.set:
        parser.error("Provide repo IDs or --set")

    repo_ids = list(args.repos) if args.repos else []
    if args.set:
        repo_ids.extend(get_repo_ids_for_set(args.set))

    if not repo_ids:
        print("No repos to process")
        return

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for r in repo_ids:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    repo_ids = unique

    print(f"Processing {len(repo_ids)} repo(s): {', '.join(repo_ids)}")

    if args.dry_run:
        print("DRY RUN MODE")

    for repo_id in repo_ids:
        try:
            kick_repo(repo_id, dry_run=args.dry_run)
        except Exception as e:
            print(f"  ERROR processing {repo_id}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
