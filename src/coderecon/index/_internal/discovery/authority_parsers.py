"""Config-file parsers for Tier 1 authority filtering.

Parse workspace configuration files (pnpm-workspace.yaml, go.work,
Cargo.toml, settings.gradle, pom.xml, .sln) to determine which
Tier 2 candidates belong to a Tier 1 workspace.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml

if TYPE_CHECKING:
    from coderecon.index.models import CandidateContext

from coderecon.index._internal.discovery.membership import is_inside  # noqa: F401  # re-export

log = structlog.get_logger(__name__)

def matches_any_glob(path: str, globs: list[str]) -> bool:
    """Check if path matches any of the globs."""
    for glob in globs:
        if glob.endswith("/**"):
            glob = glob[:-3]
        if glob.startswith("./"):
            glob = glob[2:]
        if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob + "/*"):
            return True
        if path == glob:
            return True
    return False

def _extract_package_json_workspaces(data: dict) -> list[str]:
    """Extract workspace globs from a parsed package.json."""
    ws = data.get("workspaces")
    if isinstance(ws, list):
        return list(ws)
    if isinstance(ws, dict) and "packages" in ws:
        return list(ws["packages"])
    return []


def get_js_workspace_globs(repo_root: Path, t1: CandidateContext) -> list[str]:
    """Extract workspace globs from JavaScript workspace config."""
    globs: list[str] = []
    for marker in t1.markers:
        marker_path = repo_root / marker
        try:
            if marker.endswith("pnpm-workspace.yaml"):
                content = yaml.safe_load(marker_path.read_text())
                if content and "packages" in content:
                    globs.extend(content["packages"])
            elif marker.endswith("package.json"):
                content = json.loads(marker_path.read_text())
                globs.extend(_extract_package_json_workspaces(content))
            elif marker.endswith("lerna.json"):
                content = json.loads(marker_path.read_text())
                if "packages" in content:
                    globs.extend(content["packages"])
        except OSError as e:
            log.debug("workspace_config_read_error", marker=marker, error=str(e))
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            log.warning("workspace_config_parse_error", marker=marker, error=str(e))
    return globs

def get_go_work_modules(repo_root: Path, t1: CandidateContext) -> list[str]:
    """Extract modules from go.work."""
    modules: list[str] = []
    for marker in t1.markers:
        if marker.endswith("go.work"):
            marker_path = repo_root / marker
            try:
                content = marker_path.read_text()
                in_use_block = False
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("use ("):
                        in_use_block = True
                        continue
                    if in_use_block:
                        if line == ")":
                            break
                        if line and not line.startswith("//"):
                            modules.append(line)
                    elif line.startswith("use "):
                        modules.append(line[4:].strip())
            except OSError:
                log.debug("go_work_read_failed", marker=str(marker_path))
    return modules

def get_cargo_workspace_members(repo_root: Path, t1: CandidateContext) -> list[str]:
    """Extract members from Cargo.toml [workspace]."""
    import tomllib

    members: list[str] = []
    for marker in t1.markers:
        if marker.endswith("Cargo.toml"):
            marker_path = repo_root / marker
            try:
                with marker_path.open("rb") as f:
                    data = tomllib.load(f)
                ws = data.get("workspace", {})
                members.extend(ws.get("members", []))
            except OSError:
                log.debug("cargo_toml_read_failed", path=str(marker_path), exc_info=True)
            except tomllib.TOMLDecodeError:
                log.warning("cargo_toml_parse_failed", path=str(marker_path), exc_info=True)
    return members
    return members

def get_gradle_includes(repo_root: Path, t1: CandidateContext) -> tuple[list[str], bool]:
    """Extract includes from settings.gradle."""
    includes: list[str] = []
    is_strict = True
    for marker in t1.markers:
        if marker.endswith(("settings.gradle", "settings.gradle.kts")):
            marker_path = repo_root / marker
            try:
                content = marker_path.read_text()
                if "${" in content or "$" in content:
                    is_strict = False
                for match in re.finditer(r"include\s*\(\s*['\"]([^'\"]+)['\"]", content):
                    includes.append(match.group(1))
                for match in re.finditer(r"include\s+['\"]([^'\"]+)['\"]", content):
                    includes.append(match.group(1))
            except OSError:
                log.debug("gradle_settings_read_failed", path=str(marker_path), exc_info=True)
    return includes, is_strict

def get_maven_modules(repo_root: Path, t1: CandidateContext) -> list[str]:
    """Extract modules from Maven pom.xml."""
    modules: list[str] = []
    for marker in t1.markers:
        if marker.endswith("pom.xml"):
            marker_path = repo_root / marker
            try:
                content = marker_path.read_text()
                modules_match = re.search(r"<modules>\s*(.*?)\s*</modules>", content, re.DOTALL)
                if modules_match:
                    modules_block = modules_match.group(1)
                    for module_match in re.finditer(
                        r"<module>\s*([^<]+?)\s*</module>", modules_block
                    ):
                        modules.append(module_match.group(1))
            except OSError:
                log.debug("pom_xml_read_failed", path=str(marker_path), exc_info=True)
    return modules

def get_sln_projects(repo_root: Path, t1: CandidateContext) -> list[str]:
    """Extract project paths from .sln solution file."""
    projects: list[str] = []
    for marker in t1.markers:
        if marker.endswith(".sln"):
            marker_path = repo_root / marker
            try:
                content = marker_path.read_text()
                for match in re.finditer(
                    r'Project\("[^"]+"\)\s*=\s*"[^"]+",\s*"([^"]+)"', content
                ):
                    proj_path = match.group(1)
                    if proj_path.endswith((".csproj", ".fsproj", ".vbproj")):
                        projects.append(proj_path)
            except OSError:
                log.debug("sln_read_failed", path=str(marker_path), exc_info=True)
    return projects
