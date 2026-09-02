"""The QA verdict's `notes` are the account of a refusal — and model output.

When the model refuses a draft without naming a violation code, its free-text
`notes` are the only explanation of why. That made them load-bearing, and it
made logging them raw feel justified: dispatch.py's own comment read "keep the
model's own words: they are the only account of why".

But they are model output, and nothing scans a model reply in either
direction — `services/model-gateway/redaction.py` defines only `scan_request`.
So a refusal quoting the client name it objected to put that name into
log-cmos-dev verbatim, at two call sites.

The fix keeps the words and moves them: they are persisted to the run's
`agent_run` row, where the Vault's retention policy and access controls govern
them, and the log line carries `agent_run_id` so an operator can still find
them. `output` is free-form in contracts/vault-api.yaml (AgentRunUpdate,
additionalProperties: true), so this is additive.

Found by the Hard Rule 10 audit that claude-review's finding on PR #139
demanded — these two sites were siblings of the one it named.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB
from tests.test_dispatch_qa_verdict import _run_review, _seed_reviewable_draft

# A refusal that quotes the thing it objected to — the realistic shape.
CLIENT_NAME = "Thabo Nkosi"
NOTES = (
    f"Blocking: the draft names {CLIENT_NAME} of Imperial Logistics as a "
    "reference without a clearance on file. Reach him on +27 82 123 4567 to "
    "confirm before publishing."
)

REFUSAL_VERDICT: dict[str, Any] = {"pass": False, "violations": [], "notes": NOTES}


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _agent_run_outputs(vault) -> list[dict]:
    return [row.get("output") or {} for row in vault._agent_runs.values()]


def test_refusal_notes_never_reach_the_log(clients, monkeypatch, caplog):
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(
        db, clients, draft_task_type="draft-content-repurpose"
    )

    with caplog.at_level("WARNING", logger="orchestrator"):
        _run_review(monkeypatch, db, REFUSAL_VERDICT, qa_id)

    assert "qa_verdict_failed_without_violation_code" in caplog.text, (
        "the branch under test did not run — the verdict shape must reach it"
    )
    for secret in (CLIENT_NAME, "Imperial Logistics", "+27 82 123 4567"):
        assert secret not in caplog.text, f"{secret!r} reached the log"


def test_refusal_notes_are_kept_in_the_agent_run_row(clients, monkeypatch):
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(
        db, clients, draft_task_type="draft-content-repurpose"
    )

    _run_review(monkeypatch, db, REFUSAL_VERDICT, qa_id)

    # The words are not lost — that was the whole reason they were logged.
    assert any(out.get("notes") == NOTES for out in _agent_run_outputs(clients)), (
        "the model's account of its refusal must survive somewhere governed"
    )
