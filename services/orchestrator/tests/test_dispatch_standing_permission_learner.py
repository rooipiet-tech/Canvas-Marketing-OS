"""decision-quality-loop.yaml's standing-permission-learn task --
Appendix D PR 10 (Fn 118 Standing-Permission Learner).

No model call -- deterministic grouping/thresholding over GET
/decision-history, exactly like Fn 126/129's own mechanisms. Uses
FakeVaultClient's decide_card()/list_decision_history() (tests/fakes.py),
same as test_dispatch_decision_quality.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _run(db: FakeTaskDB, task_id: str, task_type: str) -> None:
    dispatch.DISPATCH_TABLE[task_type](task_id, _envelope(task_id, task_type), db)


def _seed_decided_card(
    vault: Any,
    *,
    kind: str,
    produced_by_function: int,
    outcome: str,
    was_recommended: bool | None = None,
) -> None:
    card = vault.create_option_card(
        {
            "card_id": str(uuid.uuid4()),
            "kind": kind,
            "autonomy_level": 2,
            "risk_tier": "low",
            "agent_run_id": None,
            "produced_by_function": produced_by_function,
            "card": {
                "card_id": str(uuid.uuid4()),
                "recommended_option_id": "A",
                "options": [{"option_id": "A"}, {"option_id": "B"}],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(),
        }
    )
    vault.decide_card(
        card["card_id"],
        outcome=outcome,
        chosen_option_id="A" if outcome == "chosen" else None,
        was_recommended=was_recommended,
        decided_at=datetime.now(timezone.utc).isoformat(),
    )


def _seed_qualifying_group(
    clients, *, kind: str = "content.reply", function_id: int = 116, count: int = 20
) -> None:
    for _ in range(count):
        _seed_decided_card(
            clients,
            kind=kind,
            produced_by_function=function_id,
            outcome="chosen",
            was_recommended=True,
        )


def test_learner_finds_no_qualifying_groups_with_no_decisions(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "standing-permission-learn")

    _run(db, task_id, "standing-permission-learn")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "no_qualifying_groups"
    assert ref["proposals"] == []


def test_learner_proposes_a_permission_for_a_qualifying_group(clients):
    _seed_qualifying_group(clients)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "standing-permission-learn")
    _run(db, task_id, "standing-permission-learn")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "proposed"
    assert len(ref["proposals"]) == 1
    proposal = ref["proposals"][0]
    assert proposal["permission_id"] == "SP-007"  # SP-001..006 are hand-seeded
    assert proposal["kind"] == "content.reply"
    assert proposal["function_id"] == 116
    assert proposal["decisions"] == 20
    assert proposal["recommendation_hit_rate"] == 1.0

    card_row = clients._option_cards[proposal["card_id"]]
    card = card_row["card"]
    assert card["kind"] == "system.standing_permission"
    assert card["recommended_option_id"] == "A"
    assert len(card["options"]) == 3

    signals = [
        row
        for row in clients._signals.values()
        if row["signal_type"] == dispatch.STANDING_PERMISSION_PROPOSAL_SIGNAL_TYPE
    ]
    assert len(signals) == 1
    assert signals[0]["payload"]["draft_full"]["permission_id"] == "SP-007"
    assert signals[0]["payload"]["draft_full"]["rule"]["hard_exclusions"]
    assert "content.publish" in signals[0]["payload"]["draft_full"]["rule"]["hard_exclusions"]


def test_learner_never_proposes_for_a_non_negotiable_kind(clients):
    _seed_qualifying_group(clients, kind="content.publish", function_id=42)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "standing-permission-learn")
    _run(db, task_id, "standing-permission-learn")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "no_qualifying_groups"
    assert ref["proposals"] == []


def test_learner_skips_a_group_below_the_decision_floor(clients):
    _seed_qualifying_group(clients, count=19)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "standing-permission-learn")
    _run(db, task_id, "standing-permission-learn")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "no_qualifying_groups"


def test_learner_skips_a_group_with_any_rejected_all(clients):
    _seed_qualifying_group(clients, count=20)
    _seed_decided_card(
        clients, kind="content.reply", produced_by_function=116, outcome="rejected_all"
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "standing-permission-learn")
    _run(db, task_id, "standing-permission-learn")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "no_qualifying_groups"


def test_learner_allocates_the_next_id_across_separate_runs(clients):
    # No self-gating exists yet (documented in decision-quality-loop.yaml's
    # own comment -- a tidiness gap, not a correctness one), so a second
    # run that still sees the first run's qualifying group re-proposes it
    # too; what this test actually guards is that permission_id allocation
    # never collides across runs, which it must not regardless.
    _seed_qualifying_group(clients, kind="content.reply", function_id=116)
    db = FakeTaskDB()
    first_id = str(uuid.uuid4())
    db.seed(first_id, "standing-permission-learn")
    _run(db, first_id, "standing-permission-learn")
    first_ids = {p["permission_id"] for p in db.get_result_ref(first_id)["proposals"]}
    assert first_ids == {"SP-007"}

    _seed_qualifying_group(clients, kind="content.reply", function_id=128)
    second_id = str(uuid.uuid4())
    db.seed(second_id, "standing-permission-learn")
    _run(db, second_id, "standing-permission-learn")

    second_ids = [p["permission_id"] for p in db.get_result_ref(second_id)["proposals"]]
    assert len(second_ids) == len(set(second_ids))  # no collisions
    assert all(pid not in first_ids for pid in second_ids)  # strictly new ids
