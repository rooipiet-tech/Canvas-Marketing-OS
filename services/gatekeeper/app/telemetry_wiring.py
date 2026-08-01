"""Gatekeeper telemetry adoption (plan step 15; AC-03, AC-04; DE-5).

INCIDENT (2026-08-01, live): the original version of this module did
`from telemetry_lib import start_span` unconditionally on every
/gate-check request — but ca-gatekeeper (and ca-publisher) are governance
BUNDLE-deployed services (BUNDLE_MANIFEST.txt + a stock python:3.12-slim
image + `pip install -r requirements.txt`, see
infra/modules/governance/gatekeeper-bundle-unpack.sh), never `pip install
-e services/telemetry-lib` the way Docker-image-built services (model-
gateway/vault/registry) do — there is no mechanism in this deployment
model to install a local sibling package at all. Every single
POST /gate-check 500'd with `ModuleNotFoundError: No module named
'telemetry_lib'`. Fix: this module now implements its OWN small,
self-contained equivalent of telemetry_lib.attributes' SpanAttributeKey /
set_span_attribute / start_span (needs only `opentelemetry-api`, already
a real requirements.txt dependency) instead of importing the package —
duplicated, not shared, deliberately, since this service structurally
cannot depend on a local sibling package. configure_tracer()'s
`telemetry_lib.configure_tracer_provider` call is UNCHANGED and stays
best-effort/optional (already inside its own try/except, degrades to "no
AppInsights export configured" rather than crashing) — Application
Insights export for this service remains a smaller, non-blocking gap
than a 500 on every request; span EMISSION (the part AC-03 actually
needs) now works unconditionally.

Mirrors services/model-gateway/telemetry_wiring.py's overall structure
(a separate, independent implementation; Gatekeeper shares no runtime
package with model-gateway). emit_span() extracts the W3C traceparent
header orchestrator/clients/gatekeeper_client.py already injects and
opens one span per POST /gate-check call as a child of that trace.

Span setup (header extraction, span creation) is isolated into its own
try/except that degrades to a no-op span on any failure — structurally
separate from the wrapped call itself, so a telemetry-side error can
never alter or swallow an exception the real /gate-check handling raises
(see model-gateway/telemetry_wiring.py's docstring for the exact bug
class this avoids).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Span, Tracer

logger = logging.getLogger("gatekeeper")

SERVICE_NAME = "cmos-gatekeeper"

_configured = False

# --- inlined equivalent of telemetry_lib.attributes (see INCIDENT note
# above for why this is duplicated rather than imported) -------------------

_MAX_TEXT_LEN = 200


class _SpanAttributeKey(str, Enum):
    FUNCTION_ID = "function_id"
    TASK_REF = "task_ref"
    MODEL = "model"
    REGISTRY_VERSION = "registry_version"
    COST = "cost"
    STATUS = "status"
    DURATION_MS = "duration_ms"
    ERROR_CODE = "error_code"


def _set_span_attribute(span: Span, key: str, value: Any) -> None:
    try:
        resolved_key = _SpanAttributeKey(key)
    except ValueError:
        return  # not in the closed allowlist — silently dropped, never crashes
    if isinstance(value, str) and len(value) > _MAX_TEXT_LEN:
        return  # free-text-shaped value — silently dropped, never crashes
    span.set_attribute(resolved_key.value, value)


@contextmanager
def _start_span(
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
    with tracer.start_as_current_span(name) as span:
        _set_span_attribute(span, "function_id", function_id)
        _set_span_attribute(span, "task_ref", task_ref)
        _set_span_attribute(span, "model", model)
        _set_span_attribute(span, "registry_version", registry_version)
        _set_span_attribute(span, "cost", cost)
        for key, value in optional_attrs.items():
            _set_span_attribute(span, key, value)
        yield span


def configure_tracer(connection_string: str | None = None) -> bool:
    global _configured
    if _configured:
        return True
    import os

    resolved = connection_string or os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not resolved:
        return False
    try:
        from telemetry_lib import configure_tracer_provider

        configure_tracer_provider(resolved, service_name=SERVICE_NAME)
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash startup
        logger.warning(json.dumps({"event": "telemetry_configure_failed", "error": str(exc)}))
        return False
    _configured = True
    return True


def get_tracer() -> Tracer:
    return trace.get_tracer(SERVICE_NAME)


@contextmanager
def emit_span(
    span_name: str,
    headers: Any,
    *,
    function_id: str,
    task_ref: str,
    registry_version: str = "1",
    **optional_attrs: Any,
) -> Iterator[Span | None]:
    """Opens one span per request, as a child of the caller's traceparent
    (if present). Gate-check/gate-token issuance is never itself an LLM
    call, so model='none'/cost=0.0 (the same honest-placeholder
    convention orchestrator/dispatch.py's non-LLM spans already use).

    See model-gateway/telemetry_wiring.py's docstring for why span setup
    (this function's own header-extraction/span-creation work) is
    isolated from the wrapped call's own exception propagation.
    """
    try:
        parent_context = extract(headers or {})
        token = otel_context.attach(parent_context)
    except Exception:  # noqa: BLE001 - telemetry must never block a real request
        token = None

    span_cm = None
    span: Span | None = None
    try:
        span_cm = _start_span(
            get_tracer(),
            span_name,
            function_id=function_id,
            task_ref=task_ref,
            model="none",
            registry_version=registry_version,
            cost=0.0,
            **optional_attrs,
        )
        span = span_cm.__enter__()
    except Exception as exc:  # noqa: BLE001 - span SETUP only; never the wrapped call
        logger.warning(json.dumps({"event": "telemetry_span_setup_failed", "error": str(exc)}))
        span_cm = None
        span = None

    try:
        yield span
    finally:
        if span_cm is not None:
            import sys

            span_cm.__exit__(*sys.exc_info())
        if token is not None:
            otel_context.detach(token)
