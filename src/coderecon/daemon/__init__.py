"""CodeRecon daemon - global multi-repo HTTP server with file watching and background indexing."""

from coderecon.daemon.concurrency import FreshnessGate, MutationRouter
from coderecon.daemon.global_app import GlobalDaemon, RepoSlot, WorktreeSlot
from coderecon.daemon.indexer import BackgroundIndexer
from coderecon.daemon.watcher import FileWatcher

__all__ = [
    "BackgroundIndexer",
    "FreshnessGate",
    "GlobalDaemon",
    "MutationRouter",
    "RepoSlot",
    "FileWatcher",
    "WorktreeSlot",
]
