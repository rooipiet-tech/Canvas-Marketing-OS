"""Emit a synthetic root+children span tree under one trace id (GOAL-001).

Usage:
    python -m telemetry_lib.testing.emit_synthetic_trace --task-ref <ref>

Prints the resulting hex trace_id to stdout on success. Configures the
real Application Insights exporter via configure_tracer_provider() (reads
APPLICATIONINSIGHTS_CONNECTION_STRING) — this module is meant to be run
against a live App Insights resource by deploy-console.yml's gated smoke
job, never by the builder/orchestrator directly (see the no-live-execution
ruling in this session's build instructions).
"""

from __future__ import annotations

import argparse
import sys

from opentelemetry import trace
from opentelemetry.trace import Tracer

from telemetry_lib.attributes import start_span
from telemetry_lib.exporter import configure_tracer_provider


def _emit_spans(tracer: Tracer, task_ref: str) -> str:
    """Emit 1 root span + >=2 child spans sharing one trace id.

    Split out from emit_synthetic_trace() so unit tests can pass in a
    tracer wired to an InMemorySpanExporter without needing a real
    Application Insights connection string.
    """
    trace_id_hex = ""
    with start_span(
        tracer,
        "synthetic.root",
        function_id="telemetry-lib-selftest",
        task_ref=task_ref,
        model="synthetic",
        registry_version="v1",
        cost=0.0,
    ) as root_span:
        trace_id_hex = format(root_span.get_span_context().trace_id, "032x")

        with start_span(
            tracer,
            "synthetic.child.a",
            function_id="telemetry-lib-selftest",
            task_ref=task_ref,
            model="synthetic",
            registry_version="v1",
            cost=0.0,
        ):
            pass

        with start_span(
            tracer,
            "synthetic.child.b",
            function_id="telemetry-lib-selftest",
            task_ref=task_ref,
            model="synthetic",
            registry_version="v1",
            cost=0.0,
        ):
            pass

    return trace_id_hex


def emit_synthetic_trace(task_ref: str) -> str:
    """Configure the real App Insights exporter, emit the synthetic tree,
    force-flush, and return the hex trace_id."""
    provider = configure_tracer_provider()
    tracer = trace.get_tracer("telemetry-lib.synthetic")
    trace_id_hex = _emit_spans(tracer, task_ref)
    provider.force_flush()
    return trace_id_hex


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-ref", required=True, help="task_ref tag applied to every span")
    args = parser.parse_args(argv)

    trace_id_hex = emit_synthetic_trace(args.task_ref)
    print(trace_id_hex)


if __name__ == "__main__":
    sys.exit(main())
