from __future__ import annotations

import json
from datetime import datetime, timezone
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
from . import expertise_corpus
from .source_lifecycle import LIFECYCLE_SIGNAL_LOOKBACK
from .voice_model import _latest_voice_profile

# ---------------------------------------------------------------------
# Fn 122 -- Foundation Drafter (Appendix D PR 12)
# ---------------------------------------------------------------------
#
# SCOPE CUT, see functions/122-foundation-drafter/schema.json's own
# description for the full reasoning: ships three of prompt.md's five
# foundation artefacts -- brand and messaging constitution, metric
# definitions, approver map -- all groundable in docs/positioning.md,
# policies/autonomy-matrix.yaml and docs/permission-register.yaml, real
# and already committed. Declines the ICP list (needs real CRM/
# installed-base account data this repo does not have, the same gap
# Fn 120 documents, and a fabricated 25-40-account list would invent
# real prospective-client identities) and quarterly objectives (needs
# Fn 38's portfolio scenarios; Fn 38 does not exist anywhere in this
# repo). Adds a new option-card kind, foundation.approver_map, to
# contracts/option-card.schema.json -- additive, and that file is not
# one of the frozen-v1 contracts (see contracts/.frozen-v1.sha256).
#
# "Once, then quarterly" per prompt.md: self-gates per artefact via
# FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE signals, the same idiom
# decision_quality_level_review_monthly_handler already established for
# a cadence this repo's loop schema has no native concept of -- the
# FIRST run for a given artefact always drafts it (no prior signal).

FUNCTION_ID_122 = "122-foundation-drafter"
FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE = "foundation_artefact_published"
FOUNDATION_REFIT_WINDOW_DAYS = 90
FOUNDATION_ARTEFACT_KINDS = {
    "brand_constitution": "foundation.brand_rule",
    "metric_definitions": "foundation.metric_definition",
    "approver_map": "foundation.approver_map",
}


def _foundation_artefacts_due(vault: VaultClientExt, now: datetime) -> list[str]:
    latest: dict[str, datetime] = {}
    for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
        if row.get("signal_type") != FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE:
            continue
        key = (row.get("payload") or {}).get("artefact_key")
        received_at = _parse_iso_timestamp(row.get("received_at"))
        if key and received_at and (key not in latest or received_at > latest[key]):
            latest[key] = received_at
    return [
        key
        for key in FOUNDATION_ARTEFACT_KINDS
        if key not in latest or (now - latest[key]).days >= FOUNDATION_REFIT_WINDOW_DAYS
    ]


def foundation_drafter_bootstrap_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    now = datetime.now(timezone.utc)
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_122
        )
        due = _foundation_artefacts_due(vault, now)
        if not due:
            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("foundation-drafter", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_122,
                status="succeeded",
                input_payload={},
                output_payload={"status": "not_due"},
            )
            db.set_result_ref(
                task_id,
                {"status": "not_due", "agent_run_id": agent_run["id"], "campaign_id": campaign_id},
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
            return

        path = expertise_corpus._positioning_md_path()
        positioning_text = path.read_text(encoding="utf-8") if path.is_file() else ""
        voice_profile = _latest_voice_profile(vault)
        permission_check = clients.load_permission_check()
        cleared = [
            name
            for name in permission_check.registered_names()
            if permission_check.check_clearance(name).allowed
        ]
        register_summary = (
            "Default-deny register (docs/permission-register.yaml): a client name may be "
            "used publicly only if its status is the literal string CLEARED; absence from "
            "the file blocks identically to an explicit UNCLEARED entry. Currently CLEARED: "
            f"{cleared or 'none'}."
        )

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("foundation-drafter", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_122,
            status="running",
            input_payload={"due_artefacts": due},
        )
        payload = {
            "due_artefacts": due,
            "positioning_text": positioning_text,
            "voice_profile": voice_profile,
            "permission_register_summary": register_summary,
        }
        _validate_function_input(FUNCTION_ID_122, payload)

        with clients.build_gateway_client() as gateway:
            with emit_task_span(
                "foundation-drafter-bootstrap",
                function_id=FUNCTION_ID_122,
                task_ref=task_id,
                model="claude-sonnet",
                run_id=str(envelope.campaign_id),
            ) as span:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=_read_prompt(FUNCTION_ID_122),
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    max_tokens=6144,
                )
                set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_122, output)

        letters = ["A", "B", "C"]
        created_cards = []
        for key in due:
            artefact = output["artefacts"][key]
            evidence_refs = [
                {
                    "source_type": "project_doc",
                    "ref": artefact["cited_doc"][:300],
                    "authority": "primary",
                }
            ]
            options = []
            label_to_option_id: dict[str, str] = {}
            for index, entry in enumerate(artefact["options"]):
                option_id = letters[index]
                label_to_option_id[entry["label"]] = option_id
                options.append(
                    {
                        "option_id": option_id,
                        "label": entry["label"][:60],
                        "summary": entry["argument"][:400],
                        "payload_ref": f"vault://agent-run/{agent_run['id']}#{key}-{option_id}",
                        "evidence_refs": evidence_refs,
                        "predicted_outcome": (
                            "Becomes the versioned foundation artefact of record once chosen."
                        ),
                        "risks": ["Superseded at the next quarterly refit."],
                        "distinctness_axis": (
                            "strictness / framing" if key == "brand_constitution" else "scope"
                        ),
                    }
                )
            recommended_id = label_to_option_id.get(
                artefact["recommended_option"], options[0]["option_id"]
            )
            card = build_card(
                kind=FOUNDATION_ARTEFACT_KINDS[key],
                level=1,
                title=artefact["title"][:120],
                decision_question=artefact["decision_question"][:300],
                options=options,
                recommended=recommended_id,
                evidence_refs=evidence_refs,
                produced_by={"function_id": 122, "prompt_version": "0.1.0"},
                register_rows=["H11", "H12"],
                rationale=artefact["rationale"][:600],
                lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
            )
            created = vault.create_option_card(
                {
                    "card_id": card["card_id"],
                    "kind": card["kind"],
                    "autonomy_level": card["autonomy_level"],
                    "risk_tier": card["risk_tier"],
                    "agent_run_id": agent_run["id"],
                    "produced_by_function": 122,
                    "card": card,
                    "created_at": card["created_at"],
                    "expires_at": card["expires_at"],
                }
            )
            vault.create_signal(
                source=f"function-{FUNCTION_ID_122}",
                signal_type=FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE,
                payload={"artefact_key": key, "card_id": created["card_id"]},
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_122,
            )
            created_cards.append({"artefact_key": key, "card_id": created["card_id"]})

        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {"status": "drafted", "cards": created_cards, "campaign_id": campaign_id},
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


