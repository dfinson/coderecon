"""No-op telemetry stubs used when OpenTelemetry is not active."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

class NoOpSpan:
    """No-op span for when telemetry is disabled."""

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        return None

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        return None

    def set_status(self, status: Any) -> None:
        return None

    def record_exception(self, exception: Exception) -> None:
        return None

    def is_recording(self) -> bool:
        return False

class NoOpTracer:
    """No-op tracer for when telemetry is disabled."""

    def start_span(self, _name: str, **_kwargs: Any) -> NoOpSpan:
        return NoOpSpan()

    def start_as_current_span(self, _name: str, **_kwargs: Any) -> NoOpSpan:
        return NoOpSpan()

    @contextmanager
    def start_as_current_span_cm(self, _name: str, **_kwargs: Any) -> Any:  # noqa: ANN401 — Generator yield type
        yield NoOpSpan()

class NoOpMeter:
    """No-op meter for when telemetry is disabled."""

    def create_counter(self, _name: str, **_kwargs: Any) -> NoOpCounter:
        return NoOpCounter()

    def create_up_down_counter(self, _name: str, **_kwargs: Any) -> NoOpCounter:
        return NoOpCounter()

    def create_histogram(self, _name: str, **_kwargs: Any) -> NoOpHistogram:
        return NoOpHistogram()

    def create_observable_gauge(
        self, _name: str, _callbacks: Any = None, **_kwargs: Any
    ) -> NoOpObservable:
        return NoOpObservable()

    def create_observable_counter(
        self, _name: str, _callbacks: Any = None, **_kwargs: Any
    ) -> NoOpObservable:
        return NoOpObservable()

    def create_observable_up_down_counter(
        self, _name: str, _callbacks: Any = None, **_kwargs: Any
    ) -> NoOpObservable:
        return NoOpObservable()

class NoOpCounter:
    """No-op counter instrument."""

    def add(self, amount: int | float, attributes: dict[str, Any] | None = None) -> None:
        return None

class NoOpHistogram:
    """No-op histogram instrument."""

    def record(self, amount: int | float, attributes: dict[str, Any] | None = None) -> None:
        return None

class NoOpObservable:
    """No-op observable instrument."""

    pass

noop_tracer = NoOpTracer()
noop_meter = NoOpMeter()
