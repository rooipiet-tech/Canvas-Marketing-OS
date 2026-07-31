"""model-gateway telemetry adoption (plan step 15; AC-03, AC-04) — no
live Application Insights needed; exercised against an in-memory OTel
exporter and asserts telemetry never blocks the wrapped call."""

from __future__ import annotations

import pytest
import telemetry_wiring
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    trace.set_tracer_provider(provider)
    return exp


@pytest.fixture(autouse=True)
def _clear(exporter: InMemorySpanExporter):
    exporter.clear()
    yield


def test_configure_tracer_is_a_noop_without_connection_string(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    telemetry_wiring._configured = False
    assert telemetry_wiring.configure_tracer() is False


def test_emits_a_span_with_required_attributes(exporter):
    with telemetry_wiring.emit_completion_span({}, model="claude-haiku", task_ref="task-1") as span:
        assert span is not None

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["function_id"] == "model-gateway.completions"
    assert attrs["task_ref"] == "task-1"
    assert attrs["model"] == "claude-haiku"
    assert attrs["registry_version"] == "1"
    assert attrs["cost"] == 0.0


def test_joins_the_callers_trace_via_traceparent(exporter):
    # A real traceparent header for a fixed trace_id.
    trace_id_hex = "0af7651916cd43dd8448eb211c80319c"
    headers = {"traceparent": f"00-{trace_id_hex}-b7ad6b7169203331-01"}

    with telemetry_wiring.emit_completion_span(headers, model="claude-sonnet", task_ref="t2"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert format(spans[0].get_span_context().trace_id, "032x") == trace_id_hex


def test_never_blocks_the_wrapped_call_on_oversized_model_string(exporter):
    """telemetry_lib.set_span_attribute rejects free-text values over 200
    chars -- this must degrade to a no-op span, never prevent the wrapped
    completion call from running."""
    oversized_model = "x" * 500
    executed = False
    with telemetry_wiring.emit_completion_span({}, model=oversized_model, task_ref="t3") as span:
        executed = True
        assert span is None  # degraded to a no-op span
    assert executed is True


def test_a_real_exception_from_the_wrapped_call_propagates_unchanged(exporter):
    """The critical regression this guards against: a broad except around
    the yield would incorrectly swallow/mutate an exception raised by the
    ACTUAL completion call (e.g. an upstream provider failure) rather
    than telemetry setup itself."""

    class _UpstreamFailure(RuntimeError):
        pass

    with pytest.raises(_UpstreamFailure):
        with telemetry_wiring.emit_completion_span({}, model="claude-haiku", task_ref="t4"):
            raise _UpstreamFailure("simulated provider failure")
