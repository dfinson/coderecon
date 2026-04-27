"""PhaseBox — dynamic boxed phase with Live + Panel for real-time updating."""

from __future__ import annotations

from types import TracebackType

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskID, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

class PhaseBox:
    """Dynamic boxed phase with Live + Panel for real-time updating content.

    Usage::

        with phase_box("Discovery") as phase:
            task_id = phase.add_progress("Scanning", total=100)
            for i in range(100):
                phase.advance(task_id)
            phase.complete("4 languages detected")
    """

    def __init__(
        self,
        title: str,
        *,
        width: int = 60,
        console: Console | None = None,
        suppress_flag: object | None = None,
    ) -> None:
        self._title = title
        self._width = width
        self._console = console or Console()
        self._items: list[RenderableType] = []
        self._progress: Progress | None = None
        self._live: Live | None = None
        self._suppress_flag = suppress_flag
        self._live_table: Table | None = None

    def _render(self) -> Panel:
        """Render the current phase state as a Panel."""
        items_to_render: list[RenderableType] = []
        for item in self._items:
            items_to_render.append(item)
        if self._progress and self._progress.tasks:
            items_to_render.append(self._progress)
        if self._live_table is not None:
            items_to_render.append(self._live_table)
        content = Group(*items_to_render) if items_to_render else Text("")
        return Panel(
            content,
            title=self._title,
            title_align="left",
            border_style="dim",
            width=self._width,
            padding=(0, 1),
        )

    def _update(self) -> None:
        """Update the live display."""
        if self._live:
            self._live.update(self._render())
            self._live.refresh()

    def add_progress(self, description: str, total: int) -> TaskID:
        """Add a progress bar and return its task ID."""
        if self._progress is None:
            self._progress = Progress(
                TextColumn("{task.description}"),
                BarColumn(bar_width=35, style="cyan", complete_style="cyan"),
                TaskProgressColumn(),
                console=self._console,
                expand=False,
            )
        task_id = self._progress.add_task(description, total=total)
        self._update()
        return task_id

    def advance(self, task_id: TaskID, advance: int = 1) -> None:
        """Advance a progress bar."""
        if self._progress:
            self._progress.advance(task_id, advance)
            self._update()

    def complete(self, summary: str, *, style: str = "green") -> None:
        """Mark a step complete with a status symbol and summary."""
        if self._progress:
            for task in list(self._progress.tasks):
                self._progress.remove_task(task.id)
        if style == "yellow":
            symbol = "⚠ "
        elif style == "red":
            symbol = "✗ "
        else:
            symbol = "✓ "
        self._items.append(Text(f"{symbol}{summary}", style=style))
        self._update()

    def add_text(self, text: str, *, style: str = "") -> None:
        """Add a plain text line."""
        if style:
            self._items.append(Text(text, style=style))
        else:
            self._items.append(Text(text))
        self._update()

    def add_table(self, table: Table) -> None:
        """Add a Rich Table to the phase content."""
        self._items.append(table)
        self._update()

    def set_live_table(self, table: Table | None) -> None:
        """Set or update the live table below the progress bar."""
        self._live_table = table
        self._update()

    def __enter__(self) -> PhaseBox:
        if self._suppress_flag is not None:
            self._suppress_flag.active = True  # type: ignore[attr-defined]
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=12,
            transient=False,
        )
        self._live.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._live:
            self._live.update(self._render())
            self._live.refresh()
            self._live.__exit__(exc_type, exc_val, exc_tb)
        if self._suppress_flag is not None:
            self._suppress_flag.active = False  # type: ignore[attr-defined]
