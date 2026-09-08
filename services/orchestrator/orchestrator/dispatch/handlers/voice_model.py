from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from options_inbox.cards import build_card
from telemetry_lib import set_span_attribute

from orchestrator.clients.vault_client_ext import VaultClientExt
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
from ..scan_shared import _parse_iso_timestamp
from .expertise_corpus import EXPERTISE_ATOM_BATCH_SIGNAL_TYPE
from .source_lifecycle import LIFECYCLE_SIGNAL_LOOKBACK

# ---------------------------------------------------------------------
# Fn 114 -- Executive Voice Model (Appendix D PR 8)
# ---------------------------------------------------------------------
#
# SCOPE CUT, DOCUMENTED, same reasoning as Fn 113's own module-section
# docstring above: linkedin_post_history/fireflies_transcript/email_
# thread are deferred. This PR builds the profile from what IS real and
# already available: Fn 113's own mined atoms, plus GET /decision-history
# (gate_decision_history -- an approved input type this function already
# lists in prompt.md, and real data since Appendix D PR 6/7 added that
# endpoint) -- the ratifier's own real choices over the trailing window.

FUNCTION_ID_114 = "114-executive-voice-model"
VOICE_PROFILE_SIGNAL_TYPE = "executive_voice_profile"
VOICE_MODEL_LEADER = "pieter"
VOICE_PROFILE_DECISION_WINDOW_DAYS = 30


def _latest_voice_profile(vault: VaultClientExt) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    latest_at: datetime | None = None
    for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
        if row.get("signal_type") != VOICE_PROFILE_SIGNAL_TYPE:
            continue
        received_at = _parse_iso_timestamp(row.get("received_at"))
        if received_at and (latest_at is None or received_at > latest_at):
            latest_at = received_at
            latest = row.get("payload")
    return latest


def executive_voice_model_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 114 (prompt.md task: weekly rebuild + drift gate). dispatch.py
    recomputes `drift.exceeds_threshold` from `drift.score` against
    manifest.yaml's own drift_threshold rather than trusting the model's
    self-assessed boolean -- the same defence-in-depth every other
    self-graded verdict in this file gets."""
    now = datetime.now(timezone.utc)
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_114
        )
        atoms: list[dict[str, Any]] = []
        for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
            if row.get("signal_type") == EXPERTISE_ATOM_BATCH_SIGNAL_TYPE:
                atoms.extend((row.get("payload") or {}).get("atoms") or [])

        since = (now - timedelta(days=VOICE_PROFILE_DECISION_WINDOW_DAYS)).isoformat()
        recent_decisions = vault.list_decision_history(since=since, limit=500)
        previous_profile = _latest_voice_profile(vault)

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("executive-voice-model", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_114,
            status="running",
            input_payload={
                "leader": VOICE_MODEL_LEADER,
                "atom_count": len(atoms),
                "recent_decision_count": len(recent_decisions),
                "has_previous_profile": previous_profile is not None,
            },
        )
        drift_threshold = 0.15  # functions/114-executive-voice-model/manifest.yaml's own value
        payload = {
            "leader": VOICE_MODEL_LEADER,
            "atoms": atoms,
            "recent_decisions": recent_decisions,
            "previous_profile": previous_profile,
            "drift_threshold": drift_threshold,
        }
        _validate_function_input(FUNCTION_ID_114, payload)

        with clients.build_gateway_client() as gateway:
            with emit_task_span(
                "executive-voice-model",
                function_id=FUNCTION_ID_114,
                task_ref=task_id,
                model="claude-sonnet",
                run_id=str(envelope.campaign_id),
            ) as span:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=_read_prompt(FUNCTION_ID_114),
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    max_tokens=6144,
                )
                set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_114, output)

        drift_score = float(output["drift"]["score"])
        exceeds_threshold = drift_score > drift_threshold

        if exceeds_threshold:
            evidence = (
                f"Fn 114's rebuilt voice profile for {VOICE_MODEL_LEADER} scored a drift of "
                f"{drift_score} against the previous version, above the {drift_threshold} "
                f"threshold in functions/114-executive-voice-model/manifest.yaml. Changed "
                f"traits: {', '.join(output['drift'].get('changed_traits') or []) or 'none named'}."
            )
            card = build_card(
                kind="system.prompt_change",
                level=0,  # overridden to non_negotiable/realtime by build_card itself
                title=f"Voice profile drift for {VOICE_MODEL_LEADER}"[:120],
                decision_question=(
                    "Publish this rebuilt voice profile despite the drift, or hold it?"
                ),
                options=[
                    {
                        "option_id": "A",
                        "label": "Publish anyway",
                        "summary": "Publish the rebuilt profile despite the drift score."[:400],
                        "payload_ref": f"vault://agent-run/{agent_run['id']}",
                        "evidence_refs": [
                            {
                                "source_type": "vault_asset",
                                "ref": f"vault://agent-run/{agent_run['id']}",
                                "quote": evidence[:300],
                                "authority": "primary",
                            }
                        ],
                        "predicted_outcome": "New profile version becomes what Fn 115/43 read.",
                        "risks": ["Voice drift may reflect a stale or unrepresentative corpus."],
                    },
                    {
                        "option_id": "B",
                        "label": "Hold the previous version",
                        "summary": "Keep the last published profile; re-attempt next week."[:400],
                        "payload_ref": f"vault://agent-run/{agent_run['id']}",
                        "evidence_refs": [
                            {
                                "source_type": "vault_asset",
                                "ref": f"vault://agent-run/{agent_run['id']}",
                                "quote": evidence[:300],
                                "authority": "primary",
                            }
                        ],
                        "predicted_outcome": "No change; Fn 115/43 keep reading the prior profile.",
                        "risks": [],
                    },
                ],
                recommended="B",
                evidence_refs=[
                    {
                        "source_type": "vault_asset",
                        "ref": f"vault://agent-run/{agent_run['id']}",
                        "authority": "primary",
                    }
                ],
                produced_by={"function_id": 114, "prompt_version": "0.1.0"},
                register_rows=["H2", "H20"],
                rationale=evidence,
                lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
            )
            created = vault.create_option_card(
                {
                    "card_id": card["card_id"],
                    "kind": card["kind"],
                    "autonomy_level": card["autonomy_level"],
                    "risk_tier": card["risk_tier"],
                    "agent_run_id": agent_run["id"],
                    "produced_by_function": 114,
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
                    "status": "drift_blocked",
                    "card_id": created["card_id"],
                    "drift_score": drift_score,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
            return

        signal = vault.create_signal(
            source=f"function-{FUNCTION_ID_114}",
            signal_type=VOICE_PROFILE_SIGNAL_TYPE,
            payload=output,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_114,
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "status": "profile_updated",
            "vault_signal_id": signal["id"],
            "profile_version": output["profile_version"],
            "drift_score": drift_score,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


