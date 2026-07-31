#!/usr/bin/env python3
"""Registry telemetry adoption (plan step 16; AC-03, AC-04).

Run standalone (from repo root), mirroring test_live_path.py's own
convention (this directory has no pytest-discoverable suite -- every
check here is a standalone script, not `pytest`-collected):

    python services/registry/test_telemetry_wiring.py

Exits 0 with PASS, or 1 with FAIL: <reason>.
"""

from __future__ import annotations

import sys
from pathlib import Path

REGISTRY_DIR = Path(__file__).resolve().parent
if str(REGISTRY_DIR) not in sys.path:
    sys.path.insert(0, str(REGISTRY_DIR))

import telemetry_wiring  # noqa: E402 - after the sys.path insert above
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)


def main() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    headers: dict[str, str] = {}
    with telemetry_wiring.emit_live_eval_span(
        function_id="42-linkedin-post-writer", task_ref="lpw-001", model="claude-sonnet"
    ) as span:
        assert span is not None, "expected a real span, not a degraded no-op"
        # Captured FROM WITHIN the span -- this is the real usage shape
        # (inject_traceparent onto the outbound call made inside the span).
        headers.update(telemetry_wiring.inject_traceparent())

    spans = exporter.get_finished_spans()
    assert len(spans) == 1, f"expected exactly 1 span, got {len(spans)}"
    attrs = spans[0].attributes
    assert attrs["function_id"] == "42-linkedin-post-writer"
    assert attrs["task_ref"] == "lpw-001"
    assert attrs["model"] == "claude-sonnet"
    assert attrs["cost"] == 0.0
    assert attrs["registry_version"] == "1"

    assert "traceparent" in headers, "inject_traceparent must carry a traceparent header"

    # A real exception from the wrapped call must propagate unchanged --
    # never swallowed by span-setup error handling.
    class _Boom(RuntimeError):
        pass

    try:
        with telemetry_wiring.emit_live_eval_span(
            function_id="x", task_ref="y", model="claude-haiku"
        ):
            raise _Boom("simulated failure")
    except _Boom:
        pass
    else:
        raise AssertionError("expected _Boom to propagate through emit_live_eval_span")

    print("PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
