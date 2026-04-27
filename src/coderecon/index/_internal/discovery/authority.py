"""Tier 1 authority filter for context discovery.

This module implements Phase A.2 of SPEC.md §8.4: Tier 1 Authority Filter.
It filters Tier 2 candidates based on Tier 1 workspace configuration.

For example:
- pnpm-workspace.yaml lists which packages are part of the workspace
- go.work lists which modules are included
- settings.gradle lists which subprojects are included

Unlisted Tier 2 candidates are marked as "detached" (no owner).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from coderecon.index._internal.discovery.authority_parsers import (
    get_cargo_workspace_members,
    get_go_work_modules,
    get_gradle_includes,
    get_js_workspace_globs,
    get_maven_modules,
    get_sln_projects,
    is_inside,
    matches_any_glob,
)
from coderecon.index._internal.discovery.membership import relative_to
from coderecon.index.models import CandidateContext, LanguageFamily, ProbeStatus

log = structlog.get_logger(__name__)

@dataclass
class AuthorityResult:
    """Result of authority filtering."""
    pending: list[CandidateContext]
    detached: list[CandidateContext]
class Tier1AuthorityFilter:
    """
    Filters Tier 2 candidates based on Tier 1 workspace configuration.

    Implements Phase A.2 of SPEC.md §8.4.3.

    For families with strict workspace management (javascript, go, rust, jvm),
    Tier 2 candidates must be listed in the Tier 1 configuration to be valid.
    Unlisted candidates are marked as "detached".

    Usage::

        filter = Tier1AuthorityFilter(repo_root)
        result = filter.apply(candidates)

        # result.pending - candidates that passed
        # result.detached - candidates that were excluded
    """
    def __init__(self, repo_root: Path) -> None:
        """Initialize authority filter."""
        self.repo_root = repo_root
    def apply(self, candidates: list[CandidateContext]) -> AuthorityResult:
        """
        Apply Tier 1 authority filter to candidates.

        Args:
            candidates: List of candidate contexts from discovery

        Returns:
            AuthorityResult with pending and detached candidates.
        """
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        # Group by family
        by_family: dict[LanguageFamily, list[CandidateContext]] = {}
        for c in candidates:
            if c.language_family not in by_family:
                by_family[c.language_family] = []
            by_family[c.language_family].append(c)

        # Apply family-specific filtering
        for family, family_candidates in by_family.items():
            if family == LanguageFamily.JAVASCRIPT:
                p, d = self._filter_javascript(family_candidates)
            elif family == LanguageFamily.GO:
                p, d = self._filter_go(family_candidates)
            elif family == LanguageFamily.RUST:
                p, d = self._filter_rust(family_candidates)
            elif family in (
                LanguageFamily.JAVA,
                LanguageFamily.KOTLIN,
                LanguageFamily.SCALA,
                LanguageFamily.GROOVY,
            ):
                # JVM languages share Gradle/Maven workspace systems
                p, d = self._filter_jvm(family_candidates)
            elif family in (
                LanguageFamily.CSHARP,
                LanguageFamily.FSHARP,
                LanguageFamily.VBNET,
            ):
                # .NET languages share solution file workspace system
                p, d = self._filter_dotnet(family_candidates)
            else:
                # These families have no Tier 1 workspace mechanism -
                # they only have Tier 2 project markers (e.g., pyproject.toml,
                # Gemfile, composer.json). All candidates pass through.
                p, d = family_candidates, []

            pending.extend(p)
            detached.extend(d)

        return AuthorityResult(pending=pending, detached=detached)
    def _filter_javascript(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter JavaScript candidates based on workspace config."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        # Find Tier 1 candidates (workspace roots)
        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            # No workspace config - all candidates are pending
            return candidates, []

        # Get workspace globs from each Tier 1
        workspace_globs: dict[str, list[str]] = {}
        for t1 in tier1:
            globs = get_js_workspace_globs(self.repo_root, t1)
            if globs:
                workspace_globs[t1.root_path] = globs

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            # Check if candidate is under a Tier 1 and matches its globs
            matched = False
            for t1_root, globs in workspace_globs.items():
                if is_inside(candidate.root_path, t1_root):
                    rel_path = relative_to(candidate.root_path, t1_root)
                    if matches_any_glob(rel_path, globs):
                        matched = True
                        break

            if matched or not workspace_globs:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached
    def _filter_go(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter Go candidates based on go.work."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get modules from go.work
        workspace_modules: dict[str, list[str]] = {}
        for t1 in tier1:
            modules = get_go_work_modules(self.repo_root, t1)
            if modules:
                workspace_modules[t1.root_path] = modules

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            for t1_root, modules in workspace_modules.items():
                if is_inside(candidate.root_path, t1_root):
                    rel_path = relative_to(candidate.root_path, t1_root)
                    if rel_path in modules or f"./{rel_path}" in modules:
                        matched = True
                        break

            if matched or not workspace_modules:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached
    def _filter_rust(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter Rust candidates based on Cargo workspace."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get members from Cargo.toml [workspace]
        workspace_members: dict[str, list[str]] = {}
        for t1 in tier1:
            members = get_cargo_workspace_members(self.repo_root, t1)
            if members:
                workspace_members[t1.root_path] = members

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            for t1_root, members in workspace_members.items():
                if is_inside(candidate.root_path, t1_root):
                    rel_path = relative_to(candidate.root_path, t1_root)
                    if matches_any_glob(rel_path, members):
                        matched = True
                        break

            if matched or not workspace_members:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached
    def _filter_jvm(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter JVM candidates based on settings.gradle or Maven pom.xml."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get includes from settings.gradle or Maven pom.xml
        workspace_includes: dict[str, tuple[list[str], bool]] = {}
        for t1 in tier1:
            # Try Gradle first
            includes, is_strict = get_gradle_includes(self.repo_root, t1)
            if not includes:
                # Fall back to Maven
                includes = get_maven_modules(self.repo_root, t1)
                is_strict = bool(includes)  # Maven modules are strict
            workspace_includes[t1.root_path] = (includes, is_strict)

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            strict_mode = False
            for t1_root, (includes, is_strict) in workspace_includes.items():
                if is_inside(candidate.root_path, t1_root):
                    strict_mode = is_strict
                    rel_path = relative_to(candidate.root_path, t1_root)
                    # Gradle uses : as path separator
                    gradle_path = rel_path.replace("/", ":")
                    if gradle_path in includes or rel_path in includes:
                        matched = True
                        break

            # In permissive mode, all are pending
            if matched or not strict_mode:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached
    def _filter_dotnet(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter .NET candidates based on .sln solution files."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get project paths from each .sln file
        solution_projects: dict[str, list[str]] = {}
        for t1 in tier1:
            projects = get_sln_projects(self.repo_root, t1)
            if projects:
                solution_projects[t1.root_path] = projects

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            for t1_root, projects in solution_projects.items():
                if is_inside(candidate.root_path, t1_root):
                    rel_path = relative_to(candidate.root_path, t1_root)
                    # Check if candidate path matches any project directory
                    for proj_path in projects:
                        # Project paths in .sln are relative to solution dir
                        # and point to .csproj files - we want the directory
                        proj_dir = str(Path(proj_path).parent).replace("\\", "/")
                        if rel_path == proj_dir or proj_path.replace("\\", "/").startswith(
                            rel_path + "/"
                        ):
                            matched = True
                            break
                    if matched:
                        break

            if matched or not solution_projects:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached
