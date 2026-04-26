"""Tests for coderecon.sdk.events — event routing and subscription."""

from __future__ import annotations

from coderecon.sdk.events import EventRouter
from coderecon.sdk.types import Event


class TestEventRouter:
    def test_callback_match(self) -> None:
        router = EventRouter()
        received: list[Event] = []
        router.on("daemon.*", received.append)

        evt = Event(type="daemon.ready", data={"v": 1}, ts=1.0)
        router.dispatch(evt)
        assert len(received) == 1
        assert received[0] is evt

    def test_callback_no_match(self) -> None:
        router = EventRouter()
        received: list[Event] = []
        router.on("daemon.*", received.append)

        router.dispatch(Event(type="index.complete", data={}))
        assert len(received) == 0

    def test_callback_exception_swallowed(self) -> None:
        router = EventRouter()
        def bad_cb(e: Event) -> None:
            raise RuntimeError("boom")
        router.on("*", bad_cb)
        # Should not raise
        router.dispatch(Event(type="test", data={}))

    def test_subscribe_queue(self) -> None:
        router = EventRouter()
        queue = router.subscribe("index.*")

        evt = Event(type="index.complete", data={"repo": "x"})
        router.dispatch(evt)

        assert not queue.empty()
        assert queue.get_nowait() is evt

    def test_subscribe_no_match(self) -> None:
        router = EventRouter()
        queue = router.subscribe("index.*")

        router.dispatch(Event(type="daemon.ready", data={}))
        assert queue.empty()

    def test_unsubscribe(self) -> None:
        router = EventRouter()
        queue = router.subscribe("*")
        router.unsubscribe(queue)

        router.dispatch(Event(type="test", data={}))
        assert queue.empty()

    def test_wildcard_subscribe(self) -> None:
        router = EventRouter()
        queue = router.subscribe()  # no patterns = wildcard

        router.dispatch(Event(type="anything", data={}))
        assert not queue.empty()

    def test_multiple_patterns(self) -> None:
        router = EventRouter()
        queue = router.subscribe("daemon.*", "index.*")

        router.dispatch(Event(type="daemon.ready", data={}))
        router.dispatch(Event(type="index.start", data={}))
        router.dispatch(Event(type="other.thing", data={}))

        assert queue.qsize() == 2
