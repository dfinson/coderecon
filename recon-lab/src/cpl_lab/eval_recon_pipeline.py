"""End-to-end eval of the full recon pipeline (not the LLM reranker).

For each SWE-bench instance:
  1. Run recon_pipeline(problem_statement) — full stack:
       raw_signals → gate → file_ranker → LGBMRanker → cutoff → snippets
  2. Parse the gold patch to find GT defs (by hunk-span overlap in the index)
  3. Match each result loc to GT
  4. Compute NDCG, Hit, Precision, Recall, F1 at predicted_n and @5/@10

Output:
  ~/.recon/recon-lab/data/recon_pipeline_eval.jsonl  (one record per task)
  summary printed to stdout

Usage:
    python -m cpl_lab.eval_recon_pipeline
    python -m cpl_lab.eval_recon_pipeline --repo-filter pytest-dev/pytest
    python -m cpl_lab.eval_recon_pipeline --max-tasks 20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import re
import statistics
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

_INSTANCES = Path("~/.recon/recon-lab/clones/instances").expanduser()
_OUT = Path("~/.recon/recon-lab/data/recon_pipeline_eval.jsonl").expanduser()
_DEFAULT_DATASETS = [
    ("princeton-nlp/SWE-bench", "test"),
    ("princeton-nlp/SWE-bench_Verified", "test"),
]

# ── GT helpers (same logic as patch_ground_truth.py + build_scaffold_rerank_data.py) ──

_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _parse_patch(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Return {path: [(hunk_start, hunk_end), ...]} for all changed hunks."""
    result: dict[str, list[tuple[int, int]]] = {}
    cur_path = None
    for line in patch.split("\n"):
        m = _DIFF_HEADER.match(line)
        if m:
            cur_path = m.group(1)
            continue
        m = _HUNK_HEADER.match(line)
        if m and cur_path:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            end = start + max(count - 1, 0)
            result.setdefault(cur_path, []).append((start, end))
    return result


def _gt_locs_from_patch(patch: str, index_db: Path) -> set[str]:
    """Return set of loc strings that overlap patch hunks (via index)."""
    import sqlite3
    changed = _parse_patch(patch)
    if not changed:
        return set()

    con = sqlite3.connect(str(index_db))
    cur = con.cursor()
    gt: set[str] = set()

    for path, hunks in changed.items():
        rows = cur.execute(
            """
            SELECT d.name, d.kind, d.start_line, d.end_line
            FROM def_facts d
            JOIN files f ON d.file_id = f.id
            WHERE f.path = ?
            """,
            (path,),
        ).fetchall()
        for name, kind, start_line, end_line in rows:
            for hunk_start, hunk_end in hunks:
                if start_line <= hunk_end and end_line >= hunk_start:
                    loc = f"{kind} {name} {path}:{start_line}-{end_line}"
                    gt.add(loc)
                    break

    con.close()
    return gt


# ── loc matching ──────────────────────────────────────────────────────────────

def _loc_to_span(loc: str) -> tuple[str, int, int] | None:
    """Parse 'kind name path:start-end' → (path, start, end)."""
    # loc format: "{kind} {name} {path}:{start_line}-{end_line}"
    m = re.search(r"(\S+):(\d+)-(\d+)$", loc)
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3))


def _result_locs_are_gt(result_locs: list[str], gt_locs: set[str]) -> list[bool]:
    """Direct set membership first; fallback to span overlap for any miss."""
    # Build span index for GT
    gt_spans: dict[str, list[tuple[int, int]]] = {}
    for loc in gt_locs:
        span = _loc_to_span(loc)
        if span:
            p, s, e = span
            gt_spans.setdefault(p, []).append((s, e))

    flags: list[bool] = []
    for loc in result_locs:
        if loc in gt_locs:
            flags.append(True)
            continue
        span = _loc_to_span(loc)
        if span:
            p, s, e = span
            hit = any(s <= ge and e >= gs for gs, ge in gt_spans.get(p, []))
            flags.append(hit)
        else:
            flags.append(False)
    return flags


# ── metrics ───────────────────────────────────────────────────────────────────

def _ndcg(flags: list[bool], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, f in enumerate(flags[:k]) if f)
    n_gt = sum(flags)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(n_gt, k)))
    return dcg / ideal if ideal > 0 else 0.0


def _hit(flags: list[bool], k: int) -> float:
    return float(any(flags[:k]))


def _prf(flags: list[bool], n: int, n_gt: int) -> tuple[float, float, float]:
    tp = sum(flags[:n])
    p = tp / n if n > 0 else 0.0
    r = tp / n_gt if n_gt > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


# ── SWE-bench loader ──────────────────────────────────────────────────────────

def _load_swebench(repo_filter: str | None) -> list[tuple[str, str, str, str]]:
    """Return [(instance_id, repo_slug, problem_statement, patch)] filtered by repo."""
    from datasets import load_dataset

    records: list[tuple[str, str, str, str]] = []
    for ds_name, split in _DEFAULT_DATASETS:
        try:
            ds = load_dataset(ds_name, split=split, trust_remote_code=True)
        except Exception as exc:
            logger.warning("Could not load %s/%s: %s", ds_name, split, exc)
            continue
        for row in ds:
            slug = str(row.get("repo", ""))
            if repo_filter and repo_filter.lower() not in slug.lower():
                continue
            iid = str(row.get("instance_id", ""))
            ps = str(row.get("problem_statement", ""))
            patch = str(row.get("patch", ""))
            if iid and ps and patch:
                records.append((iid, slug, ps, patch))

    seen: set[str] = set()
    deduped = []
    for r in records:
        if r[0] not in seen:
            seen.add(r[0])
            deduped.append(r)
    return deduped


def _repo_prefix_from_wid(wid: str) -> str:
    return re.sub(r"_\d+$", "", wid)


def _repo_prefix_from_slug(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", slug).replace("__", "__")


# ── pipeline runner ───────────────────────────────────────────────────────────

def _load_context(clone_dir: Path):
    from coderecon.mcp.context import AppContext
    cp = clone_dir / ".recon"
    logging.disable(logging.INFO)
    ctx = AppContext.standalone(
        repo_root=clone_dir,
        db_path=cp / "index.db",
        tantivy_path=cp / "tantivy",
    )
    logging.disable(logging.NOTSET)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ctx.coordinator.load_existing())
    return ctx, loop


def _run_pipeline(ctx, loop, problem_statement: str) -> dict[str, Any]:
    from coderecon.mcp.tools.recon.pipeline import recon_pipeline
    return loop.run_until_complete(
        recon_pipeline(ctx, problem_statement[:2000])
    )


# ── Qwen reranker ─────────────────────────────────────────────────────────────

_QWEN_ENDPOINT = "http://localhost:11434/v1/chat/completions"
_QWEN_MODEL = "qwen2.5-coder:3b"
_QWEN_SYSTEM = (
    "You are a code relevance expert. Given a task description and a list of "
    "code definitions, return a JSON array of 1-based indices re-ordered from "
    "most to least relevant. Output ONLY the JSON array, nothing else."
)


def _render_result(i: int, r: dict) -> str:
    loc = r["loc"]
    text = r.get("snippet") or r.get("sig") or ""
    if text:
        return f"[{i + 1}] {loc}\n{text[:300]}"
    return f"[{i + 1}] {loc}"


def _call_qwen_rerank(
    ps: str, results: list[dict]
) -> tuple[list[int] | None, float]:
    """Call Qwen to rerank pipeline results.

    Returns (0-based reordered indices, latency_sec).
    """
    import time
    import urllib.request as _urlreq

    n = len(results)
    items = "\n\n".join(_render_result(i, r) for i, r in enumerate(results))
    user_msg = (
        f"Task:\n{ps[:1500]}\n\nCode definitions:\n{items}\n\n"
        "Return a JSON array of 1-based indices ordered from most to least relevant."
    )

    payload = json.dumps({
        "model": _QWEN_MODEL,
        "messages": [
            {"role": "system", "content": _QWEN_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0,
        "stream": False,
    }).encode()

    t0 = time.perf_counter()
    try:
        req = _urlreq.Request(
            _QWEN_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with _urlreq.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        latency = time.perf_counter() - t0
    except Exception as exc:
        logger.warning("Qwen call failed: %s", exc)
        return None, 0.0

    raw = body["choices"][0]["message"]["content"].strip()
    m = re.search(r"\[[\d,\s]+\]", raw)
    if not m:
        return None, latency
    try:
        indices = json.loads(m.group(0))
    except Exception:
        return None, latency

    # Convert 1-based → 0-based, dedupe, append any missing
    seen: set[int] = set()
    out: list[int] = []
    for idx in indices:
        z = int(idx) - 1
        if 0 <= z < n and z not in seen:
            seen.add(z)
            out.append(z)
    for z in range(n):
        if z not in seen:
            out.append(z)
    return out, latency


# ── main ─────────────────────────────────────────────────────────────────────

def run(
    instances_dir: Path,
    out_file: Path,
    repo_filter: str | None,
    max_tasks: int,
    skip_qwen: bool = False,
) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Find indexed anchor per repo
    anchors: dict[str, Path] = {}
    for d in sorted(instances_dir.iterdir()):
        if d.is_dir() and (d / ".recon" / "index.db").exists():
            prefix = _repo_prefix_from_wid(d.name)
            if prefix not in anchors:
                anchors[prefix] = d

    if not anchors:
        raise RuntimeError(f"No indexed instance dirs under {instances_dir}")

    print(f"Anchors found: {list(anchors.keys())}")

    print("Loading SWE-bench ...", flush=True)
    records = _load_swebench(repo_filter)
    print(f"  {len(records)} instances", flush=True)

    if max_tasks > 0:
        records = records[:max_tasks]

    # Aggregate metrics
    ndcg5_list: list[float] = []
    ndcg10_list: list[float] = []
    hit5_list: list[float] = []
    hit10_list: list[float] = []
    prec_list: list[float] = []
    rec_list: list[float] = []
    f1_list: list[float] = []
    predN_list: list[int] = []
    no_gt = 0
    gate_blocked = 0
    errors = 0

    qwen_ndcg5_list: list[float] = []
    qwen_ndcg10_list: list[float] = []
    qwen_f1_list: list[float] = []
    qwen_lat_list: list[float] = []

    ctx_cache: dict[str, Any] = {}

    if skip_qwen:
        print(f"\n{'Task':<50} {'n':>4} {'gt':>4} {'ndcg@5':>7} {'ndcg@10':>8} {'hit@5':>6} {'hit@10':>7} {'f1@n':>6} {'predN':>6}")
        print("-" * 110)
    else:
        print(f"\n{'Task':<50} {'n':>4} {'gt':>4} {'ndcg@5':>7} {'ndcg@10':>8} {'f1@n':>6} | {'q_nd5':>7} {'q_nd10':>8} {'q_f1':>6} {'lat':>5}")
        print("-" * 120)

    with out_file.open("w") as fout:
        for iid, slug, ps, patch in records:
            wid = iid.replace("-", "_")
            prefix = _repo_prefix_from_wid(wid)

            # Normalize slug prefix match
            slug_prefix = _repo_prefix_from_slug(slug)
            anchor = anchors.get(prefix) or anchors.get(slug_prefix)
            if anchor is None:
                logger.debug("No anchor for %s (prefix=%s) — skipping", iid, prefix)
                continue

            # Load/reuse context
            if prefix not in ctx_cache:
                try:
                    ctx, loop = _load_context(anchor)
                    ctx_cache[prefix] = (ctx, loop)
                except Exception as exc:
                    logger.warning("Failed to load context for %s: %s", prefix, exc)
                    errors += 1
                    continue
            ctx, loop = ctx_cache[prefix]

            # Build GT from patch + index
            index_db = anchor / ".recon" / "index.db"
            gt_locs = _gt_locs_from_patch(patch, index_db)
            if not gt_locs:
                no_gt += 1
                continue

            # Run full recon pipeline
            try:
                result = _run_pipeline(ctx, loop, ps)
            except Exception as exc:
                logger.warning("Pipeline failed for %s: %s", iid, exc)
                errors += 1
                continue

            gate = result.get("gate", "?")
            results = result.get("results", [])
            predicted_n = result.get("n", 0)

            if not results or gate != "OK":
                gate_blocked += 1
                label = iid.replace("pytest-dev__pytest-", "pytest-")[:49]
                print(f"{label:<50} {'GATE:'+gate:>4}", flush=True)
                fout.write(json.dumps({
                    "task_id": iid, "gate": gate, "results": [],
                    "predicted_n": 0, "gt_locs": list(gt_locs),
                    "metrics": {},
                }) + "\n")
                continue

            result_locs = [r["loc"] for r in results]
            flags = _result_locs_are_gt(result_locs, gt_locs)
            n_gt = len(gt_locs)

            nd5 = _ndcg(flags, 5)
            nd10 = _ndcg(flags, 10)
            ht5 = _hit(flags, 5)
            ht10 = _hit(flags, 10)
            p, r, f1 = _prf(flags, predicted_n, n_gt)

            ndcg5_list.append(nd5)
            ndcg10_list.append(nd10)
            hit5_list.append(ht5)
            hit10_list.append(ht10)
            prec_list.append(p)
            rec_list.append(r)
            f1_list.append(f1)
            predN_list.append(predicted_n)

            # -- Qwen reranking --
            qnd5 = qnd10 = qf1 = 0.0
            qwen_latency = 0.0
            qwen_order = None
            qwen_flags = flags
            if not skip_qwen:
                qwen_order, qwen_latency = _call_qwen_rerank(ps, results)
                if qwen_order is not None:
                    reranked_locs = [result_locs[i] for i in qwen_order]
                    qwen_flags = _result_locs_are_gt(reranked_locs, gt_locs)
                    qnd5 = _ndcg(qwen_flags, 5)
                    qnd10 = _ndcg(qwen_flags, 10)
                    _, _, qf1 = _prf(qwen_flags, predicted_n, n_gt)
                    qwen_ndcg5_list.append(qnd5)
                    qwen_ndcg10_list.append(qnd10)
                    qwen_f1_list.append(qf1)
                    qwen_lat_list.append(qwen_latency)

            label = iid.replace("pytest-dev__pytest-", "pytest-")[:49]
            if skip_qwen:
                print(
                    f"{label:<50} {len(results):>4} {n_gt:>4} {nd5:>7.3f} {nd10:>8.3f} "
                    f"{ht5:>6.0f} {ht10:>7.0f} {f1:>6.3f} {predicted_n:>6}",
                    flush=True,
                )
            else:
                print(
                    f"{label:<50} {len(results):>4} {n_gt:>4} {nd5:>7.3f} {nd10:>8.3f} "
                    f"{f1:>6.3f} | {qnd5:>7.3f} {qnd10:>8.3f} {qf1:>6.3f} {qwen_latency:>5.1f}",
                    flush=True,
                )

            qwen_record: dict = {}
            if not skip_qwen and qwen_order is not None:
                qwen_record = {
                    "ndcg_5": round(qnd5, 4),
                    "ndcg_10": round(qnd10, 4),
                    "f1": round(qf1, 4),
                    "latency_sec": round(qwen_latency, 3),
                }

            fout.write(json.dumps({
                "task_id": iid,
                "gate": gate,
                "predicted_n": predicted_n,
                "gt_locs": list(gt_locs),
                "result_locs": result_locs,
                "gt_flags": flags,
                "metrics": {
                    "ndcg_5": round(nd5, 4),
                    "ndcg_10": round(nd10, 4),
                    "hit_5": ht5,
                    "hit_10": ht10,
                    "precision": round(p, 4),
                    "recall": round(r, 4),
                    "f1": round(f1, 4),
                    "predicted_n": predicted_n,
                    "n_gt": n_gt,
                },
                "qwen_metrics": qwen_record,
                "results": [{"loc": r["loc"], "score": r.get("score")} for r in results],
            }) + "\n")

    print("\n" + "=" * 120)
    n = len(ndcg5_list)
    if n:
        p95_idx = max(0, int(math.ceil(0.95 * len(predN_list))) - 1)
        print(f"BASELINE  n={n}  no_gt={no_gt}  gate_blocked={gate_blocked}  errors={errors}")
        print(f"  ndcg@5        = {statistics.mean(ndcg5_list):.4f}")
        print(f"  ndcg@10       = {statistics.mean(ndcg10_list):.4f}")
        print(f"  hit@5         = {statistics.mean(hit5_list):.4f}")
        print(f"  hit@10        = {statistics.mean(hit10_list):.4f}")
        print(f"  precision@n   = {statistics.mean(prec_list):.4f}")
        print(f"  recall@n      = {statistics.mean(rec_list):.4f}")
        print(f"  f1@n          = {statistics.mean(f1_list):.4f}")
        print(f"  avg_pred_n    = {statistics.mean(predN_list):.1f}")
        print(f"  p95_pred_n    = {sorted(predN_list)[p95_idx]}")
        if not skip_qwen and qwen_ndcg5_list:
            qn = len(qwen_ndcg5_list)
            p95_lat_idx = max(0, int(math.ceil(0.95 * len(qwen_lat_list))) - 1)
            print(f"\nQWEN RERANKED  n={qn}")
            print(f"  ndcg@5        = {statistics.mean(qwen_ndcg5_list):.4f}  (Δ {statistics.mean(qwen_ndcg5_list) - statistics.mean(ndcg5_list):+.4f})")
            print(f"  ndcg@10       = {statistics.mean(qwen_ndcg10_list):.4f}  (Δ {statistics.mean(qwen_ndcg10_list) - statistics.mean(ndcg10_list):+.4f})")
            print(f"  f1@n          = {statistics.mean(qwen_f1_list):.4f}  (Δ {statistics.mean(qwen_f1_list) - statistics.mean(f1_list):+.4f})")
            print(f"  avg_latency   = {statistics.mean(qwen_lat_list):.2f}s")
            print(f"  p95_latency   = {sorted(qwen_lat_list)[p95_lat_idx]:.2f}s")
        print(f"\nResults written to: {out_file}")
    else:
        print("No tasks evaluated.")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--instances-dir", default=str(_INSTANCES))
    p.add_argument("--out", default=str(_OUT))
    p.add_argument("--repo-filter", default=None)
    p.add_argument("--max-tasks", type=int, default=0)
    p.add_argument("--skip-qwen", action="store_true", default=False)
    args = p.parse_args()
    run(
        instances_dir=Path(args.instances_dir).expanduser(),
        out_file=Path(args.out).expanduser(),
        repo_filter=args.repo_filter,
        max_tasks=args.max_tasks,
        skip_qwen=args.skip_qwen,
    )


if __name__ == "__main__":
    main()
