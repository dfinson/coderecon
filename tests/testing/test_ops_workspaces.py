"""Tests for workspace detection helpers."""

from pathlib import Path

from coderecon.testing.ops_workspaces import _is_prunable_path


class TestIsPrunablePath:
    def test_node_modules(self):
        assert _is_prunable_path(Path("node_modules/foo"))

    def test_venv(self):
        assert _is_prunable_path(Path(".venv/lib"))

    def test_normal_path(self):
        assert not _is_prunable_path(Path("src/app"))

    def test_packages_at_root_not_pruned(self):
        assert not _is_prunable_path(Path("packages/my-app"))

    def test_nested_prunable(self):
        assert _is_prunable_path(Path("src/node_modules/foo"))
