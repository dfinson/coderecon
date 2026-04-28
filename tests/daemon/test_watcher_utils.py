"""Smoke tests for watcher_utils — file watcher helpers."""

from pathlib import Path

from coderecon.daemon.watcher_utils import (
    is_cross_filesystem,
    summarize_changes_by_type,
)


class TestIsCrossFilesystem:
    def test_wsl_mount(self) -> None:
        assert is_cross_filesystem(Path("/mnt/c/Users")) is True

    def test_normal_linux_path(self) -> None:
        assert is_cross_filesystem(Path("/home/user/project")) is False

    def test_media_mount(self) -> None:
        assert is_cross_filesystem(Path("/media/usb")) is True

    def test_net_mount(self) -> None:
        assert is_cross_filesystem(Path("/net/server/share")) is True

    def test_run_user(self) -> None:
        assert is_cross_filesystem(Path("/run/user/1000/something")) is True


class TestSummarizeChangesByType:
    def test_single_type(self) -> None:
        paths = [Path("a.py"), Path("b.py"), Path("c.py")]
        summary = summarize_changes_by_type(paths)
        assert "3" in summary
        assert "Python" in summary or "py" in summary.lower()

    def test_multiple_types(self) -> None:
        paths = [Path("a.py"), Path("b.js"), Path("c.ts")]
        summary = summarize_changes_by_type(paths)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_empty_list(self) -> None:
        assert summarize_changes_by_type([]) == ""

    def test_overflow_shows_others(self) -> None:
        paths = [
            Path("a.py"), Path("b.js"), Path("c.ts"), Path("d.rs"),
            Path("e.go"), Path("f.rb"),
        ]
        summary = summarize_changes_by_type(paths)
        assert "other" in summary.lower()
