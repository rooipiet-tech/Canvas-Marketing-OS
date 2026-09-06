"""Appendix D PR 12 -- Fn 119 (Client Permission Agent), Fn 121 (Visual
Asset Composer), Fn 122 (Foundation Drafter, once-then-quarterly).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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


class _FakePermissionCheck:
    def __init__(self, clearances: dict[str, tuple[bool, str, str]]) -> None:
        self._clearances = clearances

    def check_clearance(self, name: str) -> SimpleNamespace:
        allowed, status, reason = self._clearances.get(
            name, (False, "ABSENT", f"{name!r} does not appear in the register")
        )
        return SimpleNamespace(allowed=allowed, status=status, reason=reason)

    def registered_names(self) -> list[str]:
        return list(self._clearances)


class _PromptMatchedGatewayClient:
    def __init__(self, *, marker: str, output: dict[str, Any]) -> None:
        self._inner = FakeGatewayClient()
        self._marker = marker
        self._output = output
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_PromptMatchedGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if self._marker not in kw["system_prompt"]:
            return self._inner.complete(**kw)
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(self._output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


# --- Fn 119 -----------------------------------------------------------


def test_client_permission_request_reports_cleanly_without_metadata(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "client-permission-request")

    _run(db, task_id, "client-permission-request")

    assert db.get_task(task_id)["state"] == "completed"
    assert db.get_result_ref(task_id)["status"] == "no_request_reported"


def test_client_permission_request_already_permitted_skips_the_model(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "load_permission_check",
        lambda: _FakePermissionCheck({"Acme": (True, "CLEARED", "written permission on file")}),
    )

    def _raise_if_called() -> Any:
        raise AssertionError("no model call should happen on the already_permitted path")

    monkeypatch.setattr(dispatch, "build_gateway_client", _raise_if_called)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    envelope = _envelope_with_metadata(
        task_id,
        "client-permission-request",
        {"client_name": "Acme", "context": "delivery milestone reached"},
    )
    db.seed(task_id, "client-permission-request")

    _run(db, task_id, "client-permission-request", envelope)

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "already_permitted"
    assert ref["clearance_status"] == "CLEARED"


def test_client_permission_request_builds_a_three_option_card(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "load_permission_check",
        lambda: _FakePermissionCheck({}),  # absent from the register -> UNCLEARED-equivalent
    )
    output = {
        "anonymised_path": {
            "passes_combination_test": False,
            "notes": "Industry and shape alone would identify this client.",
        },
        "options": [
            {"label": "named_case_study", "argument": "A full named case study."},
            {"label": "named_logo_and_quote", "argument": "Logo plus a one-line quote."},
            {"label": "anonymised_only", "argument": "No request needed, industry-only."},
        ],
        "recommended_option": "named_logo_and_quote",
        "rationale": "A logo and quote asks less of the relationship than a full case study.",
    }
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _PromptMatchedGatewayClient(marker="Client Permission Agent", output=output),
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    envelope = _envelope_with_metadata(
        task_id,
        "client-permission-request",
        {"client_name": "Imperial", "context": "case study candidate flagged by Fn 47"},
    )
    db.seed(task_id, "client-permission-request")

    _run(db, task_id, "client-permission-request", envelope)

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "requested"
    card = clients._option_cards[ref["card_id"]]["card"]
    assert card["kind"] == "client.permission_request"
    assert len(card["options"]) == 3
    assert card["recommended_option_id"] == "B"
    assert all(o.get("distinctness_axis") for o in card["options"])


# --- Fn 121 -------------------------------------------------------------


def test_visual_asset_compose_not_configured_without_required_metadata(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "visual-asset-compose")

    _run(db, task_id, "visual-asset-compose")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "not_configured"


def test_visual_asset_compose_builds_a_visual_variant_card(clients, monkeypatch):
    output = {
        "axis": "proof-forward vs headline-forward",
        "options": [
            {
                "label": "Proof-forward",
                "csv_row": {"headline": "99.5%+ reconciliation accuracy"},
                "argument": "Leads with the reconciliation proof point.",
            },
            {
                "label": "Headline-forward",
                "csv_row": {"headline": "Month-end, 2 days faster"},
                "argument": "Leads with the time-saved headline.",
            },
        ],
        "recommended_option": "Proof-forward",
        "rationale": "The proof point tests better with this audience.",
    }
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _PromptMatchedGatewayClient(marker="Visual Asset Composer", output=output),
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    envelope = _envelope_with_metadata(
        task_id,
        "visual-asset-compose",
        {
            "asset_type": "post_image",
            "brand_template_id": "TEMPLATE123",
            "copy_source": "Month-end reporting, 2 days faster with CoEaaS.",
        },
    )
    db.seed(task_id, "visual-asset-compose")

    _run(db, task_id, "visual-asset-compose", envelope)

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "composed"
    card = clients._option_cards[ref["card_id"]]["card"]
    assert card["kind"] == "content.visual_variant"
    assert len(card["options"]) == 2
    assert card["recommended_option_id"] == "A"
    assert card["options"][0]["distinctness_axis"] == "proof-forward vs headline-forward"


# --- Fn 122 ---------------------------------------------------------------


def _foundation_output(keys: list[str]) -> dict[str, Any]:
    artefacts = {}
    per_key_docs = {
        "brand_constitution": "docs/positioning.md",
        "metric_definitions": "docs/blueprint/agentic-marketing-engine-v4.md",
        "approver_map": "docs/permission-register.yaml",
    }
    for key in keys:
        artefacts[key] = {
            "title": f"{key} v1",
            "decision_question": f"Which cut of {key} should we ratify?",
            "cited_doc": per_key_docs[key],
            "options": [
                {"label": "Strict", "argument": f"A stricter cut of {key}."},
                {"label": "Balanced", "argument": f"A balanced cut of {key}."},
            ],
            "recommended_option": "Balanced",
            "rationale": f"Balanced best fits {key} today.",
        }
    return {"artefacts": artefacts}


def test_foundation_drafter_bootstrap_not_due_when_signals_are_fresh(clients, monkeypatch):
    for key in ("brand_constitution", "metric_definitions", "approver_map"):
        clients.create_signal(
            source="function-122-foundation-drafter",
            signal_type=dispatch.FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE,
            payload={"artefact_key": key, "card_id": str(uuid.uuid4())},
            campaign_id=str(uuid.uuid4()),
            function_id=dispatch.FUNCTION_ID_122,
        )

    def _raise_if_called() -> Any:
        raise AssertionError("no model call should happen when nothing is due")

    monkeypatch.setattr(dispatch, "build_gateway_client", _raise_if_called)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "foundation-drafter-bootstrap")

    _run(db, task_id, "foundation-drafter-bootstrap")

    assert db.get_result_ref(task_id)["status"] == "not_due"


def test_foundation_drafter_bootstrap_first_run_drafts_all_three(clients, monkeypatch):
    output = _foundation_output(["brand_constitution", "metric_definitions", "approver_map"])
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _PromptMatchedGatewayClient(marker="Foundation Drafter", output=output),
    )
    monkeypatch.setattr(
        dispatch,
        "load_permission_check",
        lambda: _FakePermissionCheck({}),
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "foundation-drafter-bootstrap")

    _run(db, task_id, "foundation-drafter-bootstrap")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "drafted"
    assert {c["artefact_key"] for c in ref["cards"]} == {
        "brand_constitution",
        "metric_definitions",
        "approver_map",
    }
    kinds = {clients._option_cards[c["card_id"]]["card"]["kind"] for c in ref["cards"]}
    assert kinds == {
        "foundation.brand_rule",
        "foundation.metric_definition",
        "foundation.approver_map",
    }

    publish_signals = [
        row
        for row in clients._signals.values()
        if row["signal_type"] == dispatch.FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE
    ]
    assert len(publish_signals) == 3


def test_foundation_drafter_bootstrap_only_drafts_the_artefact_past_its_refit_window(
    clients, monkeypatch
):
    stale_at = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    fresh_at = datetime.now(timezone.utc).isoformat()
    clients._signals["s-stale"] = {
        "id": "s-stale",
        "source": "function-122-foundation-drafter",
        "signal_type": dispatch.FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE,
        "payload": {"artefact_key": "brand_constitution", "card_id": str(uuid.uuid4())},
        "campaign_id": str(uuid.uuid4()),
        "function_id": dispatch.FUNCTION_ID_122,
        "received_at": stale_at,
    }
    for key in ("metric_definitions", "approver_map"):
        clients._signals[f"s-fresh-{key}"] = {
            "id": f"s-fresh-{key}",
            "source": "function-122-foundation-drafter",
            "signal_type": dispatch.FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE,
            "payload": {"artefact_key": key, "card_id": str(uuid.uuid4())},
            "campaign_id": str(uuid.uuid4()),
            "function_id": dispatch.FUNCTION_ID_122,
            "received_at": fresh_at,
        }

    output = _foundation_output(["brand_constitution"])
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _PromptMatchedGatewayClient(marker="Foundation Drafter", output=output),
    )
    monkeypatch.setattr(dispatch, "load_permission_check", lambda: _FakePermissionCheck({}))

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "foundation-drafter-bootstrap")

    _run(db, task_id, "foundation-drafter-bootstrap")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "drafted"
    assert [c["artefact_key"] for c in ref["cards"]] == ["brand_constitution"]
