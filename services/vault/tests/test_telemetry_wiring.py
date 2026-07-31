"""Vault telemetry adoption (plan step 15; AC-03, AC-04) — no live
Application Insights or Postgres needed; exercised against an in-memory
OTel exporter and (for the smoke check) the dependency-free GET /health
route.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from vault.telemetry_wiring import close_request_span, open_request_span  # noqa: E402


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


def test_open_close_emits_a_span_with_required_attributes(exporter):
    span_cm, token, span = open_request_span({}, function_id="assets", task_ref="obj-1")
    assert span is not None
    close_request_span(span_cm, token)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["function_id"] == "assets"
    assert attrs["task_ref"] == "obj-1"
    assert attrs["model"] == "none"
    assert attrs["cost"] == 0.0


def test_joins_the_callers_trace_via_traceparent(exporter):
    trace_id_hex = "3af7651916cd43dd8448eb211c80319f"
    headers = {"traceparent": f"00-{trace_id_hex}-b7ad6b7169203331-01"}

    span_cm, token, _span = open_request_span(headers, function_id="signals", task_ref="obj-2")
    close_request_span(span_cm, token)

    spans = exporter.get_finished_spans()
    assert format(spans[0].get_span_context().trace_id, "032x") == trace_id_hex


def test_close_never_raises_even_with_none_inputs():
    close_request_span(None, None)  # must be a complete no-op, never raise


def test_health_endpoint_still_works_with_middleware_installed():
    """Sanity check: the telemetry span-open/close added to the existing
    structured_request_logging middleware doesn't break the
    dependency-free /health route."""
    from fastapi.testclient import TestClient
    from vault.main import app

    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
