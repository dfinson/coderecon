"""Discovery operations for the index coordinator.

Standalone functions extracted from IndexCoordinatorEngine. Each takes
``engine`` as its first parameter.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete, text
from sqlmodel import col, select

from coderecon._core.languages import is_test_file
from coderecon.index.models import (
    Context,
    IndexedCoverageCapability,
    IndexedLintTool,
    ProbeStatus,
    TestTarget,
)

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


async def _resolve_context_runtimes(engine: IndexCoordinatorEngine) -> int:
    """Resolve and persist runtimes for all valid contexts.
    Called during initialization after contexts are persisted.
    Uses RuntimeResolver to detect Python venvs, Node installations, etc.
    Results are persisted to ContextRuntime table.
    Returns:
        Count of runtimes resolved
    """
    logger = structlog.get_logger(__name__)
    runtimes_resolved = 0
    from coderecon.testing.runtime import ContextRuntime, RuntimeResolver
    # Create resolver once
    resolver = RuntimeResolver(engine.repo_root)
    with engine.db.session() as session:
        # Get all valid contexts
        stmt = select(Context).where(
            Context.probe_status == ProbeStatus.VALID.value,
        )
        contexts = list(session.exec(stmt).all())
        for context in contexts:
            if context.id is None:
                continue
            # Check if runtime already exists (idempotent init)
            existing = session.exec(
                select(ContextRuntime).where(ContextRuntime.context_id == context.id)
            ).first()
            if existing is not None:
                runtimes_resolved += 1
                continue
            # Resolve runtime for this context
            try:
                result = resolver.resolve_for_context(
                    context_id=context.id,
                    language_family=context.language_family,
                    root_path=context.root_path or "",
                )
                # Persist the runtime
                session.add(result.runtime)
                runtimes_resolved += 1
                # Log any warnings
                for warning in result.warnings:
                    logger.warning(
                        "runtime_resolution_warning",
                        context_id=context.id,
                        context_name=context.name,
                        warning=warning,
                    )
                logger.debug(
                    "context_runtime_resolved",
                    context_id=context.id,
                    context_name=context.name,
                    language=context.language_family,
                    method=result.method,
                    python_exe=result.runtime.python_executable,
                )
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning(
                    "runtime_resolution_failed",
                    context_id=context.id,
                    error=str(e),
                    exc_info=True,
                )
        session.commit()
    return runtimes_resolved


async def _discover_test_targets(engine: IndexCoordinatorEngine) -> int:
    """Discover and persist test targets for all workspaces.
    Uses runner packs to find test files. Called during init() after
    contexts are persisted. Returns count of targets discovered.
    """
    targets_discovered = 0
    discovered_at = time.time()
    logger = structlog.get_logger(__name__)
    from coderecon.testing.runner_pack import runner_registry
    with engine.db.session() as session:
        existing_ids = set(session.exec(select(TestTarget.target_id)).all())
        # Get all valid contexts
        stmt = select(Context).where(
            Context.probe_status == ProbeStatus.VALID.value,
        )
        contexts = list(session.exec(stmt).all())
        # Group by workspace root to avoid duplicate discovery
        roots_to_contexts: dict[Path, list[Context]] = {}
        for ctx in contexts:
            ws_root = engine.repo_root / ctx.root_path if ctx.root_path else engine.repo_root
            roots_to_contexts.setdefault(ws_root, []).append(ctx)
        # Detect and discover for each workspace
        for ws_root, ws_contexts in roots_to_contexts.items():
            # Find applicable runner packs
            detected_packs = runner_registry.detect_all(ws_root)
            if not detected_packs:
                continue
            # Use primary context for this workspace
            primary_ctx = ws_contexts[0]
            for pack_class, _confidence in detected_packs:
                pack = pack_class()
                try:
                    targets = await pack.discover(ws_root)
                except (OSError, RuntimeError, ValueError):
                    logger.debug("test_pack_discover_failed", exc_info=True)
                    continue
                for target in targets:
                    # Skip if already exists (idempotent init)
                    if target.target_id in existing_ids:
                        targets_discovered += 1
                        continue
                    test_target = TestTarget(
                        context_id=primary_ctx.id,
                        target_id=target.target_id,
                        selector=target.selector,
                        kind=target.kind,
                        language=target.language,
                        runner_pack_id=target.runner_pack_id,
                        workspace_root=target.workspace_root,
                        estimated_cost=target.estimated_cost,
                        test_count=target.test_count,
                        path=target.path,
                        discovered_at=discovered_at,
                    )
                    session.add(test_target)
                    existing_ids.add(target.target_id)
                    targets_discovered += 1
        session.commit()
    return targets_discovered


async def _discover_lint_tools(engine: IndexCoordinatorEngine) -> int:
    """Discover and persist lint tools for all workspaces.
    Uses lint tool registry to find configured tools. Called during init()
    after contexts are persisted. Returns count of tools discovered.
    """
    tools_discovered = 0
    discovered_at = time.time()
    from coderecon.lint.tools import registry as lint_registry
    with engine.db.session() as session:
        # Get existing tool_ids for idempotent init
        existing_ids = set(session.exec(select(IndexedLintTool.tool_id)).all())
        # Detect configured tools for the repo (returns (tool, config_file) tuples)
        detected_pairs = lint_registry.detect(engine.repo_root)
        for tool, config_file in detected_pairs:
            # Skip if already exists (idempotent init)
            if tool.tool_id in existing_ids:
                tools_discovered += 1
                continue
            indexed_tool = IndexedLintTool(
                tool_id=tool.tool_id,
                name=tool.name,
                category=tool.category.value,
                languages=json.dumps(sorted(tool.languages)),
                executable=tool.executable,
                workspace_root=str(engine.repo_root),
                config_file=config_file,
                discovered_at=discovered_at,
            )
            session.add(indexed_tool)
            existing_ids.add(tool.tool_id)
            tools_discovered += 1
        session.commit()
    return tools_discovered


async def _discover_coverage_capabilities(engine: IndexCoordinatorEngine) -> int:
    """Discover and persist coverage capabilities for all workspaces.
    For each (workspace, runner_pack) pair detected during test target discovery,
    detect available coverage tools and store them. Called during init() after
    test targets are discovered.
    Returns count of capabilities discovered.
    """
    capabilities_discovered = 0
    discovered_at = time.time()
    with engine.db.session() as session:
        # Get existing (workspace_root, runner_pack_id) pairs for idempotent init
        existing_pairs = set(
            session.exec(
                select(
                    IndexedCoverageCapability.workspace_root,
                    IndexedCoverageCapability.runner_pack_id,
                )
            ).all()
        )
        # Get distinct (workspace_root, runner_pack_id) pairs from test targets
        stmt = select(
            TestTarget.workspace_root,
            TestTarget.runner_pack_id,
        ).distinct()
        pairs = list(session.exec(stmt).all())
        for workspace_root, runner_pack_id in pairs:
            # Skip if already exists (idempotent init)
            if (workspace_root, runner_pack_id) in existing_pairs:
                capabilities_discovered += 1
                continue
            # Lazy import: coderecon.testing.ops transitively imports
            # coderecon.index.__init__ which imports coderecon.index.ops,
            # creating a circular import if placed at module level.
            from coderecon.testing.ops import detect_coverage_tools
            # Detect coverage tools for this pair
            tools = detect_coverage_tools(
                Path(workspace_root),
                runner_pack_id,
                exec_ctx=None,  # Use index runtime if needed later
            )
            capability = IndexedCoverageCapability(
                workspace_root=workspace_root,
                runner_pack_id=runner_pack_id,
                tools_json=json.dumps(tools),
                discovered_at=discovered_at,
            )
            session.add(capability)
            existing_pairs.add((workspace_root, runner_pack_id))
            capabilities_discovered += 1
        session.commit()
    return capabilities_discovered


async def _rediscover_test_targets(engine: IndexCoordinatorEngine) -> int:
    """Clear and re-discover all test targets."""
    # Clear existing test targets
    with engine.db.session() as session:
        session.exec(select(TestTarget)).all()  # Load for delete
        session.execute(delete(TestTarget))
        session.commit()
    # Re-run discovery
    return await _discover_test_targets(engine)


async def _rediscover_lint_tools(engine: IndexCoordinatorEngine) -> int:
    """Clear and re-discover all lint tools."""
    # Clear existing lint tools
    with engine.db.session() as session:
        session.execute(delete(IndexedLintTool))
        session.commit()
    # Re-run discovery
    return await _discover_lint_tools(engine)


async def _update_test_targets_incremental(
    engine: IndexCoordinatorEngine,
    new_paths: list[Path],
    existing_paths: list[Path],
    removed_paths: list[Path],
) -> int:
    """Incrementally update test targets for changed files.
    Only processes files matching test patterns (test_*.py, *_test.py, etc.).
    Does NOT walk the entire filesystem.
    """
    # Filter to only test files
    new_test_files = [p for p in new_paths if is_test_file(p)]
    modified_test_files = [p for p in existing_paths if is_test_file(p)]
    removed_test_files = [p for p in removed_paths if is_test_file(p)]
    if not new_test_files and not modified_test_files and not removed_test_files:
        return 0
    targets_changed = 0
    discovered_at = time.time()
    from coderecon.testing.runner_pack import runner_registry
    with engine.db.session() as session:
        # Remove targets for deleted test files
        if removed_test_files:
            for path in removed_test_files:
                rel_path = str(path)
                # Delete targets where path matches
                session.execute(delete(TestTarget).where(col(TestTarget.path) == rel_path))
                # Also try selector match (some targets use selector=path)
                session.execute(delete(TestTarget).where(col(TestTarget.selector) == rel_path))
                # Delete coverage facts whose test_id starts with this file path
                session.execute(
                    text(
                        "DELETE FROM test_coverage_facts "
                        "WHERE test_id LIKE :prefix"
                    ),
                    {"prefix": f"{rel_path}::%"},
                )
                targets_changed += 1
        # For new/modified test files, detect runner and create target
        files_to_process = new_test_files + modified_test_files
        if files_to_process:
            # Get primary context
            ctx_stmt = select(Context).where(
                Context.probe_status == ProbeStatus.VALID.value,
            )
            contexts = list(session.exec(ctx_stmt).all())
            if not contexts:
                session.commit()
                return targets_changed
            primary_ctx = contexts[0]
            # Detect applicable runner packs once
            detected_packs = runner_registry.detect_all(engine.repo_root)
            for path in files_to_process:
                rel_path = str(path)
                full_path = engine.repo_root / path
                if not full_path.exists():
                    continue
                # Delete existing target for this path (if modified)
                if path in modified_test_files:
                    session.execute(delete(TestTarget).where(col(TestTarget.path) == rel_path))
                    session.execute(
                        delete(TestTarget).where(col(TestTarget.selector) == rel_path)
                    )
                # Find matching runner pack
                for pack_class, _confidence in detected_packs:
                    pack = pack_class()
                    # Check if this pack handles this file type
                    if (
                        pack.language == "python"
                        and path.suffix == ".py"
                        or pack.language == "javascript"
                        and path.suffix
                        in (
                            ".js",
                            ".ts",
                            ".jsx",
                            ".tsx",
                        )
                        or pack.language == "go"
                        and path.suffix == ".go"
                    ):
                        target = TestTarget(
                            context_id=primary_ctx.id,
                            target_id=f"test:{rel_path}",
                            selector=rel_path,
                            kind="file",
                            language=pack.language,
                            runner_pack_id=pack.pack_id,
                            workspace_root=str(engine.repo_root),
                            path=rel_path,
                            discovered_at=discovered_at,
                        )
                        session.add(target)
                        targets_changed += 1
                        break
        session.commit()
    return targets_changed


async def _update_lint_tools_incremental(
    engine: IndexCoordinatorEngine, changed_paths: list[Path]
) -> int:
    """Incrementally update lint tools if config files changed."""
    # Get all known config files from registered tools
    from coderecon.lint.tools import registry as lint_registry
    config_filenames: set[str] = set()
    for tool in lint_registry.all():
        for config_spec in tool.config_files:
            # Handle section-aware specs like "pyproject.toml:tool.ruff"
            filename = config_spec.split(":")[0] if ":" in config_spec else config_spec
            config_filenames.add(filename)
    # Check if any changed path is a config file
    changed_configs = [p for p in changed_paths if p.name in config_filenames]
    if not changed_configs:
        return 0
    # Config file changed - re-detect all tools (config may affect multiple)
    tools_updated = 0
    discovered_at = time.time()
    with engine.db.session() as session:
        # Clear existing tools
        session.execute(delete(IndexedLintTool))
        # Re-detect
        detected_pairs = lint_registry.detect(engine.repo_root)
        for tool, config_file in detected_pairs:
            indexed_tool = IndexedLintTool(
                tool_id=tool.tool_id,
                name=tool.name,
                category=tool.category.value,
                languages=json.dumps(sorted(tool.languages)),
                executable=tool.executable,
                workspace_root=str(engine.repo_root),
                config_file=config_file,
                discovered_at=discovered_at,
            )
            session.add(indexed_tool)
            tools_updated += 1
        session.commit()
    return tools_updated
