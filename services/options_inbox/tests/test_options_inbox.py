from datetime import datetime, timedelta, timezone

import pytest
from options_inbox.cards import CardError, build_card
from options_inbox.policy import route
from options_inbox.store import MemoryStore, hit_rate, rejection_all_rate
from options_inbox.teams_render import render_digest

NOW = datetime(2026, 9, 4, 5, 15, tzinfo=timezone.utc)
EV = [{"source_type": "fireflies_transcript", "ref": "ff:abc123", "authority": "primary"}]
PRODUCER = {"function_id": 116, "prompt_version": "0.1.0"}


def opts(n=3, evidence=True):
    axes = ["hook", "proof point", "audience"]
    return [
        {
            "option_id": "ABC"[i],
            "label": f"Option {i}",
            "summary": f"Materially different angle {i}",
            "payload_ref": f"vault://asset/{i}",
            "evidence_refs": EV if evidence else [],
            "predicted_outcome": "ICP comments",
            "risks": [],
            "distinctness_axis": axes[i],
        }
        for i in range(n)
    ]


def make(kind="content.reply", level=2, **kw):
    return build_card(
        kind=kind,
        level=level,
        title="t",
        decision_question="Which reply?",
        options=opts(),
        recommended="A",
        evidence_refs=EV,
        produced_by=PRODUCER,
        register_rows=["H9"],
        now=NOW,
        **kw,
    )


def test_level2_card_does_not_default_until_earned():
    c = make()
    assert c["default_on_timeout"] is None and c["budget_class"] == "digest"


def test_level2_card_defaults_once_earned():
    c = make(defaults_earned=True)
    assert c["default_on_timeout"] == "A"


def test_unresolvable_evidence_is_rejected():
    known = {"ff:abc123"}
    make(evidence_resolver=lambda r: r["ref"] in known)  # resolves
    with pytest.raises(CardError, match="fabricated-proof-point"):
        make(evidence_resolver=lambda r: False)


def test_level1_card_never_defaults():
    c = make(kind="content.founder_position", level=1)
    assert c["default_on_timeout"] is None


def test_non_negotiable_kind_is_realtime_and_never_defaults():
    c = make(kind="client.name_or_logo_use", level=2)
    assert (
        c["risk_tier"] == "non_negotiable"
        and c["budget_class"] == "realtime"
        and c["default_on_timeout"] is None
    )


def test_option_without_evidence_needs_novel_flag():
    with pytest.raises(CardError):
        build_card(
            kind="content.founder_position",
            level=1,
            title="t",
            decision_question="q",
            options=opts(evidence=False),
            recommended="A",
            evidence_refs=EV,
            produced_by=PRODUCER,
            register_rows=["H2"],
            now=NOW,
        )
    c = build_card(
        kind="content.founder_position",
        level=1,
        title="t",
        decision_question="q",
        options=opts(evidence=False),
        recommended="A",
        evidence_refs=EV,
        produced_by=PRODUCER,
        register_rows=["H2"],
        now=NOW,
        novel_stance=True,
    )
    assert c["novel_stance"] is True


def test_identical_options_rejected():
    o = opts()
    o[1]["summary"] = o[0]["summary"]
    with pytest.raises(CardError):
        build_card(
            kind="content.reply",
            level=2,
            title="t",
            decision_question="q",
            options=o,
            recommended="A",
            evidence_refs=EV,
            produced_by=PRODUCER,
            register_rows=["H9"],
            now=NOW,
        )


def test_budget_caps_digest_and_queues_overflow():
    pending = [make() for _ in range(9)]
    r = route(pending, [], now=NOW)
    assert len(r.sent) == 6 and len(r.queued_overflow) == 3


def test_realtime_cards_bypass_budget():
    pending = [make() for _ in range(6)] + [make(kind="crisis.correction")]
    r = route(pending, [], now=NOW)
    assert len(r.sent) == 6 and len(r.escalations) == 1


def test_timeout_default_applies_only_when_declared():
    expired_l2 = make(defaults_earned=True)
    expired_l1 = make(kind="content.founder_position", level=1)
    later = NOW + timedelta(days=10)
    r = route([expired_l2, expired_l1], [], now=later)
    assert r.timeout_defaults == [expired_l2]
    assert r.escalations == [expired_l1]  # first re-surface
    r2 = route([expired_l1], [], now=later, resurfaced={expired_l1["card_id"]})
    assert r2.expired_unresolved == [expired_l1]  # second time: closed, never defaulted


def test_standing_permission_auto_resolves_but_never_non_negotiable():
    perm = {
        "permission_id": "SP-X",
        "status": "active",
        "scope": {"card_kinds": ["content.reply", "client.name_or_logo_use"]},
        "rule": {
            "effect": "auto_approve_recommended",
            "condition": "card['autonomy_level'] >= 2",
            "hard_exclusions": [],
        },
    }
    ordinary = make()
    nn = make(kind="client.name_or_logo_use")
    r = route([ordinary, nn], [perm], now=NOW)
    assert [c["card_id"] for c, _ in r.auto_resolved] == [ordinary["card_id"]]
    assert nn in r.escalations


def test_permission_condition_cannot_reach_builtins():
    perm = {
        "permission_id": "SP-EVIL",
        "status": "active",
        "scope": {"card_kinds": ["content.reply"]},
        "rule": {
            "effect": "auto_approve_recommended",
            "condition": "__import__('os').system('true') or True",
        },
    }
    r = route([make()], [perm], now=NOW)
    assert not r.auto_resolved


def test_learning_signal_metrics():
    s = MemoryStore()
    a, b, c = make(), make(), make()
    for card in (a, b, c):
        s.put_card(card)
    s.record(
        {
            "card_id": a["card_id"],
            "outcome": "chosen",
            "chosen_option_id": "A",
            "decided_by": "pieter",
            "channel": "teams_card",
        }
    )
    s.record(
        {
            "card_id": b["card_id"],
            "outcome": "chosen",
            "chosen_option_id": "B",
            "decided_by": "pieter",
            "channel": "teams_card",
        }
    )
    s.record(
        {
            "card_id": c["card_id"],
            "outcome": "rejected_all",
            "rejection_code": "too_generic",
            "decided_by": "pieter",
            "channel": "teams_card",
        }
    )
    d = s.decisions()
    assert hit_rate(d) == 0.5 and rejection_all_rate(d) == pytest.approx(1 / 3)
    assert s.pending() == []


def test_digest_renders_three_buttons_and_picker():
    card = make()
    msg = render_digest(
        [card],
        approval_base_url="https://approval.example",
        signer=lambda cid: "sig",
        overflow_count=2,
        digest_date="2026-09-04",
    )
    body = msg["attachments"][0]["content"]["body"]
    actionset = [i for i in body[-1]["items"] if i["type"] == "ActionSet"][0]
    assert sum(1 for a in actionset["actions"] if a["type"] == "Action.OpenUrl") == 3
    assert any(i["type"] == "Input.ChoiceSet" for i in body[-1]["items"])
