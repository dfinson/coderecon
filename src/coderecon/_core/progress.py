"""User-facing progress feedback for CLI operations.

Design principles:
- Show something if operation takes >0.5s
- Progress bar if iterating >100 items
- Single line updates, no spam
- Graceful degradation in non-TTY (CI, pipes)
- Suppress structlog during spinners to avoid line collision (Issue #5)

Usage::

    from coderecon._core.progress import progress, status, spinner
    # Simple status message
    status("Discovering files...")
    # Progress bar for iteration
    for file in progress(files, desc="Indexing"):
        process(file)
    # Success/error markers
    status("Ready", style="success")  # ✓ Ready
    status("Failed to connect", style="error")  # ✗ Failed to connect
    # Spinner with log suppression
    with spinner("Reindexing 3 files"):
        do_work()  # structlog suppressed during this block
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import TYPE_CHECKING

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, Progress, TaskID, TaskProgressColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

# Threshold for showing progress bar
_PROGRESS_THRESHOLD = 100

# Console for output
_console = Console(stderr=True)

# Style prefixes for direct tool/sync operations
_STYLES = {
    "success": "[green]✓[/green] ",
    "error": "[red]✗[/red] ",
    "warning": "[yellow]![/yellow] ",
    "info": "  ",
    "none": "",
}

# Async/background sources use no symbol to avoid noise
_ASYNC_SUCCESS_SYMBOL = ""

# Source tags for console output - escaped for Rich markup
_SOURCE_TAGS = {
    "agent": "\\[agent] ",
    "indexer": "\\[indexer] ",
    "watch": "\\[watch] ",
    "system": "",  # No tag for system messages
}

# Global flag to suppress console logging during spinners (Issue #5)
_suppress_console_logs = threading.local()

def _timestamp() -> str:
    """Return current time as HH:MM:SS for log prefix."""
    return time.strftime("%H:%M:%S")

def is_console_suppressed() -> bool:
    """Check if console logging is currently suppressed."""
    return getattr(_suppress_console_logs, "active", False)

@contextmanager
def suppress_console_logs() -> Iterator[None]:
    """Context manager to suppress structlog console output.
    Used during spinners to prevent log lines from colliding with
    Rich's live display. Logs are still written to file handlers.
    """
    _suppress_console_logs.active = True
    try:
        yield
    finally:
        _suppress_console_logs.active = False

class ConsoleSuppressingFilter(logging.Filter):
    """Filter that blocks console output when suppression is active.
    Allows file handlers to continue receiving logs while console
    output is paused during Rich live displays.
    """
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: ARG002
        # Only filter if suppression is active AND this is a console handler
        # File handlers should still receive logs
        return not is_console_suppressed()
def _get_logger() -> BoundLogger:
    """Get logger lazily to respect runtime config."""
    from coderecon._core.logging import get_logger
    return get_logger("progress")

def _is_tty() -> bool:
    """Check if stderr is a TTY."""
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

def get_console() -> Console:
    """Get the shared Rich console instance."""
    return _console

def status(
    message: str,
    *,
    style: str = "info",
    source: str | None = None,
    indent: int = 0,
) -> None:
    """Print a styled status message to stderr.
    Args:
        message: The message to display
        style: Style preset (success, error, warning, info, none)
        source: Source tag (tool, index, watch, system). If provided and style=success,
                async sources (index, watch) use no symbol to avoid false confirmation.
        indent: Number of spaces to indent
    """
    # Determine the prefix symbol
    if style == "success" and source in ("indexer", "watch"):
        # Async/background sources use no symbol to avoid false confirmation
        prefix = _ASYNC_SUCCESS_SYMBOL
    else:
        prefix = _STYLES.get(style, "")
    # Add source tag if provided
    source_tag = _SOURCE_TAGS.get(source, "") if source else ""
    # Build timestamp prefix (dimmed, bracketed)
    ts = f"[dim]\\[{_timestamp()}][/dim] "
    padding = " " * indent
    _console.print(f"{ts}{source_tag}{padding}{prefix}{message}", highlight=False)
    # Log at DEBUG for observability (lazy to respect runtime config)
    _get_logger().debug("status", message=message, style=style, source=source)

def progress[T](
    iterable: Iterable[T],
    *,
    desc: str | None = None,
    total: int | None = None,
    unit: str = "files",
    force: bool = False,
) -> Iterator[T]:
    """Wrap an iterable with a progress bar if TTY and >100 items (or force=True)."""
    # Try to get total
    if total is None:
        try:
            total = len(iterable)  # type: ignore[arg-type]
        except TypeError:
            total = None
    # Decide whether to show progress
    show_bar = _is_tty() and total is not None and (force or total > _PROGRESS_THRESHOLD)
    if show_bar:
        with (
            suppress_console_logs(),
            Progress(
                TextColumn("    {task.description}:"),
                BarColumn(bar_width=25, style="cyan", complete_style="cyan"),
                TaskProgressColumn(),
                TextColumn("{task.completed}/{task.total} {task.fields[unit]}"),
                console=_console,
                transient=True,
            ) as pbar,
        ):
            task_id = pbar.add_task(desc or "Processing", total=total, unit=unit)
            for item in iterable:
                yield item
                pbar.advance(task_id)
    else:
        # No progress bar, just yield
        log = _get_logger()
        if desc and total:
            log.debug("progress_start", desc=desc, total=total)
        for item in iterable:
            yield item
        if desc and total:
            log.debug("progress_done", desc=desc, total=total)

@contextmanager
def spinner(message: str, *, indent: int = 0) -> Iterator[None]:
    """Context manager for a spinner with log suppression.
    Suppresses structlog console output during the spinner to prevent
    log lines from colliding with Rich's live display (Issue #5).
    Usage::
        with spinner("Reindexing 3 files"):
            do_work()
    """
    padding = " " * indent
    if _is_tty():
        with (
            suppress_console_logs(),
            _console.status(f"{padding}[cyan]{message}[/cyan]", spinner="dots"),
        ):
            yield
    else:
        # Non-TTY: just print the message
        _console.print(f"{padding}{message}...")
        yield

@contextmanager
def task(name: str) -> Iterator[None]:
    """Context manager for a named task with timing.
    Usage::
        with task("Building index"):
            # ... do work ...
        # Prints: ✓ Building index (3.2s)
    """
    import time
    log = _get_logger()
    log.debug("task_start", task=name)
    status(f"{name}...", style="none", indent=0)
    start = time.perf_counter()
    try:
        yield
        elapsed = time.perf_counter() - start
        status(f"{name} ({elapsed:.1f}s)", style="success")
        log.debug("task_done", task=name, elapsed_s=elapsed)
    except Exception as e:
        elapsed = time.perf_counter() - start
        status(f"{name} failed: {e}", style="error")
        log.error("task_failed", task=name, elapsed_s=elapsed, error=str(e))
        raise

def animate_text(text: str, delay: float = 0.02) -> None:
    """Print text line-by-line with a small delay for dramatic effect.
    Args:
        text: Multi-line text to animate
        delay: Seconds between each line (default 0.02)
    """
    import time
    for line in text.splitlines():
        _console.print(line, highlight=False)
        if delay > 0 and _is_tty():
            time.sleep(delay)

from coderecon._core.phase_box import PhaseBox  # noqa: E402


def phase_box(title: str, *, width: int = 60, console: Console | None = None) -> PhaseBox:
    """Create a dynamic boxed phase with Live + Panel."""
    return PhaseBox(
        title,
        width=width,
        console=console or _console,
        suppress_flag=_suppress_console_logs,
    )

class SummaryStream:
    """Sequential progress bar → checkmark summary transitions.
    For vertical flows like recon up startup where each step shows
    a progress bar, then becomes a completion summary.
    Usage::
        with summary_stream() as stream:
            async for _ in stream.step("Loading index", items, total):
                pass
            # Prints: ✓ 147 files in index
            stream.done("147 files in index")
            async for _ in stream.step("Checking changes", items, total):
                pass
            stream.done("3 new, 1 modified")
    """
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or _console
        self._current_progress: Progress | None = None
        self._current_task_id: TaskID | None = None
        self._current_live: Live | None = None
    @contextmanager
    def step(self, description: str, total: int, *, bar_width: int = 40) -> Iterator[SummaryStream]:
        """Start a step with a progress bar.
        Yields self so you can call advance(). When context exits,
        the progress bar is removed (call done() to add summary).
        """
        self._current_progress = Progress(
            TextColumn("  {task.description}"),
            BarColumn(bar_width=bar_width, style="cyan", complete_style="cyan"),
            TaskProgressColumn(),
            console=self._console,
            expand=False,
        )
        self._current_task_id = self._current_progress.add_task(description, total=total)
        # Use Live for real-time updates
        _suppress_console_logs.active = True
        self._current_live = Live(
            self._current_progress,
            console=self._console,
            refresh_per_second=12,
            transient=True,
        )
        try:
            with self._current_live:
                yield self
        finally:
            _suppress_console_logs.active = False
            self._current_progress = None
            self._current_task_id = None
            self._current_live = None
    def advance(self, amount: int = 1) -> None:
        """Advance the current progress bar."""
        if self._current_progress and self._current_task_id is not None:
            self._current_progress.advance(self._current_task_id, amount)
    def done(self, summary: str, *, style: str = "success") -> None:
        """Print a completion summary for the step."""
        prefix = _STYLES.get(style, _STYLES["success"])
        self._console.print(f"  {prefix}{summary}", highlight=False)
    def info(self, message: str) -> None:
        """Print an info message (no checkmark)."""
        self._console.print(f"  {message}", highlight=False)
    def __enter__(self) -> SummaryStream:
        return self
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """No-op: SummaryStream requires no teardown."""
        return None
def summary_stream(console: Console | None = None) -> SummaryStream:
    """Create a summary stream for sequential progress → summary transitions.
    Usage::
        with summary_stream() as stream:
            with stream.step("Loading index", total=147) as s:
                for item in items:
                    process(item)
                    s.advance()
            stream.done("147 files in index")
    """
    return SummaryStream(console=console)

def print_centered(text: str, *, style: str | None = None, console: Console | None = None) -> None:
    """Print centered text (for logos, banners)."""
    c = console or _console
    t = Text(text, style=style) if style else Text(text)
    c.print(Align.center(t), highlight=False)

def print_rule(
    *, style: str = "dim", width: int | None = None, console: Console | None = None
) -> None:
    """Print a horizontal rule separator.
    Args:
        style: Rich style for the rule
        width: Fixed width (None = full terminal width)
        console: Console to print to
    """
    c = console or _console
    if width:
        # Centered fixed-width rule
        rule_text = "─" * width
        c.print(Align.center(Text(rule_text, style=style)), highlight=False)
    else:
        c.print(Rule(style=style))

def make_extension_table(extensions: dict[str, int], *, max_bar_width: int = 20) -> Table:
    """Create a Rich Table for file extension breakdown.
    Args:
        extensions: Dict mapping extension (e.g. ".py") to file count
        max_bar_width: Maximum width of the bar column
    Returns:
        Rich Table ready to add to a phase_box or print directly
    """
    import math
    table = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False)
    table.add_column("ext", style="cyan", width=6)
    table.add_column("count", justify="right", width=4)
    table.add_column("bar", width=max_bar_width)
    sorted_exts = sorted(extensions.items(), key=lambda x: -x[1])
    if not sorted_exts:
        return table
    max_count = sorted_exts[0][1]
    max_sqrt = math.sqrt(max_count)
    for ext, count in sorted_exts:
        # Logarithmic scale for bar width
        bar_len = int(max_bar_width * math.sqrt(count) / max_sqrt) if max_sqrt > 0 else 0
        bar = "█" * bar_len
        table.add_row(ext, str(count), Text(bar, style="blue"))
    return table
