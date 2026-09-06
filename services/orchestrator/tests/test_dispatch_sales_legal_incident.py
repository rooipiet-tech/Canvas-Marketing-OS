"""Appendix D PR 11 -- Fn 120 (not wired), Fn 124 (Legal Triage sweep),
Fn 125 (Incident Autopilot diagnose).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from orchestrator import dispatch
from orchestrator.models import TaskEnvelope
from tests.fakes import FakeGatewayClient, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _run(
    db: FakeTaskDB, task_id: str, task_type: str, envelope: TaskEnvelope | None = None
) -> None:
    dispatch.DISPATCH_TABLE[task_type](task_id, envelope or _envelope(task_id, task_type), db)


def _envelope_with_metadata(task_id: str, task_type: str, metadata: dict[str, Any]) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=uuid.UUID(task_id),
        task_type=task_type,
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        metadata=metadata,
    )


# --- Fn 120 --------------------------------------------------------------


def test_sales_outcome_infer_is_not_configured(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "sales-outcome-infer")

    _run(db, task_id, "sales-outcome-infer")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "not_configured"


# --- Fn 124 ----------------------------------------------------------------


def _seed_pending_card(clients, *, kind: str = "content.reply") -> str:
    card = clients.create_option_card(
        {
            "card_id": str(uuid.uuid4()),
            "kind": kind,
            "autonomy_level": 2,
            "risk_tier": "low",
            "agent_run_id": None,
            "produced_by_function": 116,
            "card": {
                "card_id": str(uuid.uuid4()),
                "kind": kind,
                "title": "Choose the version to publish",
                "decision_question": "Which version?",
                "recommended_option_id": "A",
                "recommendation_rationale": "A tests better with this audience.",
                "options": [
                    {"option_id": "A", "summary": "Version A summary text."},
                    {"option_id": "B", "summary": "Version B summary text."},
                ],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(),
        }
    )
    return card["card_id"]


class _LegalTriageGatewayClient:
    def __init__(self, *, tier: str, softened_text: str | None = None) -> None:
        self._inner = FakeGatewayClient()
        self._tier = tier
        self._softened_text = softened_text
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_LegalTriageGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if "Legal Triage" not in kw["system_prompt"]:
            return self._inner.complete(**kw)
        output = {
            "tier": self._tier,
            "rule_cited": f"{self._tier} tier rule",
            "softened_text": self._softened_text,
            "rationale": f"Classified {self._tier} because of the test fixture.",
        }
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


def test_legal_triage_sweep_green_writes_no_card(clients, monkeypatch):
    _seed_pending_card(clients)
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _LegalTriageGatewayClient(tier="GREEN")
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "legal-triage-sweep")

    _run(db, task_id, "legal-triage-sweep")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "swept"
    assert ref["green_count"] == 1
    assert ref["amber"] == []
    assert ref["red"] == []


def test_legal_triage_sweep_amber_builds_a_three_option_card(clients, monkeypatch):
    _seed_pending_card(clients)
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _LegalTriageGatewayClient(tier="AMBER", softened_text="Softer claim text."),
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "legal-triage-sweep")

    _run(db, task_id, "legal-triage-sweep")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "swept"
    assert len(ref["amber"]) == 1
    card_row = clients._option_cards[ref["amber"][0]["triage_card_id"]]
    card = card_row["card"]
    assert card["kind"] == "legal.amber"
    assert len(card["options"]) == 3
    assert card["recommended_option_id"] == "B"


def test_legal_triage_sweep_red_builds_a_two_option_realtime_card(clients, monkeypatch):
    _seed_pending_card(clients)
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _LegalTriageGatewayClient(tier="RED")
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "legal-triage-sweep")

    _run(db, task_id, "legal-triage-sweep")

    ref = db.get_result_ref(task_id)
    assert len(ref["red"]) == 1
    card_row = clients._option_cards[ref["red"][0]["triage_card_id"]]
    card = card_row["card"]
    assert card["kind"] == "legal.sensitive_statement"
    assert card["risk_tier"] == "non_negotiable"
    assert len(card["options"]) == 2


def test_legal_triage_sweep_never_retriages_the_same_card(clients, monkeypatch):
    _seed_pending_card(clients)
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _LegalTriageGatewayClient(tier="GREEN")
    )
    db = FakeTaskDB()
    first_id = str(uuid.uuid4())
    db.seed(first_id, "legal-triage-sweep")
    _run(db, first_id, "legal-triage-sweep")
    assert db.get_result_ref(first_id)["green_count"] == 1

    second_id = str(uuid.uuid4())
    db.seed(second_id, "legal-triage-sweep")
    _run(db, second_id, "legal-triage-sweep")
    assert db.get_result_ref(second_id)["green_count"] == 0


# --- Fn 125 ----------------------------------------------------------------


class _IncidentAutopilotGatewayClient:
    def __init__(self, *, options: list[dict[str, Any]], recommended_option: str) -> None:
        self._inner = FakeGatewayClient()
        self._options = options
        self._recommended_option = recommended_option
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_IncidentAutopilotGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if "Incident Autopilot" not in kw["system_prompt"]:
            return self._inner.complete(**kw)
        output = {
            "failure_class": "unsupported_claim",
            "eval_case_summary": "A published claim traced to no source.",
            "options": self._options,
            "recommended_option": self._recommended_option,
            "rationale": "The claim reached readers before the source gap was caught.",
        }
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


_DEFAULT_INCIDENT_OPTIONS = [
    {"label": "correct_in_place", "argument": "Fix the claim in place, keep the post live."},
    {"label": "delete_and_reissue", "argument": "Delete this post and reissue a corrected one."},
    {
        "label": "delete_silently",
        "argument": "Delete without reissuing -- nothing reached readers.",
    },
]


def test_incident_diagnose_with_no_metadata_reports_cleanly(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "incident-diagnose")

    _run(db, task_id, "incident-diagnose")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "no_incident_reported"


def test_incident_diagnose_builds_a_crisis_correction_card(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _IncidentAutopilotGatewayClient(
            options=_DEFAULT_INCIDENT_OPTIONS, recommended_option="correct_in_place"
        ),
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    envelope = _envelope_with_metadata(
        task_id,
        "incident-diagnose",
        {
            "incident_description": "A published LinkedIn post cited a stat with no source.",
            "producing_function_id": "116",
            "reached_an_audience": "true",
        },
    )
    db.seed(task_id, "incident-diagnose")

    _run(db, task_id, "incident-diagnose", envelope)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "diagnosed"
    assert ref["failure_class"] == "unsupported_claim"

    card_row = clients._option_cards[ref["card_id"]]
    card = card_row["card"]
    assert card["kind"] == "crisis.correction"
    assert card["risk_tier"] == "non_negotiable"
    # delete_silently is excluded -- prompt.md: only recommend it when
    # nothing reached an audience, and this incident's metadata says it did.
    labels = {o["label"] for o in card["options"]}
    assert labels == {"correct in place", "delete and reissue"}
    assert card["recommended_option_id"] == "A"


def test_incident_diagnose_allows_delete_silently_when_nothing_reached_an_audience(
    clients, monkeypatch
):
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _IncidentAutopilotGatewayClient(
            options=_DEFAULT_INCIDENT_OPTIONS, recommended_option="delete_silently"
        ),
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    envelope = _envelope_with_metadata(
        task_id,
        "incident-diagnose",
        {
            "incident_description": "A draft leaked to a private preview link, no public traffic.",
            "producing_function_id": "116",
            "reached_an_audience": "false",
        },
    )
    db.seed(task_id, "incident-diagnose")

    _run(db, task_id, "incident-diagnose", envelope)

    ref = db.get_result_ref(task_id)
    card = clients._option_cards[ref["card_id"]]["card"]
    labels = {o["label"] for o in card["options"]}
    assert "delete silently" in labels


def test_incident_diagnose_suspends_a_named_standing_permission(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _IncidentAutopilotGatewayClient(
            options=_DEFAULT_INCIDENT_OPTIONS, recommended_option="correct_in_place"
        ),
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    envelope = _envelope_with_metadata(
        task_id,
        "incident-diagnose",
        {
            "incident_description": "SP-007 kept approving a claim that turned out unsupported.",
            "producing_function_id": "116",
            "permission_id_to_suspend": "SP-007",
        },
    )
    db.seed(task_id, "incident-diagnose")

    _run(db, task_id, "incident-diagnose", envelope)

    ref = db.get_result_ref(task_id)
    assert ref["suspended_permission_id"] == "SP-007"
    suspend_signals = [
        row
        for row in clients._signals.values()
        if row["signal_type"] == dispatch.STANDING_PERMISSION_SUSPENDED_SIGNAL_TYPE
    ]
    assert len(suspend_signals) == 1
    assert suspend_signals[0]["payload"]["permission_id"] == "SP-007"
