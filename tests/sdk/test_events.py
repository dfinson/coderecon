"""Tests for sdk/events.py — EventRouter dispatch and subscription."""

from __future__ import annotations

from unittest.mock import MagicMock

from coderecon.sdk.events import EventRouter
from coderecon.sdk.types import Event


def _event(type_: str = "index.complete", data: dict | None = None) -> Event:
    return Event(type=type_, data=data or {})


class TestEventRouter:
    def test_callback_fires_on_match(self) -> None:
        router = EventRouter()
        cb = MagicMock()
        router.on("index.*", cb)
        router.dispatch(_event("index.complete"))
        cb.assert_called_once()

    def test_callback_not_fired_on_mismatch(self) -> None:
        router = EventRouter()
        cb = MagicMock()
        router.on("test.*", cb)
        router.dispatch(_event("index.complete"))
        cb.assert_not_called()

    def test_wildcard_matches_all(self) -> None:
        router = EventRouter()
        cb = MagicMock()
        router.on("*", cb)
        router.dispatch(_event("index.complete"))
        router.dispatch(_event("test.failed"))
        assert cb.call_count == 2

    def test_callback_error_is_swallowed(self) -> None:
        router = EventRouter()

        def _bad_cb(e: Event) -> None:
            raise RuntimeError("boom")

        good_cb = MagicMock()
        router.on("*", _bad_cb)
        router.on("*", good_cb)
        # Should not raise
        router.dispatch(_event("index.complete"))
        # Good callback still fires
        good_cb.assert_called_once()

    def test_subscribe_receives_events(self) -> None:
        router = EventRouter()
        queue = router.subscribe("index.*")
        router.dispatch(_event("index.complete"))
        assert not queue.empty()
        event = queue.get_nowait()
        assert event.type == "index.complete"

    def test_subscribe_filters_events(self) -> None:
        router = EventRouter()
        queue = router.subscribe("test.*")
        router.dispatch(_event("index.complete"))
        assert queue.empty()

    def test_subscribe_default_wildcard(self) -> None:
        router = EventRouter()
        queue = router.subscribe()
        router.dispatch(_event("anything.here"))
        assert not queue.empty()

    def test_full_queue_drops_events(self) -> None:
        router = EventRouter()
        queue = router.subscribe("*", maxsize=1)
        router.dispatch(_event("a"))
        router.dispatch(_event("b"))
        # Queue has size 1, second event should be dropped
        assert queue.qsize() == 1
