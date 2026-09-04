"""options-approval-loop.yaml -- Appendix D PR 5 (Fn 116 compose_options_
handler, Fn 117 route_digest_handler).

Mirrors test_dispatch_qa_verdict.py's _seed_reviewable_draft /
_VerdictGatewayClient pattern: a completed Wednesday-draft-shaped ancestor
task with a real Vault asset, and an injectable gateway that returns a
caller-chosen verdict for the QA calls this loop makes independently of
the Options Composer completion.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import FakeGatewayClient, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

CLEAN_QA_VERDICT = {"pass": True, "violations": [], "notes": ""}
# Fn 02 and Fn 48 each validate `violations` against their OWN, disjoint
# enum (functions/02-brand-steward-qa/schema.json vs functions/48-fact-
# check-verdict/schema.json share no violation code), so "blocking" needs
# one schema-valid code per function, not one shared string.
BLOCKING_BRAND_VERDICT = {
    "pass": False,
    "violations": ["unsupported-claim"],
    "notes": "reads as an ad, not a post",
}
BLOCKING_FACT_VERDICT = {
    "pass": False,
    "violations": ["fabricated-proof-point"],
    "notes": "claim traces to nothing in the approved proof list",
}


class _QAGatewayClient:
    """FakeGatewayClient for everything except Brand Steward / Fact-Check
    (which get `brand_verdict`/`fact_verdict` respectively) and Options
    Composer (which gets `alternates_output`, or the real fake's canned
    response if `alternates_output` is None)."""

    def __init__(
        self,
        *,
        brand_verdict: dict[str, Any] = CLEAN_QA_VERDICT,
        fact_verdict: dict[str, Any] = CLEAN_QA_VERDICT,
        alternates_output: dict[str, Any] | None = None,
    ) -> None:
        self._inner = FakeGatewayClient()
        self._brand_verdict = brand_verdict
        self._fact_verdict = fact_verdict
        self._alternates_output = alternates_output
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_QAGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        prompt = kw["system_prompt"]
        # "Options Composer" MUST be checked first, unconditionally:
        # functions/116-options-composer/prompt.md's own text mentions
        # "Brand Steward 3, Fact Check 48" (step 3, describing what
        # compose_options_handler's OWN separate _run_option_qa calls do
        # downstream) -- the exact misdetection trap tests/fakes.py's
        # real FakeGatewayClient already documents for two earlier
        # scanners, sprung a third time here without this ordering.
        if "Options Composer" in prompt:
            if self._alternates_output is not None:
                content = json.dumps(self._alternates_output)
            else:
                return self._inner.complete(**kw)
        elif "Fact-Check Verdict" in prompt:
            # Checked BEFORE "Brand Steward": functions/48-fact-check-
            # verdict/prompt.md's own text mentions "Brand Steward"
            # repeatedly (it describes the boundary between the two
            # checks) -- same ordering the real FakeGatewayClient in
            # tests/fakes.py already uses for this exact reason.
            content = json.dumps(self._fact_verdict)
        elif "Brand Steward" in prompt:
            content = json.dumps(self._brand_verdict)
        else:
            return self._inner.complete(**kw)
        return {
            "id": "fake",
            "model": kw["model"],
            "content": content,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _seed_wednesday_draft(
    db: FakeTaskDB,
    vault: Any,
    *,
    draft_task_type: str = "draft-executive-ghostwrite",
    draft_text: str = "Consolidation at scale: one governed source of truth. Read more.",
    proof_points: list[dict[str, str]] | None = None,
) -> str:
    draft_id = str(uuid.uuid4())
    db.seed(draft_id, draft_task_type)
    asset = vault.create_asset(
        asset_type="linkedin_post",
        agent_run_id=None,
        campaign_id=None,
        function_id=dispatch.FUNCTION_ID_43,
        content_bytes=draft_text.encode("utf-8"),
        approval_state="draft",
    )
    db.set_result_ref(
        draft_id,
        {
            "vault_asset_id": asset["id"],
            "content_hash": asset["content_hash"],
            "pillar": "Consolidation at scale",
            "campaign": "consolidation-at-scale",
            "proof_points": proof_points
            or [{"claim": "40+ business units consolidated", "source": "https://example.com/a"}],
        },
    )
    db.transition(draft_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)
    return draft_id


def _run_compose_options(
    monkeypatch,
    db,
    draft_id,
    *,
    brand_verdict=CLEAN_QA_VERDICT,
    fact_verdict=CLEAN_QA_VERDICT,
):
    compose_id = str(uuid.uuid4())
    db.seed(compose_id, "compose-options", depends_on=[draft_id])
    gateway = _QAGatewayClient(brand_verdict=brand_verdict, fact_verdict=fact_verdict)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)
    dispatch.compose_options_handler(compose_id, _envelope(compose_id, "compose-options"), db)
    return compose_id, gateway


def test_compose_options_builds_a_three_option_digest_card(clients, monkeypatch):
    vault = clients
    db = FakeTaskDB()
    draft_id = _seed_wednesday_draft(db, vault)

    compose_id, _gateway = _run_compose_options(monkeypatch, db, draft_id)

    assert db.get_task(compose_id)["state"] == "completed"
    ref = db.get_result_ref(compose_id)
    assert ref["pass"] is True
    assert ref["surviving_count"] == 3

    card_id = ref["card_id"]
    row = vault._option_cards[card_id]
    card = row["card"]
    assert card["kind"] == "content.reply"  # not content.publish -- see dispatch.py's own note
    assert card["budget_class"] == "digest"
    assert [o["option_id"] for o in card["options"]] == ["A", "B", "C"]
    assert card["recommended_option_id"] in {"A", "B", "C"}
    assert all(o["evidence_refs"] for o in card["options"])
    assert row["produced_by_function"] == 116


def test_compose_options_skips_cleanly_when_the_draft_was_never_attempted(clients, monkeypatch):
    db = FakeTaskDB()
    draft_id = str(uuid.uuid4())
    db.seed(draft_id, "draft-executive-ghostwrite")
    db.set_result_ref(draft_id, {"status": "no_executive_configured"})
    db.transition(draft_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)

    compose_id, _gateway = _run_compose_options(monkeypatch, db, draft_id)

    assert db.get_task(compose_id)["state"] == "completed"
    assert db.get_result_ref(compose_id)["composed"] is False


def test_compose_options_dead_letters_when_every_candidate_fails_qa(clients, monkeypatch):
    db = FakeTaskDB()
    draft_id = _seed_wednesday_draft(db, clients)

    compose_id, gateway = _run_compose_options(
        monkeypatch,
        db,
        draft_id,
        brand_verdict=BLOCKING_BRAND_VERDICT,
        fact_verdict=BLOCKING_FACT_VERDICT,
    )

    assert db.get_task(compose_id)["state"] == "failed"
    ref = db.get_result_ref(compose_id)
    assert ref["pass"] is False
    assert ref["surviving_count"] == 0
    assert clients._option_cards == {}  # never persisted an invalid card

    # 1 Options Composer call + 3 candidates x 2 QA calls (brand+fact) = 7
    assert len(gateway.calls) == 7


def _seed_pending_option_card(vault: Any, *, kind: str = "content.reply") -> str:
    from options_inbox.cards import build_card

    now = datetime.now(timezone.utc)
    card = build_card(
        kind=kind,
        level=2,
        title="test card",
        decision_question="Which version?",
        options=[
            {
                "option_id": "A",
                "label": "A",
                "summary": "summary A",
                "payload_ref": "vault://asset/a",
                "evidence_refs": [{"source_type": "vault_asset", "ref": "x"}],
                "predicted_outcome": "y",
                "risks": [],
                "distinctness_axis": "hook",
            },
            {
                "option_id": "B",
                "label": "B",
                "summary": "summary B",
                "payload_ref": "vault://asset/b",
                "evidence_refs": [{"source_type": "vault_asset", "ref": "x"}],
                "predicted_outcome": "y",
                "risks": [],
                "distinctness_axis": "audience",
            },
        ],
        recommended="A",
        evidence_refs=[{"source_type": "vault_asset", "ref": "x"}],
        produced_by={"function_id": 116, "prompt_version": "0.1.0"},
        register_rows=["H9"],
        now=now,
    )
    row = vault.create_option_card(
        {
            "card_id": card["card_id"],
            "kind": card["kind"],
            "autonomy_level": card["autonomy_level"],
            "risk_tier": card["risk_tier"],
            "agent_run_id": None,
            "produced_by_function": 116,
            "card": card,
            "created_at": card["created_at"],
            "expires_at": card["expires_at"],
        }
    )
    return row["card_id"]


def test_route_digest_computes_routing_without_posting_when_unconfigured(
    clients, monkeypatch
):
    monkeypatch.delenv("CMOS_APPROVAL_BASE_URL", raising=False)
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    _seed_pending_option_card(clients)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "route-digest")
    dispatch.route_digest_handler(task_id, _envelope(task_id, "route-digest"), db)

    ref = db.get_result_ref(task_id)
    assert ref["budget_used"] == 1
    assert len(ref["sent"]) == 1
    assert ref["posted"] is False


def test_route_digest_posts_a_real_digest_when_configured(clients, monkeypatch):
    monkeypatch.setenv("CMOS_APPROVAL_BASE_URL", "https://approval.example")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://teams.example/webhook")
    card_id = _seed_pending_option_card(clients)

    posted: list[dict[str, Any]] = []

    def fake_http_post(url, json, timeout):  # noqa: A002 - matches httpx.post's own signature
        posted.append({"url": url, "json": json})

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr("httpx.post", fake_http_post)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "route-digest")
    dispatch.route_digest_handler(task_id, _envelope(task_id, "route-digest"), db)

    assert db.get_result_ref(task_id)["posted"] is True
    assert len(posted) == 1
    assert posted[0]["url"] == "https://teams.example/webhook"
    body = posted[0]["json"]["attachments"][0]["content"]["body"]
    actionset = [item for item in body[-1]["items"] if item["type"] == "ActionSet"][0]
    urls = {action["title"]: action["url"] for action in actionset["actions"]}
    assert f"card={card_id}" in urls["Choose A"]
    assert "opt=A" in urls["Choose A"]
    assert "outcome=rejected_all" in urls["Reject all"]


def test_route_digest_respects_the_daily_budget(clients, monkeypatch):
    monkeypatch.delenv("CMOS_APPROVAL_BASE_URL", raising=False)
    for _ in range(9):
        _seed_pending_option_card(clients)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "route-digest")
    dispatch.route_digest_handler(task_id, _envelope(task_id, "route-digest"), db)

    ref = db.get_result_ref(task_id)
    # policies/autonomy-matrix.yaml's approval_budget.cards_per_working_day
    # is 6 (services/options_inbox/tests/test_options_inbox.py's own
    # test_budget_caps_digest_and_queues_overflow asserts the same split).
    assert ref["budget_used"] == 6
    assert ref["queued_overflow_count"] == 3
