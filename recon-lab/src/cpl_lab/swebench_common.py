"""SWE-bench shared infrastructure — dataclass, instance selection, git helpers."""

from __future__ import annotations

import hashlib
import subprocess
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from datasets import load_dataset


@dataclass(frozen=True)
class SwebenchInstance:
    instance_id: str
    repo: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str
    hints_text: str
    source_dataset: str
    source_split: str
    repo_set: str


def select_instances(
    *,
    repo_set: str,
    repo_filter: str | None,
    training_dataset: str,
    training_split: str,
    eval_dataset: str,
    eval_split: str,
    cutoff_mod: int,
    cutoff_remainder: int,
    supplemental_datasets: list[str] | None = None,
) -> Iterable[SwebenchInstance]:
    requested_sets = {repo_set} if repo_set != "all" else {"ranker-gate", "cutoff", "eval"}

    if requested_sets & {"ranker-gate", "cutoff"}:
        # Build exclusion set: eval IDs must never appear in training data.
        eval_ids: set[str] = set()
        for row in load_dataset(eval_dataset, split=eval_split):
            eval_ids.add(str(row["instance_id"]))

        seen: set[str] = set()

        # Primary training dataset.
        yield from _iter_training(
            training_dataset, training_split,
            requested_sets, eval_ids, seen, repo_filter, cutoff_mod, cutoff_remainder,
        )

        # Supplemental training datasets.
        for entry in supplemental_datasets or []:
            ds_id, ds_split = entry.split(":", 1)
            yield from _iter_training(
                ds_id, ds_split,
                requested_sets, eval_ids, seen, repo_filter, cutoff_mod, cutoff_remainder,
            )

    if "eval" in requested_sets:
        for row in load_dataset(eval_dataset, split=eval_split):
            instance = _row_to_instance(
                row,
                source_dataset=eval_dataset,
                source_split=eval_split,
                repo_set="eval",
            )
            if repo_matches(instance, repo_filter):
                yield instance


def _iter_training(
    dataset: str,
    split: str,
    requested_sets: set[str],
    eval_ids: set[str],
    seen: set[str],
    repo_filter: str | None,
    cutoff_mod: int,
    cutoff_remainder: int,
) -> Iterable[SwebenchInstance]:
    """Yield training instances from one dataset, skipping eval/duplicates."""
    for row in load_dataset(dataset, split=split):
        iid = str(row["instance_id"])
        if iid in eval_ids or iid in seen:
            continue
        seen.add(iid)
        instance = _row_to_instance(
            row,
            source_dataset=dataset,
            source_split=split,
            repo_set=_training_repo_set(
                instance_id=iid,
                cutoff_mod=cutoff_mod,
                cutoff_remainder=cutoff_remainder,
            ),
        )
        if instance.repo_set not in requested_sets:
            continue
        if repo_matches(instance, repo_filter):
            yield instance


def repo_matches(instance: SwebenchInstance, repo_filter: str | None) -> bool:
    if not repo_filter:
        return True
    repo_filter = repo_filter.strip().lower()
    logical_repo_id = logical_repo_id_from_slug(instance.repo)
    return repo_filter in {instance.repo.lower(), logical_repo_id.lower()}


def repo_name_matches(manifest: dict[str, Any], repo_filter: str) -> bool:
    """Check if manifest matches a --repo filter."""
    repo_filter = repo_filter.strip().lower()
    for key in ("source_repo", "logical_repo_id", "workspace_id"):
        val = manifest.get(key, "")
        if isinstance(val, str) and repo_filter in val.lower():
            return True
    return False


def workspace_id(instance_id: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in instance_id)


def logical_repo_id_from_slug(repo: str) -> str:
    return repo.replace("/", "__")


def first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return "SWE-bench issue"


def combined_patch(instance: SwebenchInstance) -> str:
    patches = [instance.patch.strip(), instance.test_patch.strip()]
    return "\n".join(part for part in patches if part)


def def_to_dict(entry: Any) -> dict[str, Any]:
    return {
        "path": entry.path,
        "name": entry.name,
        "kind": entry.kind,
        "start_line": entry.start_line,
        "end_line": entry.end_line,
        "reason": entry.reason,
    }


def dict_to_def(entry: dict[str, Any]) -> Any:
    from cpl_lab.patch_ground_truth import DefEntry

    return DefEntry(
        path=str(entry["path"]),
        name=str(entry["name"]),
        kind=str(entry["kind"]),
        start_line=int(entry["start_line"]),
        end_line=int(entry["end_line"]),
        reason=str(entry.get("reason", "")),
    )


# ── Git helpers ──────────────────────────────────────────────────


def run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


_repo_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


def ensure_repo_checkout(instance: SwebenchInstance, clones_dir: Path) -> Path:
    """Clone-only checkout — no ``recon init``.  Used by Phase 1."""
    repo_slug = instance.repo.replace("/", "__")
    with _repo_locks[repo_slug]:
        return _ensure_repo_checkout_unlocked(instance, clones_dir, repo_slug)


def _ensure_repo_checkout_unlocked(
    instance: SwebenchInstance, clones_dir: Path, repo_slug: str
) -> Path:
    mirror_root = clones_dir / "_swebench_mirrors"
    instance_root = clones_dir / "instances"
    mirror_root.mkdir(parents=True, exist_ok=True)
    instance_root.mkdir(parents=True, exist_ok=True)

    mirror_dir = mirror_root / repo_slug
    if not mirror_dir.exists():
        run_git(["clone", "--mirror", f"https://github.com/{instance.repo}.git", str(mirror_dir)])
    else:
        run_git(["remote", "update", "--prune"], cwd=mirror_dir)

    try:
        run_git(["rev-parse", "--verify", instance.base_commit], cwd=mirror_dir)
    except RuntimeError:
        run_git(["fetch", "origin", instance.base_commit, "--depth=1"], cwd=mirror_dir)

    instance_dir = instance_root / workspace_id(instance.instance_id)
    if not instance_dir.exists():
        run_git(["worktree", "add", "--detach", str(instance_dir), instance.base_commit], cwd=mirror_dir)
    else:
        current = run_git(["rev-parse", "HEAD"], cwd=instance_dir).strip()
        if current != instance.base_commit:
            raise RuntimeError(
                f"Existing checkout at {instance_dir} is at {current[:12]}, expected {instance.base_commit[:12]}"
            )

    return instance_dir


def ensure_instance_checkout(instance: SwebenchInstance, clones_dir: Path) -> Path:
    """Clone + recon init checkout.  Used by the legacy combined path."""
    mirror_root = clones_dir / "_swebench_mirrors"
    instance_root = clones_dir / "instances"
    mirror_root.mkdir(parents=True, exist_ok=True)
    instance_root.mkdir(parents=True, exist_ok=True)

    repo_slug = instance.repo.replace("/", "__")
    mirror_dir = mirror_root / repo_slug
    if not mirror_dir.exists():
        run_git(["clone", "--mirror", f"https://github.com/{instance.repo}.git", str(mirror_dir)])
    else:
        run_git(["remote", "update", "--prune"], cwd=mirror_dir)

    try:
        run_git(["rev-parse", "--verify", instance.base_commit], cwd=mirror_dir)
    except RuntimeError:
        run_git(["fetch", "origin", instance.base_commit, "--depth=1"], cwd=mirror_dir)

    instance_dir = instance_root / workspace_id(instance.instance_id)
    if not instance_dir.exists():
        run_git(["worktree", "add", "--detach", str(instance_dir), instance.base_commit], cwd=mirror_dir)
    else:
        current = run_git(["rev-parse", "HEAD"], cwd=instance_dir).strip()
        if current != instance.base_commit:
            raise RuntimeError(
                f"Existing instance checkout at {instance_dir} is pinned to {current[:12]}, expected {instance.base_commit[:12]}"
            )

    index_db = instance_dir / ".recon" / "index.db"
    if not index_db.exists():
        import subprocess as _sp

        from cpl_lab.index import _ensure_recon_models, _recon_init_cmd

        _ensure_recon_models()
        cmd, env = _recon_init_cmd(instance_dir, reindex=(instance_dir / ".recon").is_dir())
        result = _sp.run(cmd, env=env, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "recon init failed"
            raise RuntimeError(detail)

    return instance_dir


# ── Private helpers ──────────────────────────────────────────────


def _training_repo_set(*, instance_id: str, cutoff_mod: int, cutoff_remainder: int) -> str:
    digest = hashlib.sha1(instance_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % max(cutoff_mod, 1)
    return "cutoff" if bucket == cutoff_remainder else "ranker-gate"


def _row_to_instance(
    row: dict[str, Any],
    *,
    source_dataset: str,
    source_split: str,
    repo_set: str,
) -> SwebenchInstance:
    return SwebenchInstance(
        instance_id=str(row["instance_id"]),
        repo=str(row["repo"]),
        base_commit=str(row["base_commit"]),
        patch=str(row.get("patch", "") or ""),
        test_patch=str(row.get("test_patch", "") or ""),
        problem_statement=str(row.get("problem_statement", "") or ""),
        hints_text=str(row.get("hints_text", "") or ""),
        source_dataset=source_dataset,
        source_split=source_split,
        repo_set=repo_set,
    )
