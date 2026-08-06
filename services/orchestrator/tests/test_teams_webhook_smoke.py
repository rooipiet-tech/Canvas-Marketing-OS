"""Tests for orchestrator/teams_webhook_smoke.py (round 19j follow-up,
6 Aug 2026, updated round 19l 6 Aug 2026) -- the one-off Teams-webhook
verification script.

These cover `build_gate_check_kwargs`, `ensure_test_agent_run`, and
`evaluate_response` -- the pure/injectable logic kept free of live
ca-gatekeeper/ca-vault HTTP calls so it's exercised without either. Also
asserts the script reuses the REAL publish.social_post/publish policy pair
rather than a synthetic function_id -- see the module's SECURITY NOTE
docstring and governance-smoke-test-job.bicep's RISK-01 comment for why a
synthetic smoke/test function_id must never be added to policy/autonomy.yaml.

round 19l addition: the script's first live run (31094556867) 500'd with a
Postgres FK violation because a bare uuid4() agent_run_id has no
corresponding agent_runs row -- see the module docstring's INCIDENT note.
`ensure_test_agent_run` now performs the same real
get_or_create_campaign()/create_agent_run() calls every production
dispatch handler makes; these tests use FakeVaultClient (tests/fakes.py,
already shared by test_dispatch.py/test_worker_loop.py) to prove that
without a live Vault.
"""

from __future__ import annotations

import uuid

from orchestrator.teams_webhook_smoke import (
    ACTION_CLASS,
    FUNCTION_ID,
    TEST_AGENT_NAME,
    TEST_CAMPAIGN_NAME,
    build_gate_check_kwargs,
    ensure_test_agent_run,
    evaluate_response,
)
from tests.fakes import FakeVaultClient


def test_reuses_the_real_production_policy_entry() -> None:
    """This must be the exact (function_id, action_class) pair
    dispatch.py's REAL_PUBLISH_FUNCTION_ID/REAL_PUBLISH_ACTION_CLASS use
    -- never a synthetic test-only function_id (RISK-01)."""
    assert FUNCTION_ID == "publish.social_post"
    assert ACTION_CLASS == "publish"


def test_ensure_test_agent_run_returns_a_real_registered_id() -> None:
    """The id returned must be one FakeVaultClient actually created via
    create_agent_run -- i.e. the same real, governed Vault write path
    every production dispatch handler uses, not a bare uuid4() with no
    backing row (round 19l's INCIDENT)."""
    vault = FakeVaultClient()
    agent_run_id = ensure_test_agent_run(vault, "abc123ef")

    # get_agent_run raises KeyError if the id was never actually created.
    row = vault.get_agent_run(agent_run_id)
    assert row["agent_name"] == TEST_AGENT_NAME
    uuid.UUID(agent_run_id)  # raises ValueError if malformed


def test_ensure_test_agent_run_reuses_the_same_test_campaign() -> None:
    """get_or_create_campaign is idempotent by name -- two calls (as two
    separate script invocations would make) must land on the SAME
    campaign, not create a new one each run."""
    vault = FakeVaultClient()
    ensure_test_agent_run(vault, "run-a")
    ensure_test_agent_run(vault, "run-b")

    campaign_id_1 = vault.get_or_create_campaign(TEST_CAMPAIGN_NAME, function_id=FUNCTION_ID)
    assert len(vault._campaigns) == 1
    assert vault._campaigns[campaign_id_1]["name"] == TEST_CAMPAIGN_NAME


def test_build_gate_check_kwargs_shape() -> None:
    agent_run_id = str(uuid.uuid4())
    kwargs = build_gate_check_kwargs("abc123ef", agent_run_id)

    assert kwargs["agent_run_id"] == agent_run_id
    assert kwargs["function_id"] == FUNCTION_ID
    assert kwargs["action_class"] == ACTION_CLASS
    assert kwargs["content_hash"] == "test-teams-webhook-verification-abc123ef"
    assert "TEST" in kwargs["preview_title"]
    assert "SAFE TO REJECT" in kwargs["preview_title"]
    assert kwargs["preview_reference"] == "test://teams-webhook-verification/abc123ef"
    assert "no downstream effect" in kwargs["evidence_summary"]


def test_evaluate_response_pass() -> None:
    passed, message = evaluate_response({"outcome": "escalated", "approval_route": "teams"})
    assert passed is True
    assert "PASS" in message
    assert "Teams" in message


def test_evaluate_response_fails_on_wrong_outcome() -> None:
    passed, message = evaluate_response({"outcome": "rejected", "approval_route": None})
    assert passed is False
    assert "expected outcome 'escalated'" in message


def test_evaluate_response_fails_on_inbox_fallback() -> None:
    """outcome=='escalated' but approval_route=='inbox' means
    TEAMS_WEBHOOK_URL didn't resolve on ca-gatekeeper -- the card never
    reached Teams, only the local approval inbox. Must fail, not pass."""
    passed, message = evaluate_response({"outcome": "escalated", "approval_route": "inbox"})
    assert passed is False
    assert "TEAMS_WEBHOOK_URL" in message


def test_evaluate_response_fails_on_missing_fields() -> None:
    passed, message = evaluate_response({})
    assert passed is False
