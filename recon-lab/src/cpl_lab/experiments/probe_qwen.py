"""Manual Qwen probe — test at different candidate counts to find sweet spot.

Usage:
    python -m cpl_lab.probe_qwen
    python -m cpl_lab.probe_qwen --top-ns 5,10,15,20 --n-tasks 10
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


_FIXTURE = Path("~/.recon/recon-lab/data/scaffold_rerank_data.jsonl").expanduser()
_ENDPOINT = "http://localhost:11434/v1/chat/completions"
_MODEL = "qwen2.5-coder:3b"


# ── prompt (same as llm_reranker.py) ─────────────────────────────────────────

def _render(i: int, c: dict) -> str:
    sig = (c.get("signature_text") or "").strip()
    path = c.get("path", "?")
    kind = c.get("kind", "")
    name = c.get("name", "")
    ns = (c.get("namespace") or "").strip()
    if sig:
        return f"[{i}] {path}  ({kind})\n    {sig}"
    if ns and ns != name:
        return f"[{i}] {path}  ({kind})\n    {ns}.{name}"
    return f"[{i}] {path}:{kind}:{name}"


SYSTEM = (
    "You are a code relevance expert. Given a bug report or feature request, "
    "rank the provided code definitions by relevance to the task.\n\n"
    "Output ONLY a JSON array of the candidate indices in descending order of "
    "relevance (most relevant first).\n"
    "Example output for 5 candidates: [3, 1, 5, 2, 4]\n\n"
    "Include every index exactly once. Do not add explanation, reasoning, or prose."
)


def _prompt(ps: str, cands: list[dict]) -> str:
    scaffolds = "\n\n".join(_render(i + 1, c) for i, c in enumerate(cands))
    n = len(cands)
    return (
        f"## Task\n{ps[:1500].strip()}\n\n"
        f"## Candidate Definitions (presented in arbitrary order)\n\n"
        f"{scaffolds}\n\n"
        f"Rank these {n} candidates by relevance. Output a JSON array of all {n} indices (1-based)."
    )


def _call_qwen(ps: str, cands: list[dict], max_tokens: int = 512, timeout: int = 120) -> tuple[list[int] | None, float]:
    body = json.dumps({
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(ps, cands)},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }).encode()

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(_ENDPOINT, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = json.loads(resp.read())["choices"][0]["message"]["content"]
    except Exception as e:
        return None, time.monotonic() - t0
    elapsed = time.monotonic() - t0

    # Parse index array
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None, elapsed
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:
        return None, elapsed

    n = len(cands)
    # normalise to 0-based
    if arr and min(arr) >= 1:
        arr = [x - 1 for x in arr]
    # validate
    if sorted(arr) != list(range(n)):
        # supplement missing
        present = set(arr)
        arr = [x for x in arr if 0 <= x < n] + [i for i in range(n) if i not in present]
    return arr[:n], elapsed


# ── metrics ──────────────────────────────────────────────────────────────────

def _ndcg(ranked_keys: list[str], gt_keys: set[str], k: int) -> float:
    dcg = sum(
        1.0 / (i + 1)  # log2(i+2) denominator approximated as i+1 for simplicity
        for i, key in enumerate(ranked_keys[:k])
        if key in gt_keys
    )
    ideal = sum(1.0 / (i + 1) for i in range(min(len(gt_keys), k)))
    return dcg / ideal if ideal > 0 else 0.0


def _hit(ranked_keys: list[str], gt_keys: set[str], k: int) -> float:
    return float(any(key in gt_keys for key in ranked_keys[:k]))


# ── main ─────────────────────────────────────────────────────────────────────

def run(top_ns: list[int], n_tasks: int, fixture: Path) -> None:
    rows = [json.loads(l) for l in fixture.read_text().splitlines() if l.strip()]
    # Sort: tasks where GT is naturally near top first (easier), then harder
    def gt_min_baseline_rank(r: dict) -> int:
        gt_keys = set(r["gt_edited_keys"])
        gt_cands = [c for c in r["candidates"] if c["def_key"] in gt_keys]
        return min((c.get("baseline_rank") or 9999) for c in gt_cands) if gt_cands else 9999

    rows.sort(key=gt_min_baseline_rank)  # easiest (GT near top) to hardest (GT buried)
    tasks = rows[:n_tasks]

    print(f"\n{'Task':<45} {'GT_rank':>7}", end="")
    for tn in top_ns:
        print(f"  N={tn:2d}(ndcg@k lat)", end="")
    print()
    print("-" * 120)

    agg: dict[int, dict[str, list]] = {tn: {"ndcg": [], "hit": [], "lat": [], "parse_fail": 0} for tn in top_ns}

    for row in tasks:
        gt_keys = set(row["gt_edited_keys"])
        ps = row["problem_statement"]
        # Sort full pool by baseline_rank
        sorted_pool = sorted(row["candidates"], key=lambda c: c.get("baseline_rank") or 9999)
        gt_min = min((c.get("baseline_rank") or 9999) for c in sorted_pool if c["def_key"] in gt_keys)

        label = row["task_id"].replace("pytest-dev__pytest-", "pytest-")[:44]
        print(f"{label:<45} {gt_min:>7}", end="", flush=True)

        for top_n in top_ns:
            # Build window: guarantee GT in top_n
            gt_cands = [c for c in sorted_pool if c["def_key"] in gt_keys]
            non_gt = [c for c in sorted_pool if c["def_key"] not in gt_keys]
            headroom = max(0, top_n - len(gt_cands))
            window = non_gt[:headroom] + gt_cands
            window.sort(key=lambda c: c.get("baseline_rank") or 9999)
            tail = non_gt[headroom:]

            ranked_arr, lat = _call_qwen(ps, window)
            if ranked_arr is None:
                agg[top_n]["parse_fail"] += 1
                print(f"  {'FAIL':>14}", end="", flush=True)
                continue

            ranked_keys = [window[i]["def_key"] for i in ranked_arr] + [c["def_key"] for c in tail]
            nd = _ndcg(ranked_keys, gt_keys, top_n)
            ht = _hit(ranked_keys, gt_keys, top_n)
            agg[top_n]["ndcg"].append(nd)
            agg[top_n]["hit"].append(ht)
            agg[top_n]["lat"].append(lat)
            print(f"  {nd:.2f}/{ht:.0f} {lat:5.1f}s", end="", flush=True)

        print()

    print("\n" + "=" * 120)
    print(f"{'SUMMARY':<45} {'':>7}", end="")
    for tn in top_ns:
        print(f"  N={tn:2d}({'':8}     )", end="")
    print()

    for tn in top_ns:
        a = agg[tn]
        nd = statistics.mean(a["ndcg"]) if a["ndcg"] else 0
        ht = statistics.mean(a["hit"]) if a["hit"] else 0
        lt = statistics.mean(a["lat"]) if a["lat"] else 0
        print(f"  N={tn:2d}: ndcg@k={nd:.3f}  hit@k={ht:.2f}  lat_mean={lt:.1f}s  parse_fail={a['parse_fail']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--top-ns", default="5,10,15,20", help="Comma-separated candidate counts to test")
    p.add_argument("--n-tasks", type=int, default=10, help="Tasks to test (sorted easiest→hardest)")
    p.add_argument("--fixture", default=str(_FIXTURE))
    args = p.parse_args()
    top_ns = [int(x) for x in args.top_ns.split(",")]
    run(top_ns, args.n_tasks, Path(args.fixture).expanduser())


if __name__ == "__main__":
    main()
