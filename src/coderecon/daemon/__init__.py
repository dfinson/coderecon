"""CodeRecon daemon - HTTP server with file watching and background indexing."""

from coderecon.daemon.app import create_app
from coderecon.daemon.indexer import BackgroundIndexer
from coderecon.daemon.lifecycle import ServerController
from coderecon.daemon.watcher import FileWatcher

__all__ = [
    "BackgroundIndexer",
    "ServerController",
    "FileWatcher",
    "create_app",
]
