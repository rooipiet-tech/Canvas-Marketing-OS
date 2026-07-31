"""TELE-003: span-attribute API structurally rejects free-text/unrecognized
values before they ever reach the exporter (mirrors contracts/service-bus/
spec.md's redaction convention, C-2)."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider

from telemetry_lib.attributes import set_span_attribute


@pytest.fixture
def span():
    provider = TracerProvider()
    tracer = provider.get_tracer("redaction-test")
    with tracer.start_as_current_span("redaction.test") as s:
        yield s


def test_long_free_text_value_raises_before_export(span) -> None:
    long_value = "x" * 500
    with pytest.raises(ValueError):
        set_span_attribute(span, "model", long_value)
    # Never actually applied to the span.
    assert "model" not in span.attributes


def test_unrecognized_key_raises(span) -> None:
    with pytest.raises(ValueError):
        set_span_attribute(span, "client_name", "Acme Corp")
    assert "client_name" not in span.attributes


def test_short_value_within_allowlisted_key_succeeds(span) -> None:
    set_span_attribute(span, "model", "claude-haiku")
    assert span.attributes["model"] == "claude-haiku"
