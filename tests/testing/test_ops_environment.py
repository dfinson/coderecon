"""Tests for testing ops_environment pure functions."""

from __future__ import annotations

import tempfile
from pathlib import Path

from coderecon.testing.ops_environment import (
    clear_coverage_tools_cache,
    detect_python_venv,
    get_python_executable,
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
