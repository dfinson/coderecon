"""Tests for testing/runtime_resolvers.py — language-specific resolver functions."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from coderecon.testing.runtime_resolvers import (
    _detect_package_manager,
    _find_python_in_venv,
    _get_go_version,
    _get_node_version,
    _run_version_check,
)


class TestFindPythonInVenv:
    """Tests for _find_python_in_venv."""

    def test_returns_none_for_nonexistent_dir(self, tmp_path: Path) -> None:
        result = _find_python_in_venv(tmp_path / "no-such-dir")
        assert result is None

    def test_returns_none_for_dir_without_venv_markers(self, tmp_path: Path) -> None:
        """A plain directory with no pyvenv.cfg or activate script is not a venv."""
        result = _find_python_in_venv(tmp_path)
        assert result is None

    def test_finds_unix_python_with_pyvenv_cfg(self, tmp_path: Path) -> None:
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        python = bin_dir / "python"
        python.touch()
        result = _find_python_in_venv(tmp_path)
        assert result == python

    def test_finds_unix_python_with_activate_script(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "activate").touch()
        python = bin_dir / "python"
        python.touch()
        result = _find_python_in_venv(tmp_path)
        assert result == python

    def test_returns_none_when_marker_exists_but_no_python(self, tmp_path: Path) -> None:
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # No python binary created
        result = _find_python_in_venv(tmp_path)
        assert result is None


class TestRunVersionCheck:
    """Tests for _run_version_check."""

    def test_returns_parsed_value(self) -> None:
        with patch("coderecon.testing.runtime_resolvers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["python", "--version"],
                returncode=0,
                stdout="Python 3.12.0\n",
                stderr="",
            )
            result = _run_version_check(
                ["python", "--version"],
                lambda r: r.stdout.strip().split()[-1] if r.returncode == 0 else None,
            )
            assert result == "3.12.0"

    def test_returns_none_on_file_not_found(self) -> None:
        with patch(
            "coderecon.testing.runtime_resolvers.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _run_version_check(["nosuchbin", "--version"], lambda r: r.stdout)
            assert result is None

    def test_returns_none_on_timeout(self) -> None:
        with patch(
            "coderecon.testing.runtime_resolvers.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5),
        ):
            result = _run_version_check(["x", "--version"], lambda r: r.stdout)
            assert result is None


class TestGetNodeVersion:
    """Tests for _get_node_version."""

    def test_parses_node_version(self) -> None:
        with patch("coderecon.testing.runtime_resolvers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["node", "--version"],
                returncode=0,
                stdout="v20.11.0\n",
                stderr="",
            )
            result = _get_node_version("/usr/bin/node")
            assert result == "20.11.0"

    def test_returns_none_on_failure(self) -> None:
        with patch("coderecon.testing.runtime_resolvers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["node", "--version"],
                returncode=1,
                stdout="",
                stderr="error",
            )
            result = _get_node_version("/usr/bin/node")
            assert result is None


class TestGetGoVersion:
    """Tests for _get_go_version."""

    def test_parses_go_version(self) -> None:
        with patch("coderecon.testing.runtime_resolvers.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["go", "version"],
                returncode=0,
                stdout="go version go1.22.1 linux/amd64\n",
                stderr="",
            )
            result = _get_go_version("/usr/bin/go")
            assert result == "1.22.1"


class TestDetectPackageManager:
    """Tests for _detect_package_manager."""

    def _make_resolver(self, repo_root: Path) -> MagicMock:
        resolver = MagicMock()
        resolver.repo_root = repo_root
        return resolver

    def test_detects_npm_from_package_lock(self, tmp_path: Path) -> None:
        (tmp_path / "package-lock.json").touch()
        resolver = self._make_resolver(tmp_path)
        with patch("coderecon.testing.runtime_resolvers.shutil.which", return_value="/usr/bin/npm"):
            pm, exe = _detect_package_manager(resolver, tmp_path)
        assert pm == "npm"
        assert exe == "/usr/bin/npm"

    def test_detects_yarn_from_lockfile(self, tmp_path: Path) -> None:
        (tmp_path / "yarn.lock").touch()
        resolver = self._make_resolver(tmp_path)
        with patch("coderecon.testing.runtime_resolvers.shutil.which", return_value="/usr/bin/yarn"):
            pm, _ = _detect_package_manager(resolver, tmp_path)
        assert pm == "yarn"

    def test_detects_pnpm_from_lockfile(self, tmp_path: Path) -> None:
        (tmp_path / "pnpm-lock.yaml").touch()
        resolver = self._make_resolver(tmp_path)
        with patch("coderecon.testing.runtime_resolvers.shutil.which", return_value="/usr/bin/pnpm"):
            pm, _ = _detect_package_manager(resolver, tmp_path)
        assert pm == "pnpm"

    def test_defaults_to_npm_when_no_lockfile(self, tmp_path: Path) -> None:
        resolver = self._make_resolver(tmp_path)
        with patch("coderecon.testing.runtime_resolvers.shutil.which", return_value="/usr/bin/npm"):
            pm, exe = _detect_package_manager(resolver, tmp_path)
        assert pm == "npm"

    def test_lockfile_in_repo_root(self, tmp_path: Path) -> None:
        context = tmp_path / "packages" / "frontend"
        context.mkdir(parents=True)
        (tmp_path / "yarn.lock").touch()
        resolver = self._make_resolver(tmp_path)
        with patch("coderecon.testing.runtime_resolvers.shutil.which", return_value="/usr/bin/yarn"):
            pm, _ = _detect_package_manager(resolver, context)
        assert pm == "yarn"
