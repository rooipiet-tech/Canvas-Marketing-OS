"""Gatekeeper telemetry adoption (plan step 15; AC-03, AC-04) — no live
Application Insights needed; exercised against an in-memory OTel
exporter."""

from __future__ import annotations

import pytest
from app.telemetry_wiring import emit_span
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


def test_emits_a_span_with_required_attributes(exporter):
    with emit_span(
        "gatekeeper.gate-check", {}, function_id="publish.social_post", task_ref="run-1"
    ) as span:
        assert span is not None

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["function_id"] == "publish.social_post"
    assert attrs["task_ref"] == "run-1"
    assert attrs["model"] == "none"
    assert attrs["cost"] == 0.0


def test_joins_the_callers_trace_via_traceparent(exporter):
    trace_id_hex = "1af7651916cd43dd8448eb211c80319d"
    headers = {"traceparent": f"00-{trace_id_hex}-b7ad6b7169203331-01"}

    with emit_span(
        "gatekeeper.gate-check", headers, function_id="publish.social_post", task_ref="run-2"
    ):
        pass

    spans = exporter.get_finished_spans()
    assert format(spans[0].get_span_context().trace_id, "032x") == trace_id_hex


def test_real_exception_from_wrapped_call_propagates_unchanged(exporter):
    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with emit_span("gatekeeper.gate-check", {}, function_id="x", task_ref="y"):
            raise _Boom("simulated failure")


def test_gate_check_endpoint_still_reachable_with_telemetry_wrapping(client) -> None:
    """Sanity check: the thin telemetry-wrapping shell around
    _gate_check_impl doesn't change the endpoint's real behavior."""
    response = client.post(
        "/gate-check",
        json={
            "agent_run_id": "00000000-0000-0000-0000-000000000000",
            "function_id": "analyse.signal",
            "action_class": "analyse",
        },
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "approved"
