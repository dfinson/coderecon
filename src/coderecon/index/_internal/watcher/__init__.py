"""File watcher infrastructure for continuous background indexing."""

from coderecon.index._internal.ignore import IgnoreChecker
from coderecon.index._internal.watcher.watcher import (
    BackgroundIndexer,
    FileChangeEvent,
    FileChangeKind,
    FileWatcher,
    WatcherConfig,
    WatcherQueue,
)

__all__ = [
    "BackgroundIndexer",
    "FileChangeEvent",
    "FileChangeKind",
    "FileWatcher",
    "IgnoreChecker",
    "WatcherConfig",
    "WatcherQueue",
]
