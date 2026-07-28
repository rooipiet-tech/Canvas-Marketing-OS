"""TELE-002: every span emitted via start_span carries the 5 required
structured tags; a call missing any required kwarg raises TypeError."""

from __future__ import annotations

import pytest

from telemetry_lib.attributes import REQUIRED_ATTRS, start_span


def test_required_attributes_present_on_span(tracer, memory_exporter) -> None:
    with start_span(
        tracer,
        "test.span",
        function_id="fn-1",
        task_ref="task-1",
        model="synthetic",
        registry_version="v1",
        cost=0.01,
    ):
        pass

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    for key in REQUIRED_ATTRS:
        assert key in attrs
    assert attrs["function_id"] == "fn-1"
    assert attrs["task_ref"] == "task-1"
    assert attrs["model"] == "synthetic"
    assert attrs["registry_version"] == "v1"
    assert attrs["cost"] == 0.01


def test_missing_required_kwarg_raises_type_error(tracer) -> None:
    with pytest.raises(TypeError):
        with start_span(
            tracer,
            "test.span",
            function_id="fn-1",
            task_ref="task-1",
            model="synthetic",
            # registry_version omitted
            cost=0.01,
        ):
            pass
