"""Shared repo/worktree resolution for HTTP and stdio transports.

Extracts the lazy-activation lookup from ``_DynamicMcpRouter.handle()``
so both the HTTP ASGI router and the stdio dispatch layer can reuse it.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coderecon.daemon.global_app import GlobalDaemon, WorktreeSlot


async def resolve_worktree(
    daemon: GlobalDaemon,
    repo_name: str,
    worktree: str | None = None,
) -> WorktreeSlot | None:
    """Resolve repo + worktree with lazy activation.

    Shared by both HTTP (``_DynamicMcpRouter``) and stdio (``dispatch``).

    If *worktree* is ``None``, resolves to the repo's default worktree
    (the ``is_main=True`` entry from the catalog — typically labeled
    ``"main"`` regardless of the git branch name).

    Returns ``None`` if the repo or worktree cannot be found/activated.
    """
    slot = daemon.get_slot(repo_name)
    if slot is None:
        slot = await daemon.lazy_activate_repo(repo_name)
    if slot is None:
        return None

    # None → repo's default worktree (is_main=True from catalog)
    if worktree is None:
        # The main worktree is always activated first during lazy_activate_repo,
        # and its slot key is whatever _detect_worktree_name() returned — "main".
        worktree = "main"

    wt_slot = slot.worktrees.get(worktree)
    if wt_slot is None:
        wt_slot = await daemon.lazy_activate_worktree(repo_name, worktree)
    if wt_slot is None:
        return None

    wt_slot.last_request_at = time.time()
    return wt_slot
