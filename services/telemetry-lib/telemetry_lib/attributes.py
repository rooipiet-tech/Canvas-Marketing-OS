"""Typed, enum-constrained span-attribute API (TELE-002, TELE-003, C-2).

C-2 (verified via repo precedent this session — contracts/service-bus/spec.md):
OTel span/log attributes must not carry free-text personal information;
only structured/enumerable tags are permitted. This module mirrors that
exact redaction convention for spans: `SpanAttributeKey` is a closed enum
(never an open free-text dict), and `set_span_attribute` structurally
rejects any value shaped like free text (over MAX_TEXT_LEN characters)
before it ever reaches the exporter.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any

from opentelemetry.trace import Span, Tracer

MAX_TEXT_LEN = 200


class SpanAttributeKey(str, Enum):
    """Closed set of allowed span attribute keys.

    The first five are REQUIRED on every span emitted via `start_span`
    (GOAL, TELE-002). The remainder are a small, deliberately narrow
    allowlist of optional structured keys — never an open free-text key
    space.
    """

    FUNCTION_ID = "function_id"
    TASK_REF = "task_ref"
    MODEL = "model"
    REGISTRY_VERSION = "registry_version"
    COST = "cost"

    STATUS = "status"
    DURATION_MS = "duration_ms"
    ERROR_CODE = "error_code"


REQUIRED_ATTRS: tuple[str, ...] = (
    SpanAttributeKey.FUNCTION_ID.value,
    SpanAttributeKey.TASK_REF.value,
    SpanAttributeKey.MODEL.value,
    SpanAttributeKey.REGISTRY_VERSION.value,
    SpanAttributeKey.COST.value,
)


def set_span_attribute(span: Span, key: str | SpanAttributeKey, value: Any) -> None:
    """Set one span attribute, structurally enforcing the redaction convention.

    Raises:
        ValueError: if `key` is not a recognized SpanAttributeKey, or if
            `value` is a string longer than MAX_TEXT_LEN (free-text/
            content-shaped values are rejected, never passed to the
            exporter — mirrors contracts/service-bus/spec.md).
    """
    try:
        resolved_key = SpanAttributeKey(key)
    except ValueError as exc:
        raise ValueError(
            f"{key!r} is not a recognized SpanAttributeKey — the span-attribute "
            "API is a closed, typed set, not an open free-text dict (C-2)."
        ) from exc

    if isinstance(value, str) and len(value) > MAX_TEXT_LEN:
        raise ValueError(
            f"value for {resolved_key.value!r} is {len(value)} chars, exceeding "
            f"MAX_TEXT_LEN={MAX_TEXT_LEN} — free-text/content-shaped values are "
            "rejected structurally and never passed to the exporter (C-2)."
        )

    span.set_attribute(resolved_key.value, value)


@contextmanager
def start_span(
    tracer: Tracer,
    name: str,
    *,
    function_id: str,
    task_ref: str,
    model: str,
    registry_version: str,
    cost: float,
    **optional_attrs: Any,
) -> Iterator[Span]:
    """Open a span carrying the 5 required attributes as first-class tags.

    The 5 required parameters are keyword-only with no defaults, so a call
    missing any of them raises TypeError before this function body ever
    runs (TELE-002). Any additional keyword arguments are applied via
    `set_span_attribute` (so they are still redaction-checked / restricted
    to the optional-key allowlist).
    """
    with tracer.start_as_current_span(name) as span:
        set_span_attribute(span, SpanAttributeKey.FUNCTION_ID, function_id)
        set_span_attribute(span, SpanAttributeKey.TASK_REF, task_ref)
        set_span_attribute(span, SpanAttributeKey.MODEL, model)
        set_span_attribute(span, SpanAttributeKey.REGISTRY_VERSION, registry_version)
        set_span_attribute(span, SpanAttributeKey.COST, cost)
        for key, value in optional_attrs.items():
            set_span_attribute(span, key, value)
        yield span
