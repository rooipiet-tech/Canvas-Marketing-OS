"""Publisher telemetry adoption (plan step 15; AC-03, AC-04) — no live
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
        "publisher.publish", {}, function_id="publish.social_post", task_ref="run-1"
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
    trace_id_hex = "2af7651916cd43dd8448eb211c80319e"
    headers = {"traceparent": f"00-{trace_id_hex}-b7ad6b7169203331-01"}

    with emit_span("publisher.publish", headers, function_id="publish.social_post", task_ref="r2"):
        pass

    spans = exporter.get_finished_spans()
    assert format(spans[0].get_span_context().trace_id, "032x") == trace_id_hex


def test_real_exception_from_wrapped_call_propagates_unchanged(exporter):
    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with emit_span("publisher.publish", {}, function_id="x", task_ref="y"):
            raise _Boom("simulated failure")


def test_publish_endpoint_still_reachable_with_telemetry_wrapping(
    client, conn, agent_run, gate_decision, make_token
) -> None:
    """Sanity check: the thin telemetry-wrapping shell around
    _publish_impl doesn't change the endpoint's real behavior."""
    import base64

    from app.hashing import recompute_content_hash

    asset_bytes = b"telemetry-wrapping sanity check payload"
    content_hash = recompute_content_hash(asset_bytes)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(asset_bytes).decode(),
            "gate_token": token,
        },
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "published"
