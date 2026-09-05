"""weekly-content-loop.yaml's tuesday-propose-founder-position task --
Appendix D PR 9 (Fn 115 Position Proposer).

Mirrors test_dispatch_options_approval.py's own seeding pattern: a
completed research-brief-shaped ancestor task carrying the pillar/
proof_points result_ref propose_founder_position_handler reads via
resolve_lineage_result, exactly as wednesday-draft-ghostwrite's own
_build_ghostwrite_payload does.
"""

from __future__ import annotations

import json
import uuid
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


def _seed_research_brief_ancestor(
    db: FakeTaskDB,
    *,
    pillar: str = "Consolidation at scale",
    proof_points: list[dict[str, str]] | None = None,
) -> str:
    brief_id = str(uuid.uuid4())
    db.seed(brief_id, "qa-review")
    db.set_result_ref(
        brief_id,
        {
            "pillar": pillar,
            "proof_points": proof_points
            if proof_points is not None
            else [{"claim": "Direct Lake handles 4TB in production.", "source": "positioning.md"}],
        },
    )
    db.transition(brief_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)
    return brief_id


def _position(
    *, stance: str, axis: str, novel: bool = False, evidence_atom_ids: list[str] | None = None
) -> dict[str, Any]:
    return {
        "stance": stance,
        "argument": f"Argument for: {stance}",
        "distinctness_axis": axis,
        "predicted_reaction": "CFOs nod, IT leads push back on the framing.",
        "risk": "Invites a Microsoft partner to disagree publicly.",
        "novel_stance": novel,
        "evidence_atom_ids": evidence_atom_ids or [],
    }


class _PositionProposerGatewayClient:
    def __init__(self, *, positions: list[dict[str, Any]], recommended: int = 0) -> None:
        self._inner = FakeGatewayClient()
        self._positions = positions
        self._recommended = recommended
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_PositionProposerGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if "Position Proposer" not in kw["system_prompt"]:
            return self._inner.complete(**kw)
        output = {
            "positions": self._positions,
            "recommended": self._recommended,
            "rationale": "This stance fits this week's audience and proof points.",
        }
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


def test_propose_founder_position_builds_a_three_option_card(clients, monkeypatch):
    positions = [
        _position(
            stance="CoEaaS beats a hire on unit economics.", axis="economics vs technical"
        ),
        _position(
            stance="The moat is the semantic layer, not the team.",
            axis="contrarian vs consensus",
        ),
        _position(
            stance="CFOs should own this decision, not IT.",
            axis="CFO-facing vs IT-facing",
            novel=True,
        ),
    ]
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _PositionProposerGatewayClient(positions=positions, recommended=1),
    )
    db = FakeTaskDB()
    brief_id = _seed_research_brief_ancestor(db)
    task_id = str(uuid.uuid4())
    db.seed(task_id, "propose-founder-position", depends_on=[brief_id])

    _run(db, task_id, "propose-founder-position")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "proposed"
    assert ref["position_count"] == 3

    card_row = clients._option_cards[ref["card_id"]]
    card = card_row["card"]
    assert card["kind"] == "content.founder_position"
    assert card["autonomy_level"] == 1
    assert len(card["options"]) == 3
    assert card["recommended_option_id"] == "B"
    axes = {o["distinctness_axis"] for o in card["options"]}
    assert len(axes) == 3
    novel_option = next(o for o in card["options"] if "New stance" in o["label"])
    assert novel_option["option_id"] == "C"


def test_propose_founder_position_cites_real_corpus_atoms(clients, monkeypatch):
    campaign_id = clients.get_or_create_campaign("seed-run", function_id=dispatch.FUNCTION_ID_113)
    clients.create_signal(
        source="test-fixture",
        signal_type=dispatch.EXPERTISE_ATOM_BATCH_SIGNAL_TYPE,
        payload={
            "atoms": [
                {
                    "atom_id": "atom-1",
                    "type": "opinion",
                    "text": "An embedded team beats a hire on unit economics.",
                    "speaker": "pieter",
                    "source": {
                        "source_type": "web_source",
                        "ref": "docs/positioning.md",
                        "authority": "primary",
                    },
                    "reuse_potential": 0.9,
                    "confidentiality": "public_ok",
                }
            ]
        },
        campaign_id=campaign_id,
        function_id=dispatch.FUNCTION_ID_113,
    )
    positions = [
        _position(
            stance="CoEaaS beats a hire on unit economics.",
            axis="economics vs technical",
            evidence_atom_ids=["atom-1"],
        ),
        _position(
            stance="The moat is the semantic layer, not the team.",
            axis="contrarian vs consensus",
        ),
    ]
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _PositionProposerGatewayClient(positions=positions),
    )
    db = FakeTaskDB()
    brief_id = _seed_research_brief_ancestor(db)
    task_id = str(uuid.uuid4())
    db.seed(task_id, "propose-founder-position", depends_on=[brief_id])

    _run(db, task_id, "propose-founder-position")

    ref = db.get_result_ref(task_id)
    card = clients._option_cards[ref["card_id"]]["card"]
    option_a = next(o for o in card["options"] if o["option_id"] == "A")
    assert option_a["evidence_refs"][0]["ref"] == "corpus-atom://atom-1"


def test_propose_founder_position_dead_letters_on_a_single_position(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _PositionProposerGatewayClient(
            positions=[_position(stance="Only one stance.", axis="n/a")]
        ),
    )
    db = FakeTaskDB()
    brief_id = _seed_research_brief_ancestor(db)
    task_id = str(uuid.uuid4())
    db.seed(task_id, "propose-founder-position", depends_on=[brief_id])

    _run(db, task_id, "propose-founder-position")

    assert db.get_task(task_id)["state"] == "failed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "insufficient_positions"
    assert ref["position_count"] == 1
