"""expertise-harvest-loop.yaml -- Appendix D PR 8 (Fn 113 Expertise Corpus
Miner, Fn 114 Executive Voice Model).

Covers the documented scope cut directly: dispatch.py mines docs/
positioning.md only (real, already-committed, already-public) rather than
Fireflies/proposals/project-docs/LinkedIn history -- see dispatch.py's own
module-section docstring above FUNCTION_ID_113 for why.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
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


def _atom(atom_id: str, text: str, *, atom_type: str = "opinion") -> dict[str, Any]:
    return {
        "atom_id": atom_id,
        "type": atom_type,
        "text": text,
        "speaker": "pieter",
        "source": {
            "source_type": "web_source",
            "ref": "docs/positioning.md",
            "authority": "primary",
        },
        "reuse_potential": 0.8,
        "confidentiality": "public_ok",
    }


class _CorpusMinerGatewayClient:
    def __init__(self, *, atoms: list[dict[str, Any]]) -> None:
        self._inner = FakeGatewayClient()
        self._atoms = atoms
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_CorpusMinerGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if "Expertise Corpus Miner" not in kw["system_prompt"]:
            return self._inner.complete(**kw)
        output = {
            "atoms": self._atoms,
            "delta": {
                "new": len(self._atoms),
                "updated": 0,
                "retired": 0,
                "sources_scanned": 1,
            },
        }
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


class _VoiceModelGatewayClient:
    def __init__(self, *, drift_score: float, changed_traits: list[str] | None = None) -> None:
        self._inner = FakeGatewayClient()
        self._drift_score = drift_score
        self._changed_traits = changed_traits or []
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_VoiceModelGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if "Executive Voice Model" not in kw["system_prompt"]:
            return self._inner.complete(**kw)
        output = {
            "leader": "pieter",
            "profile_version": "v1",
            "voice_traits": [
                {
                    "trait": "blunt about weak arguments",
                    "description": "Calls out a weak argument directly rather than softening it.",
                    "exemplars": ["That's not a moat.", "That number needs a source.", "No."],
                }
            ],
            "positions": [
                {
                    "position_id": "p1",
                    "statement": "CoEaaS beats a hire on unit economics, not just speed.",
                    "status": "observed",
                    "evidence_refs": [
                        {
                            "source_type": "web_source",
                            "ref": "docs/positioning.md",
                            "authority": "primary",
                        }
                    ],
                }
            ],
            "drift": {
                "score": self._drift_score,
                "exceeds_threshold": self._drift_score > 0.15,
                "changed_traits": self._changed_traits,
            },
        }
        return {
            "id": "fake",
            "model": kw["model"],
            "content": json.dumps(output),
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "agent_run_id": kw["agent_run_id"],
        }


# --- expertise_corpus_mine_handler --------------------------------------


def test_corpus_mine_source_unavailable_completes_cleanly(clients, monkeypatch):
    monkeypatch.setattr(dispatch, "_positioning_md_path", lambda: Path("/nope/nope.md"))
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "expertise-corpus-mine")

    _run(db, task_id, "expertise-corpus-mine")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "source_unavailable"


def test_corpus_mine_extracts_new_atoms(clients, monkeypatch):
    atoms = [_atom("a1", "CoEaaS is an embedded team on subscription, not a hire.")]
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CorpusMinerGatewayClient(atoms=atoms)
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "expertise-corpus-mine")
    _run(db, task_id, "expertise-corpus-mine")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "mined"
    assert ref["new_atom_count"] == 1

    signal = clients.get_signal(ref["vault_signal_id"])
    assert signal["signal_type"] == dispatch.EXPERTISE_ATOM_BATCH_SIGNAL_TYPE
    assert signal["payload"]["delta"]["new"] == 1


def test_corpus_mine_dedupes_against_existing_atoms(clients, monkeypatch):
    text = "CoEaaS is an embedded team on subscription, not a hire."
    campaign_id = clients.get_or_create_campaign("seed-run", function_id=dispatch.FUNCTION_ID_113)
    clients.create_signal(
        source="test-fixture",
        signal_type=dispatch.EXPERTISE_ATOM_BATCH_SIGNAL_TYPE,
        payload={"atoms": [_atom("a0", text)]},
        campaign_id=campaign_id,
        function_id=dispatch.FUNCTION_ID_113,
    )
    # Same meaning, different case/punctuation -- the normalize-based dedupe
    # proxy must still catch it.
    atoms = [_atom("a1", text.upper().rstrip(".") + "!!!")]
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CorpusMinerGatewayClient(atoms=atoms)
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "expertise-corpus-mine")
    _run(db, task_id, "expertise-corpus-mine")

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "mined"
    assert ref["new_atom_count"] == 0
    assert "vault_signal_id" not in ref


# --- executive_voice_model_handler --------------------------------------


def test_voice_model_publishes_a_profile_when_drift_is_low(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _VoiceModelGatewayClient(drift_score=0.05)
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "executive-voice-model")

    _run(db, task_id, "executive-voice-model")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "profile_updated"
    assert ref["profile_version"] == "v1"

    signal = clients.get_signal(ref["vault_signal_id"])
    assert signal["signal_type"] == dispatch.VOICE_PROFILE_SIGNAL_TYPE
    assert signal["payload"]["leader"] == "pieter"


def test_voice_model_blocks_publication_on_high_drift(clients, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _VoiceModelGatewayClient(drift_score=0.42, changed_traits=["how he opens"]),
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "executive-voice-model")

    _run(db, task_id, "executive-voice-model")

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "drift_blocked"
    assert ref["drift_score"] == 0.42

    card_row = clients._option_cards[ref["card_id"]]
    assert card_row["card"]["kind"] == "system.prompt_change"
    assert card_row["card"]["recommended_option_id"] == "B"

    # No profile signal was published under high drift.
    assert not any(
        row["signal_type"] == dispatch.VOICE_PROFILE_SIGNAL_TYPE
        for row in clients._signals.values()
    )
