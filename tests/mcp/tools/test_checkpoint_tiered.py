"""Tests for checkpoint_tiered pure helper functions."""

from types import SimpleNamespace

from coderecon.mcp.tools.checkpoint_tiered import (
    _collect_tier_failures,
    _partition_for_batching,
    _summarize_verify,
)


class TestSummarizeVerify:
    def test_clean_lint_and_passing_tests(self):
        result = _summarize_verify("clean", 0, 10, 0, "completed")
        assert "lint: clean" in result
        assert "tests: 10 passed" in result

    def test_lint_issues_and_failures(self):
        result = _summarize_verify("issues", 3, 5, 2, "completed")
        assert "lint: 3 issues" in result
        assert "5 passed" in result
        assert "2 FAILED" in result

    def test_skipped_lint_and_tests(self):
        result = _summarize_verify("skipped", 0, 0, 0, "skipped")
        assert "lint: skipped" in result
        assert "tests: skipped" in result

    def test_no_tests_shows_status(self):
        result = _summarize_verify("clean", 0, 0, 0, "no_tests")
        assert "tests: no_tests" in result


class TestPartitionForBatching:
    @staticmethod
    def _target(runner_id, workspace, cost):
        return SimpleNamespace(
            runner_pack_id=runner_id,
            workspace_root=workspace,
            estimated_cost=cost,
        )

    def test_single_target_stays_solo(self):
        t = self._target("pytest", "/repo", 0.5)
        batch_groups, solo = _partition_for_batching([t])
        assert batch_groups == []
        assert solo == [t]

    def test_two_compatible_targets_batched(self):
        t1 = self._target("pytest", "/repo", 0.5)
        t2 = self._target("pytest", "/repo", 0.3)
        batch_groups, solo = _partition_for_batching([t1, t2])
        assert len(batch_groups) == 1
        assert sorted(batch_groups[0], key=id) == sorted([t1, t2], key=id)
        assert solo == []

    def test_high_cost_target_stays_solo(self):
        t1 = self._target("pytest", "/repo", 0.5)
        t2 = self._target("pytest", "/repo", 5.0)
        batch_groups, solo = _partition_for_batching([t1, t2])
        assert batch_groups == []
        assert t2 in solo
        assert t1 in solo

    def test_different_runners_not_batched(self):
        t1 = self._target("pytest", "/repo", 0.5)
        t2 = self._target("jest", "/repo", 0.5)
        batch_groups, solo = _partition_for_batching([t1, t2])
        assert batch_groups == []
        assert len(solo) == 2


class TestCollectTierFailures:
    def test_empty_results(self):
        lines: list[str] = []
        _collect_tier_failures(None, [], lines)
        assert lines == []

    def test_solo_failures_collected(self):
        failure = SimpleNamespace(
            path="tests/test_foo.py", line=10, name="test_bar",
            message="assert False", traceback=None,
        )
        solo = SimpleNamespace(run_status=SimpleNamespace(failures=[failure]))
        lines: list[str] = []
        _collect_tier_failures(solo, [], lines)
        assert any("test_foo.py:10" in ln for ln in lines)
        assert any("test_bar" in ln for ln in lines)

    def test_batch_failures_collected(self):
        tc = SimpleNamespace(
            status="failed", file_path="tests/x.py", line_number=5,
            name="test_x", message="boom", classname=None, traceback=None,
        )
        batch_result = SimpleNamespace(tests=[tc])
        lines: list[str] = []
        _collect_tier_failures(None, [batch_result], lines)
        assert any("tests/x.py:5" in ln for ln in lines)
        assert any("boom" in ln for ln in lines)
