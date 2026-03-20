from __future__ import annotations

import json
from pathlib import Path

from cpl_lab.collect_signals import _parse_gt_tables, _parse_raw_task_jsons
from cpl_lab.collector import iter_task_json_files
from cpl_lab.pr_to_ground_truth import DefEntry, FileDiff, Hunk, assemble_task_json, generate_queries
from cpl_lab.validate_ground_truth import validate_task


def test_assemble_task_json_includes_end_lines() -> None:
    min_suff = [
        DefEntry(
            path="src/app.py",
            name="do_work",
            kind="function",
            start_line=10,
            end_line=22,
            reason="changed",
        )
    ]
    thrash_prev = [
        DefEntry(
            path="tests/test_app.py",
            name="test_do_work",
            kind="function",
            start_line=3,
            end_line=11,
            reason="test context",
        )
    ]
    task = assemble_task_json(
        task_id="PR-42",
        pr_number=42,
        pr_title="Fix edge case",
        issue_body="A sufficiently detailed issue body that explains the failure mode and expected behavior.",
        diff_text="diff --git a/src/app.py b/src/app.py\n@@ -10,2 +10,3 @@\n+return value\n",
        file_diffs=[FileDiff(path="src/app.py", hunks=(Hunk(start_line=10, line_count=3),))],
        min_suff=min_suff,
        thrash_prev=thrash_prev,
        excluded=[],
        queries=generate_queries(
            issue_title="Fix edge case",
            issue_body="A sufficiently detailed issue body that explains the failure mode and expected behavior.",
            min_suff_defs=min_suff,
            changed_paths=["src/app.py"],
            diff_text="diff --git a/src/app.py b/src/app.py\n@@ -10,2 +10,3 @@\n+return value\n",
        ),
    )

    assert task["minimum_sufficient_defs"][0]["end_line"] == 22
    assert task["thrash_preventing_defs"][0]["end_line"] == 11


def test_validate_task_allows_relaxed_pr_mining_narrow_queries() -> None:
    task = {
        "task_id": "PR-7",
        "task_complexity": "narrow",
        "task_text": "Issue text",
        "diff": "diff",
        "solve_notes": "notes",
        "confidence": "high",
        "source": "pr-mining",
        "minimum_sufficient_defs": [
            {
                "path": "src/app.py",
                "name": "do_work",
                "kind": "function",
                "start_line": 1,
                "end_line": 3,
                "reason": "changed",
            }
        ],
        "thrash_preventing_defs": [],
        "tier_difference_reasoning": "reason",
        "excluded_defs": [],
        "queries": [
            {"query_type": "Q_SEMANTIC", "query_text": "a", "seeds": [], "pins": [], "justification": "j"},
            {"query_type": "Q_IDENTIFIER", "query_text": "b", "seeds": [], "pins": [], "justification": "j"},
            {"query_type": "Q_LEXICAL", "query_text": "c", "seeds": [], "pins": [], "justification": "j"},
            {"query_type": "Q_FULL", "query_text": "d", "seeds": [], "pins": [], "justification": "j"},
        ],
    }

    assert validate_task(task, "task.json") == []


def test_iter_task_json_files_excludes_derived_files(tmp_path: Path) -> None:
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "PR-1.json").write_text("{}")
    (gt_dir / "summary.json").write_text("{}")
    (gt_dir / "non_ok_queries.json").write_text("{}")

    assert [path.name for path in iter_task_json_files(gt_dir)] == ["PR-1.json"]


def test_parse_gt_tables_reads_postprocessed_ground_truth(tmp_path: Path) -> None:
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "queries.jsonl").write_text(
        json.dumps(
            {
                "run_id": "python-flask_PR-1",
                "query_id": "python-flask_PR-1_q0",
                "query_text": "fix parser",
                "query_type": "Q_FULL",
                "seeds": ["parse"],
                "pins": ["src/parser.py"],
                "label_gate": "OK",
            }
        )
        + "\n"
    )
    (gt_dir / "touched_objects.jsonl").write_text(
        json.dumps(
            {
                "run_id": "python-flask_PR-1",
                "candidate_key": "src/parser.py:function:parse:12",
                "tier": "minimum",
            }
        )
        + "\n"
    )

    queries, rel = _parse_gt_tables("python-flask", gt_dir)

    assert queries[0]["task_id"] == "PR-1"
    assert rel["PR-1"]["src/parser.py:function:parse:12"] == 2


def test_parse_raw_task_jsons_supports_mined_task_files(tmp_path: Path) -> None:
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "PR-3.json").write_text(
        json.dumps(
            {
                "task_id": "PR-3",
                "minimum_sufficient_defs": [
                    {
                        "path": "src/app.py",
                        "kind": "function",
                        "name": "run",
                        "start_line": 9,
                    }
                ],
                "thrash_preventing_defs": [],
                "queries": [
                    {
                        "query_type": "Q_FULL",
                        "query_text": "fix run",
                        "seeds": ["run"],
                        "pins": ["src/app.py"],
                    }
                ],
            }
        )
    )

    queries, rel = _parse_raw_task_jsons(gt_dir)

    assert queries[0]["task_id"] == "PR-3"
    assert rel["PR-3"]["src/app.py:function:run:9"] == 2