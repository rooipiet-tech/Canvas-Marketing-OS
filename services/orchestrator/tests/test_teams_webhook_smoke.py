"""Tests for orchestrator/teams_webhook_smoke.py (round 19j follow-up,
6 Aug 2026) -- the one-off Teams-webhook verification script.

These cover `build_gate_check_kwargs` and `evaluate_response`, the pure
logic kept free of the live gatekeeper HTTP call so it's exercised without
a real ca-gatekeeper. Also asserts the script reuses the REAL
publish.social_post/publish policy pair rather than a synthetic
function_id -- see the module's SECURITY NOTE docstring and
governance-smoke-test-job.bicep's RISK-01 comment for why a synthetic
smoke/test function_id must never be added to policy/autonomy.yaml.
"""

from __future__ import annotations

from orchestrator.teams_webhook_smoke import (
    ACTION_CLASS,
    FUNCTION_ID,
    build_gate_check_kwargs,
    evaluate_response,
)


def test_reuses_the_real_production_policy_entry() -> None:
    """This must be the exact (function_id, action_class) pair
    dispatch.py's REAL_PUBLISH_FUNCTION_ID/REAL_PUBLISH_ACTION_CLASS use
    -- never a synthetic test-only function_id (RISK-01)."""
    assert FUNCTION_ID == "publish.social_post"
    assert ACTION_CLASS == "publish"


def test_build_gate_check_kwargs_shape() -> None:
    kwargs = build_gate_check_kwargs("abc123ef")

    assert kwargs["function_id"] == FUNCTION_ID
    assert kwargs["action_class"] == ACTION_CLASS
    assert kwargs["content_hash"] == "test-teams-webhook-verification-abc123ef"
    assert "TEST" in kwargs["preview_title"]
    assert "SAFE TO REJECT" in kwargs["preview_title"]
    assert kwargs["preview_reference"] == "test://teams-webhook-verification/abc123ef"
    assert "no downstream effect" in kwargs["evidence_summary"]
    # agent_run_id must be a real UUID string (gatekeeper's gate-check
    # rejects a non-UUID agent_run_id outright, HTTP 400).
    import uuid

    uuid.UUID(kwargs["agent_run_id"])  # raises ValueError if malformed


def test_build_gate_check_kwargs_fresh_agent_run_id_each_call() -> None:
    """A fresh agent_run_id every call is what guarantees
    latest_approved() can never find a prior match -- if this were
    deterministic/reused, a second run could accidentally hit the
    already-approved branch and skip posting to Teams."""
    first = build_gate_check_kwargs("tag1")
    second = build_gate_check_kwargs("tag1")
    assert first["agent_run_id"] != second["agent_run_id"]


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
