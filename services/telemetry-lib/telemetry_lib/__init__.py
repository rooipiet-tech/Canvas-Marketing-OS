"""telemetry-lib — Canvas Marketing OS shared OpenTelemetry helper library.

Wave-1 scope (session/s7-console): this package's own packaging, exporter
configuration, and typed span-attribute API. Other services adopt it in
Wave 2 (see .loop/spec.json out_of_scope) — this build does not modify any
other service directory.
"""

from telemetry_lib.attributes import SpanAttributeKey, set_span_attribute, start_span
from telemetry_lib.exporter import configure_tracer_provider

__all__ = [
    "SpanAttributeKey",
    "configure_tracer_provider",
    "set_span_attribute",
    "start_span",
]
