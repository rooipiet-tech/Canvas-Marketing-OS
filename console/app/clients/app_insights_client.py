"""AppInsightsClient — direct KQL query against Application Insights /
Log Analytics for trace-timeline data (AGENT-003).

Uses azure-monitor-query's LogsQueryClient with DefaultAzureCredential
(managed-identity auth in a deployed Container App; falls back through the
usual DefaultAzureCredential chain locally) — no connection-string round
trip, matching this repo's managed-identity-first posture.

The same KQL shape used here is documented verbatim in console/README.md
(AGENT-003) and independently run by deploy-console.yml's gated smoke job
(GOAL-001 live half) — so the documentation and this client stay
proven-correct by construction.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient

REQUIRED_SPAN_FIELDS = ("function_id", "task_ref", "model", "registry_version", "cost")

_QUERY_TEMPLATE = """
union traces, dependencies, requests
| where tostring(customDimensions.task_ref) == '{task_ref}'
| project
    timestamp,
    name,
    function_id = tostring(customDimensions.function_id),
    task_ref = tostring(customDimensions.task_ref),
    model = tostring(customDimensions.model),
    registry_version = tostring(customDimensions.registry_version),
    cost = tostring(customDimensions.cost)
"""


def build_trace_query(task_ref: str) -> str:
    """The exact KQL used by get_trace_spans — also documented in
    console/README.md's "Querying spans directly (agents)" section."""
    safe_task_ref = task_ref.replace("'", "")
    return _QUERY_TEMPLATE.format(task_ref=safe_task_ref)


class AppInsightsClient:
    def __init__(self, workspace_id: str, credential: Any | None = None) -> None:
        self._workspace_id = workspace_id
        self._client = LogsQueryClient(credential or DefaultAzureCredential())

    def get_trace_spans(self, task_ref: str, *, timespan_hours: int = 24) -> list[dict[str, Any]]:
        query = build_trace_query(task_ref)
        response = self._client.query_workspace(
            self._workspace_id, query, timespan=timedelta(hours=timespan_hours)
        )
        spans: list[dict[str, Any]] = []
        for table in response.tables:
            for row in table.rows:
                spans.append(dict(zip(table.columns, row, strict=False)))
        return spans
