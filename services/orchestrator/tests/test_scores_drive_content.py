"""The daily loop's scores now reach the weekly content loop (F-SCORES-UNREAD).

Scoring ranked signals and nothing read the ranking except the order of a
bullet list in the morning brief. The weekly loop -- which produces
everything Canvas actually publishes -- chose its pillar with
`CONTENT_PILLARS[week_number % 5]`, reading no signal, card or score. The
daily loop could report a market on fire and the weekly loop would still
write about whatever the calendar said.

And function 41 received `{"pillar": ...}` alone while its own schema
requires `signal_summary` -- the field described as "the raw signal or
opportunity-card text this brief is built from... a brief must never
invent evidence the signal does not supply". A cited brief, requested
with no sources, feeding all five Wednesday drafting functions.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"


def _item(headline: str, pillar: str, confidence: str, url: str = FABRIC_URL) -> dict[str, Any]:
    return {
        "headline": headline,
        "so_what": "why it matters",
        "source_url": url,
        "pillar": pillar,
        "confidence": confidence,
    }


def _signal_row(items: list[dict[str, Any]], *, age_days: float = 1.0) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "signal_type": dispatch.SIGNAL_BATCH_TYPE,
        "payload": {"topic": "t", "horizon_days": 30, "summary": "s" * 60, "signals": items},
        "received_at": (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(),
    }


class _StubVault:
    def __init__(self, rows: list[dict[str, Any]] | None = None, raises: bool = False) -> None:
        self._rows = rows or []
        self._raises = raises

    def __enter__(self) -> "_StubVault":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def list_signals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if self._raises:
            raise RuntimeError("vault unreachable (test)")
        return self._rows[:limit]


# ---------------------------------------------------------------------
# Reading the week's evidence
# ---------------------------------------------------------------------


def test_recent_signals_are_scored_with_the_same_rule_score_signals_uses():
    vault = _StubVault([_signal_row([_item("A high one", "Fabric-native", "high")])])

    scored = dispatch._recent_scored_signals(vault)

    assert len(scored) == 1
    assert scored[0]["score"] == dispatch._score_signal({"confidence": "high"})
    assert scored[0]["pillar"] == "Fabric-native"


def test_signals_outside_the_lookback_window_do_not_vote():
    vault = _StubVault([_signal_row([_item("Old", "Fabric-native", "high")], age_days=30)])

    assert dispatch._recent_scored_signals(vault, days=7) == []


def test_fan_out_cards_are_skipped_rather_than_bucketed_under_a_guess():
    """Scanner cards carry a taxonomy, not a pillar. They are real signal
    but cannot vote on a pillar, so they must not be counted."""
    row = _signal_row([])
    row["signal_type"] = dispatch.CARD_BATCH_TYPE
    row["payload"] = {
        "topic": "t",
        "cards": [{"headline": "A card", "source_url": FABRIC_URL, "taxonomy": "tender-signal"}],
    }

    assert dispatch._recent_scored_signals(_StubVault([row])) == []


def test_an_unreachable_vault_yields_no_evidence_rather_than_raising(caplog):
    with caplog.at_level("WARNING"):
        assert dispatch._recent_scored_signals(_StubVault(raises=True)) == []

    assert "recent_signals_unavailable" in caplog.text


# ---------------------------------------------------------------------
# Choosing the pillar
# ---------------------------------------------------------------------


def test_the_pillar_with_the_strongest_evidence_wins_not_the_noisiest():
    """Three low-confidence mentions must not outweigh one well-evidenced
    move -- the sum of scores decides, not the count."""
    scored = dispatch._recent_scored_signals(
        _StubVault(
            [
                _signal_row(
                    [
                        _item("weak 1", "Productised speed", "low"),
                        _item("weak 2", "Productised speed", "low"),
                        _item("weak 3", "Productised speed", "low"),
                        _item("strong", "Fabric-native", "high"),
                    ]
                )
            ]
        )
    )

    assert dispatch._top_pillar(scored) == "Fabric-native"


def test_the_same_evidence_always_chooses_the_same_pillar():
    scored = [
        {"pillar": "Fabric-native", "score": 0.5},
        {"pillar": "Finance-grade trust", "score": 0.5},
    ]

    assert dispatch._top_pillar(scored) == dispatch._top_pillar(list(reversed(scored)))


def test_no_evidence_means_no_pillar_rather_than_a_default():
    assert dispatch._top_pillar([]) is None


# ---------------------------------------------------------------------
# Monday planning
# ---------------------------------------------------------------------


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _plan(db: FakeTaskDB) -> dict[str, Any]:
    task_id = str(uuid.uuid4())
    db.seed(task_id, "plan-content-monday")
    dispatch.plan_content_monday_handler(
        task_id, _envelope(task_id, "plan-content-monday"), db
    )
    return db.get_result_ref(task_id)


def test_monday_follows_the_evidence_when_there_is_some(clients, monkeypatch):
    rows = [_signal_row([_item("A strong Fabric move", "Fabric-native", "high")])]
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: _StubVault(rows))

    ref = _plan(FakeTaskDB())

    assert ref["pillar"] == "Fabric-native"
    assert ref["pillar_source"] == "signals"
    assert ref["scored_signal_count"] == 1


def test_monday_falls_back_to_the_rotation_on_a_quiet_week(clients, monkeypatch):
    """The previous behaviour is the floor, not a new failure mode --
    planning must never block for want of evidence."""
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: _StubVault([]))

    ref = _plan(FakeTaskDB())

    assert ref["pillar"] == dispatch._rotation_pillar()
    assert ref["pillar_source"] == "rotation"


def test_which_decided_is_recorded_because_they_are_different_claims(clients, monkeypatch):
    """"The market chose this" and "the calendar chose this" are very
    different things to say about a week's content."""
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: _StubVault([]))
    assert _plan(FakeTaskDB())["pillar_source"] == "rotation"

    rows = [_signal_row([_item("Evidence", "Beyond the dashboard", "high")])]
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: _StubVault(rows))
    assert _plan(FakeTaskDB())["pillar_source"] == "signals"


def test_the_plan_carries_the_evidence_forward_for_the_brief(clients, monkeypatch):
    rows = [
        _signal_row(
            [
                _item("Top Fabric move", "Fabric-native", "high"),
                _item("Lesser Fabric move", "Fabric-native", "low"),
                _item("Another pillar", "Productised speed", "high", MONEYWEB_URL),
            ]
        )
    ]
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: _StubVault(rows))

    ref = _plan(FakeTaskDB())

    headlines = [item["headline"] for item in ref["top_signals"]]
    assert headlines == ["Top Fabric move", "Lesser Fabric move"]  # chosen pillar only, best first


# ---------------------------------------------------------------------
# Function 41 finally gets its evidence
# ---------------------------------------------------------------------


class _CapturingGateway:
    def __init__(self) -> None:
        from tests.fakes import FakeGatewayClient

        self._inner = FakeGatewayClient()
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_CapturingGateway":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return self._inner.complete(**kw)


def _run_41(db: FakeTaskDB, plan_ref: dict[str, Any], gateway: _CapturingGateway) -> None:
    plan_id = str(uuid.uuid4())
    db.seed(plan_id, "plan-content-monday")
    db.set_result_ref(plan_id, plan_ref)
    db.transition(plan_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)

    task_id = str(uuid.uuid4())
    db.seed(task_id, "draft-research-brief", depends_on=[plan_id])
    dispatch.draft_research_brief_handler(
        task_id, _envelope(task_id, "draft-research-brief"), db
    )


def test_the_brief_is_built_from_the_weeks_actual_signals(clients, monkeypatch):
    gateway = _CapturingGateway()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    _run_41(
        FakeTaskDB(),
        {
            "pillar": "Fabric-native",
            "week_number": 12,
            "pillar_source": "signals",
            "top_signals": [
                {
                    "headline": "A listed group consolidated 14 ERPs",
                    "so_what": "Proof the consolidation pillar lands",
                    "source_url": MONEYWEB_URL,
                    "confidence": "high",
                }
            ],
        },
        gateway,
    )

    sent = json.loads(gateway.calls[0]["user_content"])
    assert "A listed group consolidated 14 ERPs" in sent["signal_summary"]
    assert MONEYWEB_URL in sent["signal_summary"]


def test_the_payload_satisfies_function_41s_own_schema(clients, monkeypatch):
    """The contract violation this closes: every required field present,
    and nothing the schema forbids."""
    gateway = _CapturingGateway()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    _run_41(
        FakeTaskDB(),
        {"pillar": "Fabric-native", "week_number": 3, "top_signals": []},
        gateway,
    )

    sent = json.loads(gateway.calls[0]["user_content"])
    dispatch._validate_function_input(dispatch.FUNCTION_ID_41, sent)  # must not raise
    assert set(sent) == {"pillar", "vertical", "signal_summary"}


def test_a_week_with_no_evidence_says_so_instead_of_sending_nothing(clients, monkeypatch):
    """An absence of evidence must reach the model as a statement, not as
    a blank -- the prompt's own rules then produce an honest low-confidence
    brief rather than invented citations."""
    gateway = _CapturingGateway()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    _run_41(
        FakeTaskDB(),
        {"pillar": "Productised speed", "week_number": 5, "top_signals": []},
        gateway,
    )

    summary = json.loads(gateway.calls[0]["user_content"])["signal_summary"]
    assert "No scored market signals" in summary
    assert "cite nothing that is not supplied" in summary


# ---------------------------------------------------------------------
# The validator itself
# ---------------------------------------------------------------------


def test_input_validation_rejects_the_payload_this_handler_used_to_send():
    """`{"pillar": ...}` alone -- exactly what shipped until now."""
    with pytest.raises(dispatch.DispatchError, match="handler input failed schema.json"):
        dispatch._validate_function_input(dispatch.FUNCTION_ID_41, {"pillar": "Fabric-native"})


def test_input_validation_rejects_a_field_the_schema_forbids():
    with pytest.raises(dispatch.DispatchError, match="handler input failed schema.json"):
        dispatch._validate_function_input(
            dispatch.FUNCTION_ID_41,
            {
                "pillar": "Fabric-native",
                "vertical": "construction",
                "signal_summary": "something",
                "smuggled": "value",
            },
        )
