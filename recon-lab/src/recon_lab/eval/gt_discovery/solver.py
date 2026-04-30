"""Multi-turn agent solver for GT discovery.

Uses a basic_agent loop with CodeRecon tools to let an LLM explore
the codebase and find all relevant definitions for a given task.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any

from inspect_ai.solver import Solver, TaskState, solver

from recon_lab.eval.gt_discovery.tracer import TraceCollector


SYSTEM_PROMPT = """\
You are an expert code explorer. Your goal is to FIND all code definitions \
(functions, classes, methods, constants) that a developer would need to \
understand or modify to complete the described task.

You will NOT write or modify code. You will ONLY explore the repository \
using the provided tools and report what you find.

Strategy:
1. Start with recon_search using the query and seeds.
2. Follow interesting leads with read_file to confirm relevance.
3. Use recon_impact to find callers/callees of critical symbols.
4. Use grep_search for string-level patterns the search might miss.
5. Think about: What interfaces does new code implement? What does it \
   register into? What sibling patterns exist? What parent classes \
   or protocols are involved?

Be thorough but focused. You have limited turns — prioritize depth \
over breadth. Report your findings by listing the most relevant \
definitions you discovered.
"""


class _ContextManager:
    """Manages AppContext lifecycle for GT discovery."""

    def __init__(self, clone_dir: str) -> None:
        self._instances_dir = Path(clone_dir).expanduser()
        self._clones_dir = self._instances_dir.parent
        self._cached_repo: str | None = None
        self._ctx: Any = None
        self._repo_root: Path | None = None

    def _workspace_id(self, instance_id: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in instance_id)

    async def ensure_context(self, instance_id: str) -> tuple[Any, Path]:
        """Load or reuse AppContext for an instance. Returns (ctx, repo_root)."""
        if self._cached_repo == instance_id and self._ctx is not None:
            return self._ctx, self._repo_root  # type: ignore[return-value]

        if self._ctx is not None:
            self._ctx.coordinator.close()
            self._ctx = None
            self._cached_repo = None
            gc.collect()

        from coderecon.mcp.context import AppContext
        from recon_lab.data_manifest import main_clone_dir_for_dir

        wid = self._workspace_id(instance_id)
        instance_dir = self._instances_dir / wid

        data_root = self._clones_dir.parent / "data"
        data_dir = data_root / instance_id
        main_dir = main_clone_dir_for_dir(data_dir, self._clones_dir)
        if main_dir is None:
            msg = f"Cannot resolve main clone for {instance_id!r}"
            raise FileNotFoundError(msg)

        cp = main_dir / ".recon"
        if not cp.exists():
            msg = f"No coderecon index at {cp} (instance {instance_id!r})"
            raise FileNotFoundError(msg)

        repo_root = instance_dir if instance_dir.exists() else main_dir

        logging.disable(logging.WARNING)

        self._ctx = AppContext.standalone(
            repo_root=repo_root,
            db_path=cp / "index.db",
            tantivy_path=cp / "tantivy",
        )
        await self._ctx.coordinator.load_existing()
        self._cached_repo = instance_id
        self._repo_root = repo_root

        return self._ctx, repo_root

    def make_def_resolver(self) -> Any:
        """Return a callable that resolves (path, start, end) → defs from the index."""

        def resolve(path: str, start_line: int, end_line: int) -> list[dict]:
            if self._ctx is None:
                return []
            try:
                coord = self._ctx.coordinator
                defs = coord.defs_at_span(path, start_line, end_line)
                return [
                    {
                        "def_uid": d.uid,
                        "path": d.path,
                        "name": d.name,
                        "kind": d.kind,
                        "start_line": d.start_line,
                        "end_line": d.end_line,
                    }
                    for d in defs
                ]
            except Exception:
                return []

        return resolve


@solver
def gt_discovery_solver(
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    max_turns: int = 15,
) -> Solver:
    """Inspect AI solver that runs a multi-turn agent loop for GT discovery.

    The agent uses CodeRecon tools to explore the codebase and find
    relevant definitions. All accesses are traced.
    """
    from inspect_ai.solver import generate, use_tools

    from recon_lab.eval.gt_discovery.tools import all_tools, bind_tools_context

    ctx_mgr = _ContextManager(clone_dir)

    async def solve(state: TaskState, gen: Any) -> TaskState:
        meta = state.metadata
        instance_id = meta.get("task_id") or meta.get("instance_id") or meta["repo_id"]

        # Set up context
        ctx, repo_root = await ctx_mgr.ensure_context(instance_id)
        resolver = ctx_mgr.make_def_resolver()
        tracer = TraceCollector(_resolve_defs=resolver)

        # Bind tool context
        bind_tools_context(
            ctx=ctx,
            repo=meta.get("repo_id", ""),
            worktree_root=repo_root,
            tracer=tracer,
        )

        # Inject system prompt
        from inspect_ai.model import ChatMessageSystem

        state.messages.insert(0, ChatMessageSystem(content=SYSTEM_PROMPT))

        # Run agent loop
        tools = all_tools()
        state = await use_tools(tools)(state, gen)

        for _turn in range(max_turns):
            tracer.advance_turn()
            state = await generate()(state, gen)
            # Check if the model issued a tool call
            last_msg = state.messages[-1] if state.messages else None
            if last_msg is None:
                break
            if not getattr(last_msg, "tool_calls", None):
                break
            state = await use_tools(tools)(state, gen)

        # Store trace in state for scorer
        state.store.set("access_trace", tracer.to_dict())
        state.store.set("touched_def_uids", sorted(tracer.touched_def_uids()))
        state.store.set("touched_paths", sorted(tracer.touched_paths()))
        state.store.set("total_turns", tracer._turn)

        return state

    return solve
