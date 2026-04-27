"""Git hook management — install/uninstall hooks for auto-reindex.

Installs a post-checkout and post-merge hook that triggers background
reindexing when the daemon is running. The hook sends a lightweight
HTTP request to the running daemon's /reindex endpoint.
"""

from __future__ import annotations

import stat
from pathlib import Path

import structlog

from coderecon.files.ops import atomic_write_text

log = structlog.get_logger(__name__)

_HOOK_MARKER = "# coderecon-managed-hook"

_HOOK_TEMPLATE = """\
#!/bin/sh
{marker}
# Auto-reindex after git operations (checkout, merge, rebase, pull).
# Installed by: coderecon init --hooks
# Safe to remove: coderecon init --no-hooks

RECON_DIR="$(git rev-parse --show-toplevel)/.recon"
PORT_FILE="$RECON_DIR/daemon.port"

if [ -f "$PORT_FILE" ]; then
    PORT=$(cat "$PORT_FILE")
    # Fire-and-forget: notify daemon to reindex
    curl -s -o /dev/null -X POST "http://127.0.0.1:$PORT/reindex" 2>/dev/null || true
fi
"""

_HOOK_NAMES = ("post-checkout", "post-merge", "post-rewrite")

def install_hooks(repo_root: Path) -> list[str]:
    """Install git hooks for auto-reindex.

    Installs post-checkout, post-merge, and post-rewrite hooks.
    If a hook already exists and is not coderecon-managed, it is
    left untouched and skipped.

    Returns:
        List of hook names that were installed.
    """
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.exists():
        log.warning("git_hooks_dir_missing", path=str(hooks_dir))
        return []

    installed = []
    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        if hook_path.exists():
            content = hook_path.read_text()
            if _HOOK_MARKER not in content:
                log.info(
                    "hook_exists_skipping",
                    hook=hook_name,
                    reason="not coderecon-managed",
                )
                continue

        hook_content = _HOOK_TEMPLATE.format(marker=_HOOK_MARKER)
        atomic_write_text(hook_path, hook_content)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)
        installed.append(hook_name)
        log.debug("hook_installed", hook=hook_name)

    return installed

def uninstall_hooks(repo_root: Path) -> list[str]:
    """Remove coderecon-managed hooks.

    Only removes hooks that contain the coderecon marker. Custom
    hooks are left untouched.

    Returns:
        List of hook names that were removed.
    """
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.exists():
        return []

    removed = []
    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        if not hook_path.exists():
            continue

        content = hook_path.read_text()
        if _HOOK_MARKER in content:
            hook_path.unlink()
            removed.append(hook_name)
            log.debug("hook_removed", hook=hook_name)

    return removed

def hooks_installed(repo_root: Path) -> list[str]:
    """Check which coderecon hooks are currently installed."""
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.exists():
        return []

    found = []
    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        if hook_path.exists() and _HOOK_MARKER in hook_path.read_text():
            found.append(hook_name)

    return found
