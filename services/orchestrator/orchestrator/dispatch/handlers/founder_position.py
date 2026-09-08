from __future__ import annotations

import json
from typing import Any

from options_inbox.cards import build_card
from telemetry_lib import set_span_attribute

from orchestrator.dispatch_errors import DispatchError
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core
from ..completion import _complete_and_meter
from ..core import (
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
)
from ..lineage import resolve_lineage_result
from .expertise_corpus import EXPERTISE_ATOM_BATCH_SIGNAL_TYPE
from .source_lifecycle import LIFECYCLE_SIGNAL_LOOKBACK
from .voice_model import _latest_voice_profile

# ---------------------------------------------------------------------
# Fn 115 -- Position Proposer (Appendix D PR 9)
# ---------------------------------------------------------------------
#
# "Fn 43 REWIRE" (the other half of PR 9's own name) -- SCOPE CUT,
# DOCUMENTED, found while implementing this section, not assumed going
# in. _build_ghostwrite_payload (Fn 43's own payload builder, above) has
# UNCONDITIONALLY raised DraftNotAttempted("no_executive_configured", ...)
# since it was written: function 43's schema requires `executive_name`,
# and nothing in this repository configures one anywhere -- Pieter's own
# standing direction (1 Sep 2026) is that no executive is to be named
# yet. That gate is unrelated to whether a position has been chosen; it
# is a separate, still-open decision this session has no authority to
# reverse.
#
# _draft_social_post_handler (Fn 43's caller) calls build_payload(
# ancestor_ref) BEFORE opening a Vault client at all, deliberately ("a
# function that cannot honestly be called this week costs nothing and
# leaves no half-open campaign or running agent_run behind it" -- see
# that handler's own docstring) -- so a real "does a chosen position
# exist" lookup cannot be added inside _build_ghostwrite_payload without
# restructuring that shared call shape, which five OTHER drafting
# handlers also use. That is exactly the kind of shared-mechanism change
# this repo's own hard rules say needs auditing across every call site,
# not a one-line addition -- out of scope here. So this PR builds Fn 115
# fully (a real content.founder_position card, real corpus/voice
# grounding) and leaves the actual re-wiring of Fn 43's payload builder --
# reading the chosen position once one exists, once executive_name is
# also configured -- as the documented next step, rather than bolting an
# extra Vault round-trip onto a function whose entire body still runs
# before Vault access exists today.

FUNCTION_ID_115 = "115-position-proposer"


def propose_founder_position_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 115. Input is this week's research brief (same tuesday-qa-
    research-brief ancestor wednesday-draft-ghostwrite already reads),
    the corpus (Fn 113's atoms) and the voice profile (Fn 114's latest
    published version, if any). Up to 3 positions differing on a
    declared distinctness_axis -- never three phrasings of one stance,
    per prompt.md."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(
            "propose-founder-position: no research-brief ancestor carries a result_ref"
        )
    _brief_task, brief_ref = lineage
    pillar = brief_ref.get("pillar") or "this week's brief"
    proof_points = brief_ref.get("proof_points") or []

    with clients.build_vault_client() as vault, clients.build_gateway_client() as gateway:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_115
        )
        atoms: list[dict[str, Any]] = []
        for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
            if row.get("signal_type") == EXPERTISE_ATOM_BATCH_SIGNAL_TYPE:
                atoms.extend((row.get("payload") or {}).get("atoms") or [])
        voice_profile = _latest_voice_profile(vault)

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("position-proposer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_115,
            status="running",
            input_payload={
                "pillar": pillar,
                "atom_count": len(atoms),
                "has_voice_profile": voice_profile is not None,
            },
        )
        payload = {
            "topic": pillar,
            "proof_points": proof_points,
            "corpus_atoms": atoms[:100],
            "voice_profile": voice_profile,
        }
        _validate_function_input(FUNCTION_ID_115, payload)

        with emit_task_span(
            "propose-founder-position",
            function_id=FUNCTION_ID_115,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            response, cost = _complete_and_meter(
                gateway,
                vault,
                model="claude-sonnet",
                system_prompt=_read_prompt(FUNCTION_ID_115),
                user_content=json.dumps(payload),
                agent_run_id=agent_run["id"],
                max_tokens=4096,
            )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_115, output)

        if len(output["positions"]) < 2:
            vault.update_agent_run(
                agent_run["id"],
                status="failed",
                output_payload=output,
                completed_at=_now_iso(),
            )
            db.set_result_ref(
                task_id,
                {
                    "status": "insufficient_positions",
                    "position_count": len(output["positions"]),
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
            return

        atoms_by_id = {atom["atom_id"]: atom for atom in atoms if atom.get("atom_id")}
        letters = ["A", "B", "C"]
        options = []
        for index, position in enumerate(output["positions"]):
            cited_atom_ids = position.get("evidence_atom_ids") or []
            cited_atoms = [atoms_by_id[aid] for aid in cited_atom_ids if aid in atoms_by_id]
            if cited_atoms:
                evidence_refs = [
                    {
                        "source_type": "vault_asset",
                        "ref": f"corpus-atom://{atom['atom_id']}",
                        "quote": atom.get("text", "")[:300],
                        "authority": "secondary",
                    }
                    for atom in cited_atoms
                ]
            else:
                evidence_refs = [
                    {
                        "source_type": "vault_asset",
                        "ref": f"vault://agent-run/{agent_run['id']}",
                        "authority": "primary",
                    }
                ]
            label = (
                "New stance — you have not said this before"
                if position["novel_stance"]
                else "Stance"
            )
            summary = f"{label}: {position['stance']}"[:400]
            options.append(
                {
                    "option_id": letters[index],
                    "label": label[:60],
                    "summary": summary,
                    "payload_ref": f"vault://agent-run/{agent_run['id']}",
                    "evidence_refs": evidence_refs,
                    "predicted_outcome": position["predicted_reaction"],
                    "risks": [position["risk"]],
                    "distinctness_axis": position["distinctness_axis"],
                }
            )

        recommended_index = output["recommended"]
        if not 0 <= recommended_index < len(options):
            recommended_index = 0
        recommended_letter = options[recommended_index]["option_id"]

        card = build_card(
            kind="content.founder_position",
            level=1,  # functions/115-position-proposer/prompt.md's own autonomy_level
            title=f"Founder position: {pillar}"[:120],
            decision_question="Which position should this week's founder piece take?",
            options=options,
            recommended=recommended_letter,
            evidence_refs=[
                {
                    "source_type": "vault_asset",
                    "ref": f"vault://agent-run/{agent_run['id']}",
                    "authority": "primary",
                }
            ],
            produced_by={"function_id": 115, "prompt_version": "0.1.0"},
            register_rows=["H2"],
            rationale=output.get("rationale", ""),
            novel_stance=all(p["novel_stance"] for p in output["positions"]),
            lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
        )
        created = vault.create_option_card(
            {
                "card_id": card["card_id"],
                "kind": card["kind"],
                "autonomy_level": card["autonomy_level"],
                "risk_tier": card["risk_tier"],
                "agent_run_id": agent_run["id"],
                "produced_by_function": 115,
                "card": card,
                "created_at": card["created_at"],
                "expires_at": card["expires_at"],
            }
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "status": "proposed",
            "card_id": created["card_id"],
            "position_count": len(options),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


