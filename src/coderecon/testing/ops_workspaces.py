"""Workspace detection — monorepo support, helper utilities."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import structlog

from coderecon.index._internal.ignore import PRUNABLE_DIRS
from coderecon.testing.models import ParsedTestSuite
from coderecon.testing.runner_pack import RunnerPack, runner_registry

log = structlog.get_logger(__name__)


@dataclass
class DetectedWorkspace:
    """A detected workspace with its runner pack."""
    root: Path
    pack: RunnerPack
    confidence: float

def _is_prunable_path(rel_path: Path) -> bool:
    """Check if relative path contains any prunable directory components.
    Note: 'packages' is in PRUNABLE_DIRS for .NET, but is also a common JS
    monorepo pattern. We only consider a path prunable if it has nested
    prunable dirs or is clearly not a project directory.
    """
    parts = rel_path.parts
    for part in parts:
        # Skip 'packages' at root level since it's commonly used in JS monorepos
        if part == "packages" and parts.index(part) == 0:
            continue
        if part in PRUNABLE_DIRS:
            return True
    return False

def detect_workspaces(repo_root: Path) -> list[DetectedWorkspace]:
    """Detect all workspaces and their runners in a repo.
    Supports monorepos by finding nested workspace roots.
    Respects PRUNABLE_DIRS to avoid scanning .venv, node_modules, etc.
    """
    workspaces: list[DetectedWorkspace] = []
    # First check repo root
    for pack_class, confidence in runner_registry.detect_all(repo_root):
        workspaces.append(
            DetectedWorkspace(
                root=repo_root,
                pack=pack_class(),
                confidence=confidence,
            )
        )
    # Collect workspace directories from various monorepo tools
    workspace_dirs: set[Path] = set()
    # Check for yarn/npm workspaces in package.json
    root_pkg = repo_root / "package.json"
    if root_pkg.exists():
        try:
            data = json.loads(root_pkg.read_text())
            workspaces_field = data.get("workspaces", [])
            # Handle both array and object format
            if isinstance(workspaces_field, dict):
                patterns = workspaces_field.get("packages", [])
            else:
                patterns = workspaces_field
            for pattern in patterns:
                # Expand glob patterns
                for ws_path in repo_root.glob(pattern):
                    if (
                        ws_path.is_dir()
                        and not _is_prunable_path(ws_path.relative_to(repo_root))
                        and (ws_path / "package.json").exists()
                    ):
                        workspace_dirs.add(ws_path)
        except (OSError, json.JSONDecodeError, KeyError):
            log.debug("npm_workspace_parse_failed", exc_info=True)
    # Check for pnpm workspaces
    pnpm_ws = repo_root / "pnpm-workspace.yaml"
    if pnpm_ws.exists():
        try:
            import yaml
            data = yaml.safe_load(pnpm_ws.read_text()) or {}
            for pattern in data.get("packages", []):
                for ws_path in repo_root.glob(pattern):
                    if (
                        ws_path.is_dir()
                        and not _is_prunable_path(ws_path.relative_to(repo_root))
                        and (ws_path / "package.json").exists()
                    ):
                        workspace_dirs.add(ws_path)
        except (OSError, yaml.YAMLError, KeyError):
            log.debug("pnpm_workspace_parse_failed", exc_info=True)
    # Check for Nx workspaces
    nx_json = repo_root / "nx.json"
    if nx_json.exists():
        # Nx projects can be in apps/, libs/, packages/
        for subdir in ["apps", "libs", "packages", "projects"]:
            for project_dir in (repo_root / subdir).glob("*"):
                if (
                    project_dir.is_dir()
                    and not _is_prunable_path(project_dir.relative_to(repo_root))
                    and (
                        (project_dir / "package.json").exists()
                        or (project_dir / "project.json").exists()
                    )
                ):
                    workspace_dirs.add(project_dir)
    # Check for Turborepo
    turbo_json = repo_root / "turbo.json"
    if turbo_json.exists():
        # Turbo uses package.json workspaces, already handled above
        # But also check common patterns
        for subdir in ["apps", "packages"]:
            for project_dir in (repo_root / subdir).glob("*"):
                if (
                    project_dir.is_dir()
                    and not _is_prunable_path(project_dir.relative_to(repo_root))
                    and (project_dir / "package.json").exists()
                ):
                    workspace_dirs.add(project_dir)
    # Check for Lerna
    lerna_json = repo_root / "lerna.json"
    if lerna_json.exists():
        try:
            data = json.loads(lerna_json.read_text())
            for pattern in data.get("packages", ["packages/*"]):
                for ws_path in repo_root.glob(pattern):
                    if (
                        ws_path.is_dir()
                        and not _is_prunable_path(ws_path.relative_to(repo_root))
                        and (ws_path / "package.json").exists()
                    ):
                        workspace_dirs.add(ws_path)
        except (OSError, json.JSONDecodeError, KeyError):
            log.debug("lerna_workspace_parse_failed", exc_info=True)
    # Check for Rush
    rush_json = repo_root / "rush.json"
    if rush_json.exists():
        try:
            data = json.loads(rush_json.read_text())
            for project in data.get("projects", []):
                project_folder = project.get("projectFolder")
                if project_folder:
                    ws_path = repo_root / project_folder
                    if ws_path.is_dir():
                        workspace_dirs.add(ws_path)
        except (OSError, json.JSONDecodeError, KeyError):
            log.debug("rush_workspace_parse_failed", exc_info=True)
    # Legacy: Check for packages/* pattern (fallback)
    for pkg_json in repo_root.glob("packages/*/package.json"):
        if not _is_prunable_path(pkg_json.parent.relative_to(repo_root)):
            workspace_dirs.add(pkg_json.parent)
    # Detect runners in each workspace
    # Note: workspace_dirs comes from intentional workspace detection (package.json workspaces,
    # monorepo configs, etc.) so we don't re-filter them. The prunable path check was already
    # applied during collection where appropriate.
    for ws_root in workspace_dirs:
        for pack_class, confidence in runner_registry.detect_all(ws_root):
            workspaces.append(
                DetectedWorkspace(
                    root=ws_root,
                    pack=pack_class(),
                    confidence=confidence,
                )
            )
    # Deduplicate by (root, pack_id), keeping highest confidence
    seen: dict[tuple[Path, str], DetectedWorkspace] = {}
    for ws in workspaces:
        key = (ws.root, ws.pack.pack_id)
        if key not in seen or ws.confidence > seen[key].confidence:
            seen[key] = ws
    return list(seen.values())

def _os_script_path(unix_path: str) -> str:
    """Convert Unix script path to OS-appropriate form.
    On Windows, converts ./script to script (relies on .bat/.cmd lookup).
    On Unix, returns the path unchanged.
    """
    if sys.platform == "win32" and unix_path.startswith("./"):
        base = unix_path[2:]
        # Simple wrapper script (no subdirs): ./gradlew -> gradlew
        if "/" not in base:
            return base
        # Subdir path: ./vendor/bin/phpunit -> vendor\bin\phpunit
        return base.replace("/", "\\")
    return unix_path

def _classify_result_error(
    result: ParsedTestSuite,
    output_path: Path,
    stdout: str,
    exit_code: int | None,
) -> None:
    """Classify error type on a parsed test result based on output and exit code."""
    if result.errors > 0 and result.total == 0:
        if not output_path.exists() and not stdout.strip():
            result.error_type = "output_missing"
            result.error_detail = "No output file or stdout from test runner"
        elif result.error_type == "none":
            result.error_type = "parse_failed"
            result.error_detail = "Could not parse test output"
    elif exit_code and exit_code != 0 and result.failed == 0 and result.errors == 0:
        result.error_type = "command_failed"
        result.error_detail = f"Command exited with code {exit_code}"
        result.errors = 1
