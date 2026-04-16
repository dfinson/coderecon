"""Qwen probe with recon-style scaffolds (signature + docstring from source).

Same as probe_qwen.py but _render() reads the actual sig+docstring from the
cloned instance source files, matching what the recon pipeline endpoint sends
to the LLM.

Usage:
    python -m cpl_lab.probe_qwen_rich                      # all tasks, N=20
    python -m cpl_lab.probe_qwen_rich --n-tasks 20
    python -m cpl_lab.probe_qwen_rich --instances-dir ~/my/clones/instances
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.request
from pathlib import Path
from typing import Any


_FIXTURE = Path("~/.recon/recon-lab/data/scaffold_rerank_data.jsonl").expanduser()
_INSTANCES = Path("~/.recon/recon-lab/clones/instances").expanduser()
_ENDPOINT = "http://localhost:11434/v1/chat/completions"
_MODEL = "qwen2.5-coder:3b"
_TOP_N = 20


# ── source-file scaffold reader (mirrors pipeline.py _read_signature) ─────────

def _read_sig_doc(repo_root: Path, path: str, start_line: int, end_line: int) -> str | None:
    """Read first line(s) + docstring from source — same logic as recon pipeline."""
    full = repo_root / path
    if not full.exists():
        return None
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        span = lines[start:end]
        if not span:
            return None

        sig_lines = [span[0]]
        for ln in span[1:]:
            stripped = ln.strip()
            if not stripped or stripped.startswith('"""') or stripped.startswith("'''"):
                sig_lines.append(ln)
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                        break
                    for ln2 in span[len(sig_lines):]:
                        sig_lines.append(ln2)
                        if '"""' in ln2 or "'''" in ln2:
                            break
                break
            if stripped.endswith((":", "{", "->", ",")):
                sig_lines.append(ln)
            else:
                break

        return "\n".join(sig_lines[:10])
    except Exception:  # noqa: BLE001
        return None


def _task_id_to_workspace(task_id: str) -> str:
    """pytest-dev__pytest-10051  →  pytest_dev__pytest_10051"""
    return task_id.replace("-", "_")


# ── prompt ────────────────────────────────────────────────────────────────────

def _render_rich(i: int, c: dict, repo_root: Path) -> str:
    """Render candidate with source sig+docstring if available."""
    path = c.get("path", "?")
    kind = c.get("kind", "")
    name = c.get("name", "")
    start = c.get("start_line", 0)
    end = c.get("end_line", 0)

    src = _read_sig_doc(repo_root, path, start, end)
    if src:
        return f"[{i}] {path}  ({kind})\n{src}"

    # Fallback to index signature_text
    sig = (c.get("signature_text") or "").strip()
    ns = (c.get("namespace") or "").strip()
    if sig:
        return f"[{i}] {path}  ({kind})\n    {sig}"
    if ns and ns != name:
        return f"[{i}] {path}  ({kind})\n    {ns}.{name}"
    return f"[{i}] {path}:{kind}:{name}"


_SYSTEM = (
    "You are a code relevance expert. Given a bug report or feature request, "
    "rank the provided code definitions by relevance to the task.\n\n"
    "Output ONLY a JSON array of the candidate indices in descending order of "
    "relevance (most relevant first).\n"
    "Example output for 5 candidates: [3, 1, 5, 2, 4]\n\n"
    "Include every index exactly once. Do not add explanation, reasoning, or prose."
)


def _prompt(ps: str, cands: list[dict], repo_root: Path) -> str:
    scaffolds = "\n\n".join(_render_rich(i + 1, c, repo_root) for i, c in enumerate(cands))
    n = len(cands)
    return (
        f"## Task\n{ps[:1500].strip()}\n\n"
        f"## Candidate Definitions (presented in arbitrary order)\n\n"
        f"{scaffolds}\n\n"
        f"Rank these {n} candidates by relevance. Output a JSON array of all {n} indices (1-based)."
    )


# ── LLM call ─────────────────────────────────────────────────────────────────

def _call_qwen(
    ps: str, cands: list[dict], repo_root: Path, timeout: int = 120
) -> tuple[list[int] | None, float]:
    body = json.dumps({
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(ps, cands, repo_root)},
        ],
        "max_tokens": 512,
        "temperature": 0.0,
    }).encode()

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            _ENDPOINT, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = json.loads(resp.read())["choices"][0]["message"]["content"]
    except Exception:
        return None, time.monotonic() - t0
    elapsed = time.monotonic() - t0

    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None, elapsed
    try:
        arr = json.loads(text[start : end + 1])
    except Exception:
        return None, elapsed

    n = len(cands)
    if arr and min(arr) >= 1:
        arr = [x - 1 for x in arr]
    # supplement missing indices
    present = set(arr)
    arr = [x for x in arr if 0 <= x < n] + [i for i in range(n) if i not in present]
    return arr[:n], elapsed


# ── metrics ───────────────────────────────────────────────────────────────────

def _ndcg(ranked_keys: list[str], gt_keys: set[str], k: int) -> float:
    dcg = sum(
        1.0 / math.log2(i + 2)
        for i, key in enumerate(ranked_keys[:k])
        if key in gt_keys
    )
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gt_keys), k)))
    return dcg / ideal if ideal > 0 else 0.0


def _hit(ranked_keys: list[str], gt_keys: set[str], k: int) -> float:
    return float(any(key in gt_keys for key in ranked_keys[:k]))


def _f1(ranked_keys: list[str], gt_keys: set[str], predicted_n: int) -> tuple[float, float, float]:
    returned = set(ranked_keys[:predicted_n])
    tp = len(returned & gt_keys)
    p = tp / len(returned) if returned else 0.0
    r = tp / len(gt_keys) if gt_keys else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


# ── main ─────────────────────────────────────────────────────────────────────

def run(top_n: int, n_tasks: int, fixture: Path, instances_dir: Path) -> None:
    rows = [json.loads(l) for l in fixture.read_text().splitlines() if l.strip()]

    def gt_min_rank(r: dict) -> int:
        gt = set(r["gt_edited_keys"])
        cands = [c for c in r["candidates"] if c["def_key"] in gt]
        return min((c.get("baseline_rank") or 9999) for c in cands) if cands else 9999

    rows.sort(key=gt_min_rank)
    tasks = rows[:n_tasks] if n_tasks > 0 else rows

    ndcg_list: list[float] = []
    hit_list: list[float] = []
    lat_list: list[float] = []
    prec_list: list[float] = []
    rec_list: list[float] = []
    f1_list: list[float] = []
    parse_fails = 0
    predicted_n = 10  # fixed cutoff for P/R/F1, same as eval default

    print(f"\nQwen {_MODEL} @ N={top_n}  —  rich scaffolds (sig+docstring from source)")
    print(f"Tasks: {len(tasks)}\n")
    print(f"{'Task':<45} {'GT_rank':>7}  {'ndcg@k':>6}  {'hit':>3}  {'f1@10':>6}  {'lat':>6}  {'src%':>5}")
    print("-" * 90)

    for row in tasks:
        gt_keys = set(row["gt_edited_keys"])
        ps = row["problem_statement"]
        task_id = row["task_id"]
        wid = _task_id_to_workspace(task_id)
        repo_root = instances_dir / wid

        sorted_pool = sorted(row["candidates"], key=lambda c: c.get("baseline_rank") or 9999)
        gt_min = min((c.get("baseline_rank") or 9999) for c in sorted_pool if c["def_key"] in gt_keys)

        # Build window: guarantee GT in top_n
        gt_cands = [c for c in sorted_pool if c["def_key"] in gt_keys]
        non_gt = [c for c in sorted_pool if c["def_key"] not in gt_keys]
        headroom = max(0, top_n - len(gt_cands))
        window = non_gt[:headroom] + gt_cands
        window.sort(key=lambda c: c.get("baseline_rank") or 9999)
        tail = non_gt[headroom:]

        # Count how many got rich source scaffolds
        src_count = sum(
            1 for c in window
            if _read_sig_doc(repo_root, c.get("path", ""), c.get("start_line", 0), c.get("end_line", 0)) is not None
        )
        src_pct = round(100 * src_count / len(window)) if window else 0

        label = task_id.replace("pytest-dev__pytest-", "pytest-")[:44]
        ranked_arr, lat = _call_qwen(ps, window, repo_root)

        if ranked_arr is None:
            parse_fails += 1
            print(f"{label:<45} {gt_min:>7}  {'FAIL':>6}  {'-':>3}  {'FAIL':>6}  {lat:>5.1f}s  {src_pct:>4}%")
            continue

        ranked_keys = [window[i]["def_key"] for i in ranked_arr] + [c["def_key"] for c in tail]
        nd = _ndcg(ranked_keys, gt_keys, top_n)
        ht = _hit(ranked_keys, gt_keys, top_n)
        p, r, f1 = _f1(ranked_keys, gt_keys, predicted_n)
        ndcg_list.append(nd)
        hit_list.append(ht)
        lat_list.append(lat)
        prec_list.append(p)
        rec_list.append(r)
        f1_list.append(f1)
        print(f"{label:<45} {gt_min:>7}  {nd:>6.3f}  {ht:>3.0f}  {f1:>6.3f}  {lat:>5.1f}s  {src_pct:>4}%", flush=True)

    print("\n" + "=" * 90)
    if ndcg_list:
        print(f"SUMMARY  N={top_n}  rich scaffolds  (F1/P/R @ predicted_n={predicted_n})")
        print(f"  ndcg@{top_n}       = {statistics.mean(ndcg_list):.4f}")
        print(f"  hit@{top_n}        = {statistics.mean(hit_list):.4f}")
        print(f"  precision@{predicted_n}  = {statistics.mean(prec_list):.4f}")
        print(f"  recall@{predicted_n}     = {statistics.mean(rec_list):.4f}")
        print(f"  f1@{predicted_n}         = {statistics.mean(f1_list):.4f}")
        print(f"  avg_lat      = {statistics.mean(lat_list):.2f}s")
        p95 = sorted(lat_list)[max(0, int(math.ceil(0.95 * len(lat_list))) - 1)]
        print(f"  p95_lat      = {p95:.2f}s")
        print(f"  tasks        = {len(ndcg_list)}  parse_fails={parse_fails}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--top-n", type=int, default=_TOP_N)
    p.add_argument("--n-tasks", type=int, default=0, help="0 = all tasks")
    p.add_argument("--fixture", default=str(_FIXTURE))
    p.add_argument("--instances-dir", default=str(_INSTANCES))
    args = p.parse_args()
    run(
        top_n=args.top_n,
        n_tasks=args.n_tasks,
        fixture=Path(args.fixture).expanduser(),
        instances_dir=Path(args.instances_dir).expanduser(),
    )


if __name__ == "__main__":
    main()
