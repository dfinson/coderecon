"""Session management for CodeRecon MCP server.

Handles session lifecycle, state tracking, and task binding.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from coderecon.config.models import TimeoutsConfig

# Tools that acquire an exclusive session lock while running.
# No other tool may execute concurrently on the same session.
EXCLUSIVE_TOOLS: frozenset[str] = frozenset({"checkpoint", "semantic_diff"})


@dataclass
class MutationContext:
    """Tracks in-flight semantic refactors (rename/move previews).

    Cleared by checkpoint after successful commit.
    """

    context_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)

    # Refactor_* tracking: refactor_id → type ("rename"/"move"/"impact")
    pending_refactors: dict[str, str] = field(default_factory=dict)

    @property
    def has_pending_refactors(self) -> bool:
        return bool(self.pending_refactors)

    @property
    def is_empty(self) -> bool:
        """True when no pending refactors — safe to discard."""
        return not self.pending_refactors

    def clear(self) -> None:
        """Reset all mutation state.  Called by checkpoint."""
        self.pending_refactors.clear()


@dataclass
class SessionState:
    """State for a single session."""

    session_id: str
    created_at: float
    last_active: float
    task_id: str | None = None
    fingerprints: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    # Maps recon_id → {candidate_id: repo_relative_path}.
    # Populated by recon pipeline, consumed by semantic refactors.
    candidate_maps: dict[str, dict[str, str]] = field(default_factory=dict)
    # Tracks in-flight semantic refactors (rename/move previews).
    mutation_ctx: MutationContext = field(default_factory=MutationContext)

    # Read-only intent: True = research-only session (mutations blocked),
    # False = read-write session, None = not yet declared.
    # Set by recon(read_only=...), reset on new recon call.
    read_only: bool | None = None
    # Last recon_id — set by recon; used by semantic refactors.
    last_recon_id: str | None = None

    # Exclusive-tool lock: prevents concurrent tool execution during
    # long-running operations like checkpoint, semantic_diff.
    _exclusive_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _exclusive_holder: str | None = field(default=None, repr=False)

    def touch(self) -> None:
        """Update last active timestamp."""
        self.last_active = time.time()

    @asynccontextmanager
    async def exclusive(self, tool_name: str) -> AsyncIterator[None]:
        """Acquire the exclusive lock for a long-running tool.

        While held, any other tool call on this session will block until
        the exclusive tool completes.
        """
        async with self._exclusive_lock:
            self._exclusive_holder = tool_name
            try:
                yield
            finally:
                self._exclusive_holder = None

    @property
    def exclusive_holder(self) -> str | None:
        """Name of the tool currently holding the exclusive lock, or None."""
        return self._exclusive_holder


class SessionManager:
    """Manages active sessions."""

    def __init__(self, config: TimeoutsConfig | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._config = config or TimeoutsConfig()

    def get_or_create(self, session_id: str | None = None) -> SessionState:
        """Get existing session or create new one.

        Args:
            session_id: Optional session ID. If None, creates new session.

        Returns:
            SessionState object
        """
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            return session

        # Create new session
        new_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        session = SessionState(
            session_id=new_id,
            created_at=time.time(),
            last_active=time.time(),
        )
        self._sessions[new_id] = session
        return session

    def get(self, session_id: str) -> SessionState | None:
        """Get session if exists."""
        return self._sessions.get(session_id)

    def close(self, session_id: str) -> None:
        """Close a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def cleanup_stale(self) -> int:
        """Remove stale sessions.

        Returns:
            Number of sessions removed
        """
        now = time.time()
        to_remove = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active > self._config.session_idle_sec
        ]
        for sid in to_remove:
            del self._sessions[sid]
        return len(to_remove)
