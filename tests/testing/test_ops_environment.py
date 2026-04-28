"""Tests for testing ops_environment pure functions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderecon.testing.ops_environment import (
    clear_coverage_tools_cache,
    detect_coverage_tools,
    detect_node_package_manager,
    detect_python_venv,
    get_python_executable,
    _default_parallelism,
)


class TestDetectPythonVenv:
    def test_finds_dot_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv = root / ".venv"
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = /usr/bin")
            result = detect_python_venv(root)
            assert result == venv

    def test_finds_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv = root / "venv"
            venv.mkdir()
            (venv / "bin").mkdir()
            (venv / "bin" / "activate").write_text("#!/bin/bash")
            result = detect_python_venv(root)
            assert result == venv

    def test_no_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = detect_python_venv(root)
            assert result is None

    def test_ignores_non_venv_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / ".venv"
            fake.mkdir()
            # No pyvenv.cfg or activate script
            result = detect_python_venv(root)
            assert result is None


class TestGetPythonExecutable:
    def test_with_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv = root / ".venv"
            (venv / "bin").mkdir(parents=True)
            python = venv / "bin" / "python"
            python.write_text("#!/usr/bin/env python3")
            python.chmod(0o755)
            (venv / "pyvenv.cfg").write_text("home = /usr/bin")
            result = get_python_executable(root)
            assert result == str(python)

    def test_without_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = get_python_executable(root)
            assert result == "python"


class TestClearCoverageToolsCache:
    def test_clears(self) -> None:
        # Just verify it doesn't error
        clear_coverage_tools_cache()


class TestDetectCoverageTools:
    """Tests for detect_coverage_tools across different runner packs."""

    def setup_method(self) -> None:
        clear_coverage_tools_cache()

    def test_python_pytest_cov_available(self, tmp_path: Path) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = detect_coverage_tools(tmp_path, "python.pytest")
        assert result == {"pytest-cov": True}
        mock_run.assert_called_once()

    def test_python_pytest_cov_not_available(self, tmp_path: Path) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            result = detect_coverage_tools(tmp_path, "python.pytest")
        assert result == {"pytest-cov": False}

    def test_python_pytest_file_not_found(self, tmp_path: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = detect_coverage_tools(tmp_path, "python.pytest")
        assert result == {"pytest-cov": False}

    def test_python_pytest_timeout(self, tmp_path: Path) -> None:
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            result = detect_coverage_tools(tmp_path, "python.pytest")
        assert result == {"pytest-cov": False}

    def test_js_jest_builtin(self, tmp_path: Path) -> None:
        result = detect_coverage_tools(tmp_path, "js.jest")
        assert result == {"built-in": True}

    def test_js_vitest_builtin(self, tmp_path: Path) -> None:
        clear_coverage_tools_cache()
        result = detect_coverage_tools(tmp_path, "js.vitest")
        assert result == {"built-in": True}

    def test_go_builtin(self, tmp_path: Path) -> None:
        result = detect_coverage_tools(tmp_path, "go.gotest")
        assert result == {"built-in": True}

    def test_rust_nextest_with_cargo_llvm_cov(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value="/usr/bin/cargo-llvm-cov"):
            result = detect_coverage_tools(tmp_path, "rust.nextest")
        assert result == {"cargo-llvm-cov": True}

    def test_rust_nextest_without_cargo_llvm_cov(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=None):
            result = detect_coverage_tools(tmp_path, "rust.nextest")
        assert result == {"cargo-llvm-cov": False}

    def test_caching(self, tmp_path: Path) -> None:
        """Second call returns cached result without spawning subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            detect_coverage_tools(tmp_path, "python.pytest")
            detect_coverage_tools(tmp_path, "python.pytest")
        # subprocess.run called only once due to caching
        mock_run.assert_called_once()

    def test_uses_exec_ctx_python_executable(self, tmp_path: Path) -> None:
        exec_ctx = MagicMock()
        exec_ctx.runtime.python_executable = "/custom/python"
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            detect_coverage_tools(tmp_path, "python.pytest", exec_ctx=exec_ctx)
        args = mock_run.call_args[0][0]
        assert args[0] == "/custom/python"


class TestDetectNodePackageManager:
    """Tests for detect_node_package_manager using tmp_path."""

    def test_pnpm(self, tmp_path: Path) -> None:
        (tmp_path / "pnpm-lock.yaml").write_text("")
        assert detect_node_package_manager(tmp_path) == "pnpm"

    def test_yarn(self, tmp_path: Path) -> None:
        (tmp_path / "yarn.lock").write_text("")
        assert detect_node_package_manager(tmp_path) == "yarn"

    def test_bun(self, tmp_path: Path) -> None:
        (tmp_path / "bun.lockb").write_bytes(b"")
        assert detect_node_package_manager(tmp_path) == "bun"

    def test_npm_default(self, tmp_path: Path) -> None:
        assert detect_node_package_manager(tmp_path) == "npm"

    def test_pnpm_takes_priority(self, tmp_path: Path) -> None:
        """pnpm-lock.yaml checked first, so it wins if multiple exist."""
        (tmp_path / "pnpm-lock.yaml").write_text("")
        (tmp_path / "yarn.lock").write_text("")
        assert detect_node_package_manager(tmp_path) == "pnpm"


class TestDefaultParallelism:
    """Tests for _default_parallelism with mocked os.cpu_count."""

    def test_normal_cpu_count(self) -> None:
        with patch("os.cpu_count", return_value=4):
            assert _default_parallelism() == 8  # 4 * 2 = 8

    def test_high_cpu_count_capped(self) -> None:
        with patch("os.cpu_count", return_value=32):
            # 32 * 2 = 64, but capped at 16
            assert _default_parallelism() == 16

    def test_none_cpu_count_fallback(self) -> None:
        with patch("os.cpu_count", return_value=None):
            # Fallback to 4, then 4 * 2 = 8
            assert _default_parallelism() == 8

    def test_single_cpu(self) -> None:
        with patch("os.cpu_count", return_value=1):
            assert _default_parallelism() == 2  # 1 * 2 = 2
