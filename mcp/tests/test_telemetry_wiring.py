"""Shared mcp_common telemetry adoption (plan steps 15-16; AC-03, AC-04)
-- covers mcp-web/mcp-buffer (step 15) and mcp-canva (step 16) in one
place, since all 3 servers share mcp_common.protocol.MCPServer. No live
Application Insights needed; exercised against an in-memory OTel
exporter.
"""

from __future__ import annotations

import pytest
from mcp_common.telemetry import close_tool_call_span, open_tool_call_span
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

SERVERS = ["mcp-web", "mcp-buffer", "mcp-canva"]


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
    span_cm, token, span = open_tool_call_span("mcp-web", {}, tool_name="fetch_url")
    assert span is not None
    close_tool_call_span(span_cm, token)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["function_id"] == "fetch_url"
    assert attrs["model"] == "none"
    assert attrs["cost"] == 0.0


def test_joins_the_callers_trace_via_traceparent(exporter):
    trace_id_hex = "4af7651916cd43dd8448eb211c8031a0"
    headers = {"traceparent": f"00-{trace_id_hex}-b7ad6b7169203331-01"}

    span_cm, token, _span = open_tool_call_span("mcp-buffer", headers, tool_name="list_queue")
    close_tool_call_span(span_cm, token)

    spans = exporter.get_finished_spans()
    assert format(spans[0].get_span_context().trace_id, "032x") == trace_id_hex


def test_close_never_raises_even_with_none_inputs():
    close_tool_call_span(None, None)  # must be a complete no-op, never raise


@pytest.mark.mcp_conformance
@pytest.mark.parametrize("server_name", SERVERS)
def test_mcp_endpoint_still_works_with_telemetry_wired_in(server_name, mcp_client_factory):
    """Sanity check across all 3 servers: the telemetry span open/close
    wired into mcp_common.protocol.MCPServer's shared POST /mcp route
    doesn't change conformance-suite-covered behavior."""
    client, _tools = mcp_client_factory(server_name)
    resp = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == server_name
