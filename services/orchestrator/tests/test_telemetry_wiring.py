"""orchestrator/telemetry_wiring.py — AC-03's "every stage's span linked
under one trace id" mechanism, tested directly with an in-memory OTel
exporter (no live Application Insights needed).

OTel's global TracerProvider can only be SET once per process (a second
`trace.set_tracer_provider(...)` call is silently ignored, with a
warning) -- so the in-memory exporter is installed ONCE, module-scoped,
and each test clears it first rather than each installing its own.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from orchestrator.telemetry_wiring import emit_task_span, inject_traceparent


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    trace.set_tracer_provider(provider)
    return exp


@pytest.fixture(autouse=True)
def _clear_exporter(exporter: InMemorySpanExporter):
    exporter.clear()
    yield


def test_two_independently_dispatched_spans_share_one_trace_id_via_run_id(exporter):
    """Simulates 2 SEPARATE task dispatches (each its own independent
    emit_task_span call, exactly how worker.py's asyncio.to_thread-per-
    task-message dispatch works) for the SAME run (same campaign_id) --
    both spans must land under the same trace_id."""
    run_id = "9d1c1a2e-2222-4444-8888-abcdefabcdef"

    with emit_task_span(
        "ingest-signals", function_id="09", task_ref="t1", model="claude-haiku", run_id=run_id
    ):
        pass

    with emit_task_span(
        "draft-brief", function_id="brief.compose", task_ref="t2", model="none", run_id=run_id
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    trace_ids = {span.get_span_context().trace_id for span in spans}
    assert len(trace_ids) == 1, "both spans must share exactly one trace_id"


def test_different_run_ids_get_different_trace_ids(exporter):
    with emit_task_span(
        "ingest-signals", function_id="09", task_ref="t1", model="claude-haiku", run_id="run-a"
    ):
        pass
    with emit_task_span(
        "ingest-signals", function_id="09", task_ref="t2", model="claude-haiku", run_id="run-b"
    ):
        pass

    spans = exporter.get_finished_spans()
    trace_ids = {span.get_span_context().trace_id for span in spans}
    assert len(trace_ids) == 2


def test_traceparent_injected_reflects_the_run_scoped_trace_id(exporter):
    """The header orchestrator/clients/*.py attaches to every outbound
    call, captured from WITHIN an emit_task_span block, must encode the
    SAME trace_id as the span itself -- this is what lets an adopting
    downstream service (steps 15-16) join the same trace."""
    run_id = "11111111-2222-3333-4444-555555555555"

    captured_headers: dict[str, str] = {}
    with emit_task_span(
        "draft-content", function_id="42", task_ref="t3", model="claude-sonnet", run_id=run_id
    ) as span:
        captured_headers.update(inject_traceparent())
        span_trace_id = span.get_span_context().trace_id

    assert "traceparent" in captured_headers
    # traceparent format: 00-<32 hex trace_id>-<16 hex span_id>-<2 hex flags>
    parts = captured_headers["traceparent"].split("-")
    assert len(parts) == 4
    assert int(parts[1], 16) == span_trace_id
