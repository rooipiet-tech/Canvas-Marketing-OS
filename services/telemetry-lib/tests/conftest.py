from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(memory_exporter: InMemorySpanExporter):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    return provider.get_tracer("telemetry-lib-tests")
