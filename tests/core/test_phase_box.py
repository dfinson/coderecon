"""Smoke tests for PhaseBox — Rich UI phase panel."""

from io import StringIO

from rich.console import Console

from coderecon.core.phase_box import PhaseBox


def _make_box(title: str = "Test") -> PhaseBox:
    console = Console(file=StringIO(), force_terminal=True, width=80)
    return PhaseBox(title, console=console)


class TestPhaseBox:
    def test_context_manager(self) -> None:
        box = _make_box()
        with box as pb:
            assert pb is box
            assert pb._live is not None

    def test_add_progress_and_advance(self) -> None:
        box = _make_box()
        with box as pb:
            tid = pb.add_progress("Scanning", total=10)
            assert pb._progress is not None
            assert len(pb._progress.tasks) == 1
            pb.advance(tid, 5)

    def test_complete(self) -> None:
        box = _make_box()
        with box as pb:
            pb.complete("Done", style="green")
            assert len(pb._items) == 1
            assert "Done" in str(pb._items[0])

    def test_complete_warning(self) -> None:
        box = _make_box()
        with box as pb:
            pb.complete("Warning", style="yellow")
            assert "⚠" in str(pb._items[0])

    def test_complete_error(self) -> None:
        box = _make_box()
        with box as pb:
            pb.complete("Failed", style="red")
            assert "✗" in str(pb._items[0])

    def test_add_text(self) -> None:
        box = _make_box()
        with box as pb:
            pb.add_text("hello")
            assert len(pb._items) == 1
            pb.add_text("styled", style="bold")
            assert len(pb._items) == 2

    def test_set_live_table(self) -> None:
        from rich.table import Table
        box = _make_box()
        with box as pb:
            t = Table()
            t.add_column("col")
            t.add_row("row")
            pb.set_live_table(t)
            assert pb._live_table is t
            pb.set_live_table(None)
            assert pb._live_table is None
