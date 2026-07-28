"""AGENT-003: AppInsightsClient.get_trace_spans returns the 5 required
fields per row, sourced from a monkeypatched LogsQueryClient.query_workspace."""

from __future__ import annotations

from dataclasses import dataclass, field

from azure.monitor.query import LogsQueryClient

from app.clients.app_insights_client import AppInsightsClient


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
