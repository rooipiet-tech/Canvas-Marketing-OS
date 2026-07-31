"""Application Insights OTel exporter setup (TELE-001, C-1).

C-1 (regional residency, verified via Microsoft Learn's Application
Insights FAQ this session — see .loop/domain.md): data is stored in the
region the Application Insights resource was created in ONLY if the
region-specific connection string is used — the global ingestion endpoint
does not guarantee regional compliance even for a resource nominally "in"
southafricanorth. `configure_tracer_provider` therefore never falls back to
a hardcoded/default global endpoint: it reads the connection string from an
explicit argument or the APPLICATIONINSIGHTS_CONNECTION_STRING env var
(itself sourced, at deploy time, from the region-pinned Application
Insights resource's own `connectionString` output — see
infra/modules/console/app-insights.bicep) and raises loudly if neither is
present, rather than silently degrading telemetry or guessing an endpoint.

Secret-generation note (L-0004, applies_to includes this package): if any
future change to this module generates a token/secret for embedding in a
structured-syntax string (a connection string, a URI), it MUST use a
URI-safe alphabet (e.g. `openssl rand -hex N` / `secrets.token_hex`) —
never `openssl rand -base64`, whose `/+=` alphabet breaks URI parsing a
large fraction of the time. Nothing in this module currently generates a
secret; this note exists so the next editor doesn't reintroduce that bug.
"""

from __future__ import annotations

import os

from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

CONNECTION_STRING_ENV_VAR = "APPLICATIONINSIGHTS_CONNECTION_STRING"


def configure_tracer_provider(
    connection_string: str | None = None,
    *,
    service_name: str = "cmos-console",
) -> TracerProvider:
    """Configure and install a TracerProvider exporting to Application Insights.

    Args:
        connection_string: Application Insights connection string. If not
            supplied, read from the APPLICATIONINSIGHTS_CONNECTION_STRING
            environment variable.
        service_name: OTel resource service.name attribute.

    Raises:
        RuntimeError: if no connection string is supplied and the env var
            is unset/empty. Never falls back to a hardcoded global endpoint.
    """
    resolved = connection_string or os.environ.get(CONNECTION_STRING_ENV_VAR)
    if not resolved:
        raise RuntimeError(
            "No Application Insights connection string was supplied and "
            f"{CONNECTION_STRING_ENV_VAR} is unset/empty. telemetry-lib "
            "never falls back to a hardcoded global endpoint (C-1 regional "
            "residency) — pass connection_string explicitly or set "
            f"{CONNECTION_STRING_ENV_VAR}."
        )

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = AzureMonitorTraceExporter(connection_string=resolved)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider
