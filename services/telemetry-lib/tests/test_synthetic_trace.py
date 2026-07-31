"""GOAL-001 (synthetic-trace half): the synthetic fixture emits exactly 1
root + >=2 child spans sharing one trace_id, each with all 5 required
attributes populated."""

from __future__ import annotations

from telemetry_lib.attributes import REQUIRED_ATTRS
from telemetry_lib.testing.emit_synthetic_trace import _emit_spans


def test_synthetic_trace_shape(tracer, memory_exporter) -> None:
    trace_id_hex = _emit_spans(tracer, task_ref="smoke-test-1")

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 3  # 1 root + 2 children

    trace_ids = {format(s.get_span_context().trace_id, "032x") for s in spans}
    assert trace_ids == {trace_id_hex}

    names = {s.name for s in spans}
    assert names == {"synthetic.root", "synthetic.child.a", "synthetic.child.b"}

    for span in spans:
        for key in REQUIRED_ATTRS:
            assert key in span.attributes
        assert span.attributes["task_ref"] == "smoke-test-1"
