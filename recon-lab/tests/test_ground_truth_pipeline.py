from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from cpl_lab.collect_signals import _parse_gt_tables, _parse_raw_task_jsons
from cpl_lab.collect import _find_clone_dir
from cpl_lab.collector import iter_task_json_files
from cpl_lab.data_manifest import load_repo_manifest, write_repo_manifest
from cpl_lab.github_models import _token_budget_field, response_text
from cpl_lab.index import _find_recon_python, _recon_env, _recon_init_cmd
from cpl_lab.merge_signals import _align_to_merged
from cpl_lab.patch_ground_truth import parse_unified_diff
from cpl_lab.validate_ground_truth import validate_task
import pyarrow as pa


def test_parse_unified_diff_tracks_hunk_end_lines() -> None:
    file_diffs = parse_unified_diff(
        "diff --git a/src/app.py b/src/app.py\n@@ -10,2 +10,3 @@\n+return value\n"
    )

    assert file_diffs[0].path == "src/app.py"
    assert file_diffs[0].hunks[0].start_line == 10
    assert file_diffs[0].hunks[0].end_line == 12


def test_validate_task_accepts_swebench_narrow_queries() -> None:
    task = {
        "task_id": "django__django_101",
        "task_complexity": "narrow",
        "task_text": "Issue text",
        "diff": "diff",
        "solve_notes": "notes",
        "confidence": "high",
        "source": "swebench",
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
            {"query_type": "Q_STRUCTURAL", "query_text": "d", "seeds": [], "pins": [], "justification": "j"},
            {"query_type": "Q_NAVIGATIONAL", "query_text": "e", "seeds": [], "pins": [], "justification": "j"},
            {"query_type": "Q_FULL", "query_text": "f", "seeds": [], "pins": [], "justification": "j"},
        ],
    }

    assert validate_task(task, "task.json") == []


def test_iter_task_json_files_excludes_derived_files(tmp_path: Path) -> None:
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "django__django_101.json").write_text("{}")
    (gt_dir / "summary.json").write_text("{}")
    (gt_dir / "non_ok_queries.json").write_text("{}")

    assert [path.name for path in iter_task_json_files(gt_dir)] == ["django__django_101.json"]


def test_parse_gt_tables_reads_postprocessed_ground_truth(tmp_path: Path) -> None:
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "queries.jsonl").write_text(
        json.dumps(
            {
                "run_id": "django__django_101_django__django_101",
                "query_id": "django__django_101_django__django_101_q0",
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
                "run_id": "django__django_101_django__django_101",
                "candidate_key": "src/parser.py:function:parse:12",
                "tier": "minimum",
            }
        )
        + "\n"
    )

    queries, rel = _parse_gt_tables("django__django_101", gt_dir)

    assert queries[0]["task_id"] == "django__django_101"
    assert rel["django__django_101"]["src/parser.py:function:parse:12"] == 2


def test_parse_raw_task_jsons_supports_swebench_task_files(tmp_path: Path) -> None:
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "django__django_303.json").write_text(
        json.dumps(
            {
                "task_id": "django__django_303",
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

    assert queries[0]["task_id"] == "django__django_303"
    assert rel["django__django_303"]["src/app.py:function:run:9"] == 2


def test_repo_manifest_round_trip(tmp_path: Path) -> None:
    repo_dir = tmp_path / "django__django_101"
    write_repo_manifest(
        repo_dir,
        {
            "repo_set": "eval",
            "logical_repo_id": "django__django",
            "clone_dir": "/tmp/clone",
        },
    )

    manifest = load_repo_manifest(repo_dir)

    assert manifest["repo_set"] == "eval"
    assert manifest["logical_repo_id"] == "django__django"


def test_find_clone_dir_uses_repo_manifest_clone_path(tmp_path: Path) -> None:
    clones_dir = tmp_path / "clones"
    repo_dir = tmp_path / "data" / "django__django_101"
    clone_dir = clones_dir / "instances" / "django__django_101"
    write_repo_manifest(
        repo_dir,
        {
            "repo_set": "eval",
            "logical_repo_id": "django__django",
            "clone_dir": str(clone_dir),
        },
    )

    assert _find_clone_dir(clones_dir, repo_dir) == clone_dir


def test_align_to_merged_preserves_logical_repo_id() -> None:
    table = pa.table(
        {
            "task_id": ["django__django_101"],
            "query_id": ["django__django_101_q0"],
            "query_type": ["Q_FULL"],
            "candidate_key": ["src/app.py:function:run:9"],
            "path": ["src/app.py"],
            "kind": ["function"],
            "name": ["run"],
            "lexical_path": ["src/app.py"],
            "qualified_name": ["app.run"],
            "start_line": [9],
            "end_line": [12],
            "object_size_lines": [4],
            "file_ext": ["py"],
            "parent_dir": ["src"],
            "path_depth": [2],
            "has_docstring": [True],
            "has_decorators": [False],
            "has_return_type": [False],
            "signature_text": ["def run()"],
            "namespace": ["app"],
            "nesting_depth": [0],
            "has_parent_scope": [False],
            "hub_score": [1],
            "is_test": [False],
            "term_match_count": [1.0],
            "term_total_matches": [1.0],
            "graph_edge_type": ["calls"],
            "graph_seed_rank": [1.0],
            "symbol_source": ["index"],
            "import_direction": ["none"],
            "retriever_hits": [1],
            "query_len": [10],
            "has_identifier": [True],
            "has_path": [False],
            "identifier_density": [0.1],
            "has_numbers": [False],
            "has_quoted_strings": [False],
            "term_count": [2],
            "label_relevant": [2],
            "repo_id": ["django__django_101"],
            "repo_set": ["eval"],
            "logical_repo_id": ["django__django"],
            "object_count": [10],
            "file_count": [2],
            "label_gate": ["OK"],
            "run_id": ["django__django_101"],
        }
    )

    aligned = _align_to_merged(table)

    assert "logical_repo_id" in aligned.column_names
    assert aligned.column("logical_repo_id").to_pylist() == ["django__django"]


def test_find_recon_python_prefers_repo_venv(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "coderecon"
    venv_python = repo_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/usr/bin/env python3\n")
    venv_python.chmod(0o755)

    fallback_python = tmp_path / "python3"
    fallback_python.write_text("#!/usr/bin/env python3\n")
    fallback_python.chmod(0o755)
    monkeypatch.setattr("sys.executable", str(fallback_python))

    assert _find_recon_python(repo_root) == str(venv_python)


def test_recon_env_prepends_coderecon_src(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "coderecon"
    monkeypatch.setenv("PYTHONPATH", "existing:path")

    env = _recon_env(repo_root)

    assert env["PYTHONPATH"] == f"{repo_root / 'src'}:existing:path"


def test_token_budget_field_uses_gpt5_compat_name() -> None:
    assert _token_budget_field("openai/gpt-4.1-mini") == "max_tokens"
    assert _token_budget_field("openai/gpt-5-mini") == "max_completion_tokens"


def test_response_text_supports_string_and_part_lists() -> None:
    assert response_text({"choices": [{"message": {"content": "ok"}}]}) == "ok"
    assert response_text(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "o"},
                            {"type": "text", "text": "k"},
                        ]
                    }
                }
            ]
        }
    ) == "ok"


def test_recon_init_cmd_supports_reindex_flag(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "coderecon"
    venv_python = repo_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/usr/bin/env python3\n")
    venv_python.chmod(0o755)
    monkeypatch.setattr("cpl_lab.index._coderecon_repo_root", lambda: repo_root)

    cmd, _ = _recon_init_cmd(tmp_path / "repo", reindex=True)

    assert cmd[-3:] == ["init", "-r", str(tmp_path / "repo")]