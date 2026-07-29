"""AGENT-003: AppInsightsClient.get_trace_spans returns the 5 required
fields per row, sourced from a monkeypatched LogsQueryClient.query_workspace."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from azure.monitor.query import LogsQueryClient

from app.clients.app_insights_client import (
    AppInsightsClient,
    InvalidTaskRefError,
    build_trace_query,
)


@dataclass
class _FakeTable:
    columns: list[str]
    rows: list[list[object]]


@dataclass
class _FakeResponse:
    tables: list[_FakeTable] = field(default_factory=list)


def test_get_trace_spans_returns_required_fields(monkeypatch) -> None:
    columns = [
        "timestamp",
        "name",
        "function_id",
        "task_ref",
        "model",
        "registry_version",
        "cost",
    ]
    ts = "2026-07-28T00:00:0"
    rows = [
        [f"{ts}0Z", "synthetic.root", "fn-1", "smoke-test-1", "synthetic", "v1", "0.0"],
        [f"{ts}1Z", "synthetic.child.a", "fn-1", "smoke-test-1", "synthetic", "v1", "0.0"],
        [f"{ts}2Z", "synthetic.child.b", "fn-1", "smoke-test-1", "synthetic", "v1", "0.0"],
    ]
    fake_response = _FakeResponse(tables=[_FakeTable(columns=columns, rows=rows)])

    def fake_query_workspace(self, workspace_id, query, timespan):
        return fake_response

    monkeypatch.setattr(LogsQueryClient, "query_workspace", fake_query_workspace)
    monkeypatch.setattr(LogsQueryClient, "__init__", lambda self, credential: None)

    client = AppInsightsClient(workspace_id="fake-workspace-id", credential=object())
    spans = client.get_trace_spans(task_ref="smoke-test-1")

    assert len(spans) == 3
    for span in spans:
        for key in ("function_id", "task_ref", "model", "registry_version", "cost"):
            assert key in span
        assert span["task_ref"] == "smoke-test-1"


# --- RISK-001: task_ref allowlist validation --------------------------------


@pytest.mark.parametrize(
    "task_ref",
    [
        "smoke-test-1",
        "smoke-1690000000",
        "a1b2c3d4-e5f6-4789-a012-3456789abcde",  # uuid4 shape
        "telemetry-lib-selftest_run-0",
    ],
)
def test_build_trace_query_accepts_realistic_task_refs(task_ref: str) -> None:
    query = build_trace_query(task_ref)
    assert f"== '{task_ref}'" in query


@pytest.mark.parametrize(
    "task_ref",
    [
        "smoke' or 1=1 --",
        "task; drop table x",
        "with a space",
        "quote'inside",
        "",
        "a" * 200,
    ],
)
def test_build_trace_query_rejects_kql_breaking_task_refs(task_ref: str) -> None:
    """RISK-001: task_ref is validated against an allowlist BEFORE the KQL
    string is built — an invalid value is rejected outright (400 at the
    route layer), never silently blocklist-stripped into the query."""
    with pytest.raises(InvalidTaskRefError):
        build_trace_query(task_ref)


def test_get_trace_spans_propagates_invalid_task_ref_before_any_network_call(monkeypatch) -> None:
    def fail_if_called(self, workspace_id, query, timespan):
        raise AssertionError("query_workspace must not be called for an invalid task_ref")

    monkeypatch.setattr(LogsQueryClient, "query_workspace", fail_if_called)
    monkeypatch.setattr(LogsQueryClient, "__init__", lambda self, credential: None)

    client = AppInsightsClient(workspace_id="fake-workspace-id", credential=object())
    with pytest.raises(InvalidTaskRefError):
        client.get_trace_spans(task_ref="not valid'")
