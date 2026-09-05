"""decision-quality-loop.yaml -- W1 + Appendix D PR 6/7 (Fn 126 Decision
Quality Evaluator, Fn 127 Eval Generator).

Both handlers read GET /decision-history (real approval_decisions joined
with the producing card) via VaultClientExt.list_decision_history --
FakeVaultClient's own decide_card()/list_decision_history() (tests/
fakes.py) stand in for the real Vault join.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import FakeGatewayClient, patch_dispatch_clients
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
    options: list[dict[str, Any]],
    recommended_option_id: str,
    outcome: str,
    chosen_option_id: str | None = None,
    was_recommended: bool | None = None,
    rejection_code: str | None = None,
    decided_at: str | None = None,
) -> str:
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
                "recommended_option_id": recommended_option_id,
                "options": options,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(),
        }
    )
    vault.decide_card(
        card["card_id"],
        outcome=outcome,
        chosen_option_id=chosen_option_id,
        was_recommended=was_recommended,
        rejection_code=rejection_code,
        decided_at=decided_at or datetime.now(timezone.utc).isoformat(),
    )
    return card["card_id"]


def _option(option_id: str, *, distinct: bool = True, evidenced: bool = True) -> dict[str, Any]:
    option: dict[str, Any] = {"option_id": option_id, "summary": f"Option {option_id}"}
    if distinct:
        option["distinctness_axis"] = f"axis-{option_id}"
    if evidenced:
        option["evidence_refs"] = [
            {"source_type": "web_source", "ref": "x", "authority": "primary"}
        ]
    return option


# --- decision_quality_evaluate_handler --------------------------------


def test_decision_quality_evaluate_with_no_decisions_is_empty(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "decision-quality-evaluate")

    _run(db, task_id, "decision-quality-evaluate")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "scored"
    assert ref["function_count"] == 0
    assert ref["improvement_brief_count"] == 0


def test_decision_quality_evaluate_computes_real_metrics(clients):
    options = [_option("A"), _option("B")]
    _seed_decided_card(
        clients,
        kind="content.reply",
        produced_by_function=116,
        options=options,
        recommended_option_id="A",
        outcome="chosen",
        chosen_option_id="A",
        was_recommended=True,
    )
    _seed_decided_card(
        clients,
        kind="content.reply",
        produced_by_function=116,
        options=options,
        recommended_option_id="A",
        outcome="chosen",
        chosen_option_id="B",
        was_recommended=False,
    )
    _seed_decided_card(
        clients,
        kind="content.reply",
        produced_by_function=116,
        options=options,
        recommended_option_id="A",
        outcome="rejected_all",
        rejection_code="options_not_distinct",
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "decision-quality-evaluate")
    _run(db, task_id, "decision-quality-evaluate")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "scored"
    assert ref["function_count"] == 1

    signal = clients.get_signal(ref["vault_signal_id"])
    entry = signal["payload"]["functions"][0]
    assert entry["function_id"] == 116
    assert entry["decisions"] == 3
    assert entry["recommendation_hit_rate"] == 0.5  # 1 of 2 "chosen" decisions matched
    assert entry["rejection_all_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert entry["distinctness_pass_rate"] == 1.0
    assert entry["evidence_coverage"] == 1.0
    assert entry["rejection_codes"] == {"options_not_distinct": 1}

    briefs = signal["payload"]["improvement_briefs"]
    assert len(briefs) == 1
    assert briefs[0]["target_function"] == 102
    assert briefs[0]["rejection_code"] == "options_not_distinct"


# --- decision_quality_level_review_monthly_handler ---------------------


def test_level_review_first_pass_with_no_breach_demotes_nothing(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "decision-quality-level-review-monthly")
    _run(db, task_id, "decision-quality-level-review-monthly")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "review_pass_complete"
    assert ref["demoted"] == []


def test_level_review_is_not_due_again_immediately_after(clients):
    db = FakeTaskDB()
    first_id = str(uuid.uuid4())
    db.seed(first_id, "decision-quality-level-review-monthly")
    _run(db, first_id, "decision-quality-level-review-monthly")

    second_id = str(uuid.uuid4())
    db.seed(second_id, "decision-quality-level-review-monthly")
    _run(db, second_id, "decision-quality-level-review-monthly")

    ref = db.get_result_ref(second_id)
    assert ref["status"] == "not_due"
    assert ref["next_due_in_days"] > 0


def test_level_review_demotes_on_rejection_all_rate_breach(clients):
    options = [_option("A"), _option("B")]
    # earn-in-rules.yaml: rejection_all_rate_gt 0.50, min_decisions 15.
    for _ in range(15):
        _seed_decided_card(
            clients,
            kind="source.promote",
            produced_by_function=128,
            options=options,
            recommended_option_id="A",
            outcome="rejected_all",
            rejection_code="too_generic",
        )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "decision-quality-level-review-monthly")
    _run(db, task_id, "decision-quality-level-review-monthly")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "review_pass_complete"
    assert len(ref["demoted"]) == 1
    assert ref["demoted"][0]["function_id"] == 128
    assert ref["demoted"][0]["trigger"] == "rejection_all_rate_gt"

    card_row = clients._option_cards[ref["demoted"][0]["card_id"]]
    assert card_row["card"]["kind"] == "system.autonomy_level_change"
    assert card_row["card"]["recommended_option_id"] == "A"


# --- eval_generator_handler ---------------------------------------------


class _EvalGeneratorGatewayClient:
    """Echoes each given production_failures entry back as a well-formed
    case -- self-consistent regardless of what the fixture's card_ids
    happen to be, so this test never depends on brittle exact UUIDs."""

    def __init__(self) -> None:
        self._inner = FakeGatewayClient()
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_EvalGeneratorGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if "Eval Generator" not in kw["system_prompt"]:
            return self._inner.complete(**kw)
        payload = json.loads(kw["user_content"])
        cases = [
            {
                "source_card_id": failure["card_id"],
                "input_summary": f"Real production failure for card {failure['card_id']}"[:400],
                "expected_verdict": failure["rejection_code"] or "unspecified",
                "rationale": "Derived directly from a real rejected_all decision."[:300],
            }
            for failure in payload["production_failures"]
        ]
        output = {
            "cases": cases,
            "rationale": (
                "These failures share a common rejection pattern worth regression-testing."
            ),
        }
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


def test_eval_generator_no_production_failures_completes_cleanly(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "eval-generator")
    _run(db, task_id, "eval-generator")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "no_production_failures"
    assert ref["generated"] == []


def test_eval_generator_generates_a_case_batch_from_real_failures(clients, monkeypatch):
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: _EvalGeneratorGatewayClient())
    options = [_option("A"), _option("B")]
    _seed_decided_card(
        clients,
        kind="content.reply",
        produced_by_function=116,
        options=options,
        recommended_option_id="A",
        outcome="rejected_all",
        rejection_code="claim_unsupported",
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "eval-generator")
    _run(db, task_id, "eval-generator")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "generated"
    assert len(ref["generated"]) == 1
    assert ref["generated"][0]["target_function_id"] == 116
    assert ref["generated"][0]["case_count"] == 1

    card_row = clients._option_cards[ref["generated"][0]["card_id"]]
    card = card_row["card"]
    assert card["kind"] == "system.prompt_change"
    assert card["recommended_option_id"] == "B"
    assert {o["option_id"] for o in card["options"]} == {"A", "B", "C"}
