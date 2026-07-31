"""AC-03 — every stage's span is linked under one trace id, and every
span carries all 5 telemetry_lib-required attributes matching console's
REQUIRED_SPAN_FIELDS.

The trace_id-SHARING mechanism itself (telemetry_wiring.emit_task_span's
run_id-derived parent context) is proven directly, with an in-memory OTel
exporter, in services/orchestrator/tests/test_telemetry_wiring.py — no
live Application Insights needed there. This file proves the OTHER half:
against a real live run, each of the 5 stages actually produced >=1 span
in the real span store, using console's own AppInsightsClient/KQL query
shape (the exact mechanism AC-03's verify text names), cross-checked
against REQUIRED_SPAN_FIELDS.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSOLE_ROOT = REPO_ROOT / "console"
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))

EXPECTED_STAGE_TYPES = [
    "ingest-signals",
    "draft-brief",
    "qa-review",
    "draft-content",
    "request-approval",
]


@pytest.fixture(scope="session")
def app_insights_workspace_id() -> str:
    import os

    workspace_id = os.environ.get("APPLICATIONINSIGHTS_WORKSPACE_ID")
    if not workspace_id:
        pytest.skip(
            "APPLICATIONINSIGHTS_WORKSPACE_ID not set — this test needs a live Log "
            "Analytics workspace to query spans from"
        )
    return workspace_id


def test_each_stage_has_at_least_one_span_with_required_fields(
    live_daily_loop_run: dict[str, Any], app_insights_workspace_id: str
) -> None:
    from app.clients.app_insights_client import REQUIRED_SPAN_FIELDS, AppInsightsClient

    client = AppInsightsClient(app_insights_workspace_id)
    final_state = live_daily_loop_run["final_state"]
    stages_by_type = {s["task_type"]: s for s in final_state.get("stages", [])}

    for task_type in EXPECTED_STAGE_TYPES:
        stage = stages_by_type.get(task_type)
        assert stage is not None, f"no stage found for {task_type}"
        spans = client.get_trace_spans(stage["task_id"])
        assert spans, f"{task_type} (task_ref={stage['task_id']}) has zero spans in App Insights"
        for span in spans:
            for field in REQUIRED_SPAN_FIELDS:
                assert span.get(field), f"{task_type} span missing required field {field!r}: {span}"
