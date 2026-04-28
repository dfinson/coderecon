"""Smoke tests for _noop_telemetry stubs."""

from coderecon.core._noop_telemetry import (
    NoOpCounter,
    NoOpHistogram,
    NoOpMeter,
    NoOpObservable,
    NoOpSpan,
    NoOpTracer,
    noop_meter,
    noop_tracer,
)


class TestNoOpSpan:
    def test_context_manager(self) -> None:
        with NoOpSpan() as span:
            assert span is not None

    def test_set_attribute(self) -> None:
        span = NoOpSpan()
        span.set_attribute("key", "value")

    def test_set_attributes(self) -> None:
        span = NoOpSpan()
        span.set_attributes({"k": "v"})

    def test_add_event(self) -> None:
        span = NoOpSpan()
        span.add_event("evt", {"a": 1})

    def test_set_status(self) -> None:
        NoOpSpan().set_status("OK")

    def test_record_exception(self) -> None:
        NoOpSpan().record_exception(RuntimeError("boom"))

    def test_is_recording(self) -> None:
        assert NoOpSpan().is_recording() is False


class TestNoOpTracer:
    def test_start_span(self) -> None:
        span = NoOpTracer().start_span("op")
        assert isinstance(span, NoOpSpan)

    def test_start_as_current_span(self) -> None:
        span = NoOpTracer().start_as_current_span("op")
        assert isinstance(span, NoOpSpan)

    def test_start_as_current_span_cm(self) -> None:
        with NoOpTracer().start_as_current_span_cm("op") as span:
            assert isinstance(span, NoOpSpan)


class TestNoOpMeter:
    def test_create_counter(self) -> None:
        c = NoOpMeter().create_counter("hits")
        assert isinstance(c, NoOpCounter)
        c.add(1)

    def test_create_up_down_counter(self) -> None:
        c = NoOpMeter().create_up_down_counter("gauge")
        assert isinstance(c, NoOpCounter)

    def test_create_histogram(self) -> None:
        h = NoOpMeter().create_histogram("latency")
        assert isinstance(h, NoOpHistogram)
        h.record(42.0)

    def test_create_observable_gauge(self) -> None:
        o = NoOpMeter().create_observable_gauge("g")
        assert isinstance(o, NoOpObservable)

    def test_create_observable_counter(self) -> None:
        o = NoOpMeter().create_observable_counter("c")
        assert isinstance(o, NoOpObservable)

    def test_create_observable_up_down_counter(self) -> None:
        o = NoOpMeter().create_observable_up_down_counter("u")
        assert isinstance(o, NoOpObservable)


class TestModuleSingletons:
    def test_noop_tracer_is_instance(self) -> None:
        assert isinstance(noop_tracer, NoOpTracer)

    def test_noop_meter_is_instance(self) -> None:
        assert isinstance(noop_meter, NoOpMeter)
