"""source-lifecycle-loop.yaml -- Appendix D PR 5b (Fn 128, Source
Discovery & Lifecycle Manager).

Covers the documented scope cut directly: dispatch.py mines already-
probed alternates from functions/_shared/source-candidates.bootstrap.
yaml rather than performing live web research (see dispatch.py's own
module-section docstring above FUNCTION_ID_128), so these tests assert
the CARD MECHANISM -- dedupe, card build, dead-letter on a thin pool,
nightly yield, self-gating monthly retire with a mandatory replacement
-- against that real bootstrap data, never a synthetic fixture standing
in for it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import FakeGatewayClient, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope


class _SourceLifecycleGatewayClient:
    """Echoes back up to `max_candidates` of the candidate_pool entries
    dispatch.py actually sent it, each with a distinctness_axis/rationale
    a model would add -- self-consistent regardless of what the bootstrap
    file's live content happens to be today, so this test never depends
    on that file's exact rows."""

    def __init__(self, *, max_candidates: int = 3) -> None:
        self._inner = FakeGatewayClient()
        self._max_candidates = max_candidates
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_SourceLifecycleGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if "Source Discovery & Lifecycle Manager" not in kw["system_prompt"]:
            return self._inner.complete(**kw)

        payload = json.loads(kw["user_content"])
        pool = payload["candidate_pool"][: self._max_candidates]
        letters = ["A", "B", "C"]
        candidates = [
            {
                "option_id": letters[i],
                "url": item["url"],
                "domain": item["domain"],
                "distinctness_axis": f"axis-{i}-{item['domain']}"[:80],
                "rationale": f"{item['rationale']} (echoed by the fake)"[:400],
                "provisional": item["provisional"],
                "probe": item["probe"],
            }
            for i, item in enumerate(pool)
        ]
        output = {
            "card_kind": payload.get("card_kind", "source.promote"),
            "signal_class": payload["signal_class"],
            "candidates": candidates,
            "recommended_option_id": candidates[0]["option_id"] if candidates else None,
            "retiring_source_url": payload.get("retiring_source_url"),
        }
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _run(db: FakeTaskDB, task_id: str, task_type: str) -> None:
    dispatch.DISPATCH_TABLE[task_type](task_id, _envelope(task_id, task_type), db)


# --- daily discovery --------------------------------------------------


def test_source_discovery_builds_a_promote_card(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _SourceLifecycleGatewayClient()
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    task_type = "source-discovery-tenders-events-partners"
    db.seed(task_id, task_type)

    _run(db, task_id, task_type)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "proposed"
    assert ref["signal_class"] == "tenders-events-partners"

    card_row = clients._option_cards[ref["card_id"]]
    card = card_row["card"]
    assert card["kind"] == "source.promote"
    assert card["autonomy_level"] == 1
    assert 2 <= len(card["options"]) <= 3
    assert card["recommended_option_id"] in {o["option_id"] for o in card["options"]}
    for option in card["options"]:
        assert option["evidence_refs"][0]["ref"].startswith("vault://signal/")


def test_source_discovery_reputation_community_has_no_candidates(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    task_type = "source-discovery-reputation-community"
    db.seed(task_id, task_type)

    _run(db, task_id, task_type)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "no_candidates"
    assert ref["signal_class"] == "reputation-community"


def test_source_discovery_dead_letters_when_fewer_than_two_survive(clients, monkeypatch):
    # max_candidates=1 forces the model to echo exactly one candidate --
    # contracts/option-card.schema.json needs >=2 options, so this must
    # dead-letter rather than crash inside build_card.
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _SourceLifecycleGatewayClient(max_candidates=1),
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    task_type = "source-discovery-tenders-events-partners"
    db.seed(task_id, task_type)

    _run(db, task_id, task_type)

    assert db.get_task(task_id)["state"] == "failed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "insufficient_candidates"
    assert ref["candidate_count"] == 1


# --- nightly yield ------------------------------------------------------


def test_source_yield_writes_one_row_per_live_source(clients):
    """FakeMCPClient's default probe_url fixture carries no status_code,
    so every source reads as unreachable here -- a deterministic,
    real-code-path assertion, not a claim about live reachability."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "source-yield-nightly")

    _run(db, task_id, "source-yield-nightly")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    # One row per (profile, url) pair -- not per distinct URL, since the
    # same URL legitimately appears under more than one profile (e.g.
    # learn.microsoft.com under both market-intelligence and
    # fabric-ecosystem) and each occurrence gets its own yield row.
    expected_row_count = sum(
        len(profile.get("urls") or [])
        for profile in dispatch._load_scan_profiles().get("profiles", [])
    )
    assert ref["source_count"] == expected_row_count
    assert ref["unreachable_count"] == expected_row_count

    signal = clients.get_signal(ref["vault_signal_id"])
    assert signal["signal_type"] == dispatch.YIELD_SIGNAL_TYPE
    assert len(signal["payload"]["rows"]) == expected_row_count


# --- monthly retire -------------------------------------------------------

RETIRING_URL = "https://www.sars.gov.za/feed/?post_type=latest_news"
RETIRING_PROFILE_ID = "vertical-financial-services"


def _seed_yield_floor_breach(vault: Any, *, url: str, campaign_id: str) -> None:
    for _ in range(dispatch.YIELD_FLOOR_CONSECUTIVE_FAILURES):
        vault.create_signal(
            source="test-fixture",
            signal_type=dispatch.YIELD_SIGNAL_TYPE,
            payload={"rows": [{"url": url, "reachable": False}]},
            campaign_id=campaign_id,
            function_id=dispatch.FUNCTION_ID_128,
        )


def test_source_retire_first_pass_with_no_breach_retires_nothing(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "source-retire-monthly")

    _run(db, task_id, "source-retire-monthly")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "retire_pass_complete"
    assert ref["retired_count"] == 0


def test_source_retire_is_not_due_again_immediately_after(clients):
    db = FakeTaskDB()
    first_id = str(uuid.uuid4())
    db.seed(first_id, "source-retire-monthly")
    _run(db, first_id, "source-retire-monthly")

    second_id = str(uuid.uuid4())
    db.seed(second_id, "source-retire-monthly")
    _run(db, second_id, "source-retire-monthly")

    ref = db.get_result_ref(second_id)
    assert ref["status"] == "not_due"
    assert ref["next_due_in_days"] > 0


def test_source_retire_emits_a_card_with_a_replacement_on_floor_breach(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _SourceLifecycleGatewayClient()
    )
    campaign_id = clients.get_or_create_campaign("seed-run", function_id=dispatch.FUNCTION_ID_128)
    _seed_yield_floor_breach(clients, url=RETIRING_URL, campaign_id=campaign_id)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "source-retire-monthly")

    _run(db, task_id, "source-retire-monthly")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "retire_pass_complete"
    assert ref["retired_count"] == 1
    assert ref["retired"][0]["url"] == RETIRING_URL

    card_row = clients._option_cards[ref["retired"][0]["card_id"]]
    card = card_row["card"]
    assert card["kind"] == "source.retire"
    assert 2 <= len(card["options"]) <= 3

    retired_signals = [
        row
        for row in clients._signals.values()
        if row["signal_type"] == dispatch.RETIRED_SOURCE_SIGNAL_TYPE
    ]
    assert len(retired_signals) == 1
    assert retired_signals[0]["payload"]["url"] == RETIRING_URL
    assert retired_signals[0]["payload"]["profile_id"] == RETIRING_PROFILE_ID
