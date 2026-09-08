from __future__ import annotations

import json
import logging
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core
from ..completion import _complete_and_meter
from ..core import (
    FUNCTION_ID_26,
    FUNCTION_ID_41,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
    logger,
)
from ..lineage import resolve_lineage_result

BRIEF_VERTICALS = [
    "logistics & distribution",
    "mining & industrial",
    "beverage/FMCG",
    "construction",
    "financial services",
]


def _monday_plan(task_id: str, db: Any) -> dict[str, Any]:
    """The whole plan-content-monday result_ref, not just its pillar --
    function 41 needs the evidence that chose the pillar, not only the
    name of it."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(
            f"{task_id}: no ancestor task carries a result_ref for this week's plan"
        )
    _ancestor_task, ancestor_ref = lineage
    return ancestor_ref


def _monday_plan_pillar(task_id: str, db: Any) -> str:
    """Every S11 drafting handler needs this week's pillar. Walks straight
    to the immediate plan-content-monday ancestor via resolve_lineage_result
    (one hop for draft-research-brief/draft-client-advocacy-harvest, since
    plan-content-monday now always carries a result_ref -- see
    plan_content_monday_handler above)."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(
            f"{task_id}: no ancestor task carries a result_ref for this week's pillar"
        )
    _ancestor_task, ancestor_ref = lineage
    pillar = ancestor_ref.get("pillar")
    if not pillar:
        raise DispatchError(f"{task_id}: nearest ancestor result_ref carries no pillar")
    return pillar

def draft_research_brief_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Function 41. Feeds every Wednesday drafting function -- NOT itself
    reviewed by Thursday's QA gate (mirrors ingest-signals' own position
    relative to daily-signal-loop's QA: an upstream research artifact, not
    a publish-bound draft)."""
    plan = _monday_plan(task_id, db)
    pillar = plan.get("pillar")
    if not pillar:
        raise DispatchError("draft-research-brief: monday plan carries no pillar")
    top_signals = plan.get("top_signals") or []
    vertical = BRIEF_VERTICALS[
        int(plan.get("week_number") or 0) % len(BRIEF_VERTICALS)
    ]

    # F-BRIEF-WITHOUT-EVIDENCE: this handler used to send `{"pillar": ...}`
    # and nothing else, while schema.json requires `signal_summary` -- the
    # field whose own description says a brief "must never invent evidence
    # the signal does not supply". A cited brief was being requested with
    # no sources, and the five Wednesday drafting functions all build on
    # it. The week's actual scored signals now go in, attributed.
    if top_signals:
        signal_summary = "\n".join(
            f"- [{item.get('confidence', '?')}] {item.get('headline', '')} — "
            f"{item.get('so_what', '')} (source: {item.get('source_url', '')})"
            for item in top_signals
        )
    else:
        # Said plainly rather than left blank: the prompt's own rules turn
        # an absence of evidence into a low-confidence brief, which is the
        # honest output for a week the scan found nothing in.
        signal_summary = (
            f"No scored market signals were recorded for the {pillar} pillar in the "
            "last 7 days. Write from Canvas's own positioning only, cite nothing that "
            "is not supplied here, and say plainly that this week produced no new "
            "market evidence."
        )

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_41
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("research-brief-writer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_41,
            status="running",
            input_payload={
                "pillar": pillar,
                "vertical": vertical,
                "vertical_source": "rotation-placeholder",
                "pillar_source": plan.get("pillar_source"),
                "signal_count": len(top_signals),
            },
        )

        system_prompt = _read_prompt("41-research-brief-writer")
        payload = {"pillar": pillar, "vertical": vertical, "signal_summary": signal_summary}
        _validate_function_input(FUNCTION_ID_41, payload)
        user_content = json.dumps(payload)

        with emit_task_span(
            "draft-research-brief",
            function_id=FUNCTION_ID_41,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with clients.build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                    content_class="public_source_content",
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        # F-WEEKLY-OUTPUT-UNVALIDATED: the daily loop validates what its
        # functions return; the weekly loop did not, so this brief -- the
        # artifact every Wednesday draft is built from -- could be any
        # shape at all and still be written to the Vault.
        core._validate_function_output(FUNCTION_ID_41, output)
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )
        brief = vault.create_brief(
            title=f"Research Brief — {pillar}",
            body_text=json.dumps(output.get("brief", output)),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_41,
        )

    brief_body = output.get("brief", {})
    proof_points = brief_body.get("proof_points") or []
    if not proof_points:
        # Not an error: function 41's own schema says this array is "empty
        # when the signal supplies no citable evidence -- proof over
        # platitude means an unsupported claim is never fabricated to fill
        # this array". An empty week is the honest outcome of a week with
        # no evidence, and saying so here is what lets a reader tell that
        # apart from a brief nobody checked.
        log_event(
            logger,
            logging.WARNING,
            "research_brief_without_proof_points",
            pillar=pillar,
            signal_count=len(top_signals),
        )

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "pillar": brief_body.get("pillar", pillar),
            "vertical": brief_body.get("vertical"),
            "audience_note": output.get("audience_note"),
            # F-PROOF-POINTS-DROPPED: function 41 PRODUCES structured
            # {claim, source} proof points -- its output schema requires
            # the array -- and the drafting handoff flattened the whole
            # brief to a JSON string, so five consumers whose own schemas
            # require `proof_points` or `proof_point` had to re-infer them
            # from prose. Carried structurally here so the drafting stage
            # can be given what it actually asks for.
            "proof_points": proof_points,
            "proof_point_count": len(proof_points),
            "signal_count": len(top_signals),
            "pillar_source": plan.get("pillar_source"),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# The brief-shaped fields draft_research_brief_handler writes onto its own
# result_ref above, which every Wednesday drafting handler needs and which
# any pass-through task standing between the brief and those drafts must
# therefore carry forward.
#
# F-BRIEF-FIELDS-DROPPED-BY-QA: inserting tuesday-qa-research-brief between
# the brief and the six drafts (the process-3 review gate) silently starved
# them. resolve_lineage_result stops at the FIRST ancestor carrying any
# non-null result_ref, so the walk began stopping at the QA task, whose
# result_ref forwarded `brief_id` and `vault_asset_id` but none of these.
# Every draft's `pillar` became None -- which draft_content_repurpose_
# handler's own "source ancestor result_ref carries no pillar" guard would
# then have raised on, one hop further down. Not caught by the loop tests:
# they assert graph shape, and no test walks a brief through a review into
# a draft. Adding a node to a graph whose edges carry untyped dicts is
# exactly the failure mode result_ref's shapelessness invites.
BRIEF_CARRIED_KEYS = (
    "pillar",
    "vertical",
    "audience_note",
    "proof_points",
    "proof_point_count",
    "signal_count",
    "pillar_source",
    # `campaign` does not come from the brief -- it is derived at drafting
    # from the pillar -- but it travels the same hops and was dropped at
    # the same gate, so it belongs in the same list.
    #
    # F-CAMPAIGN-DROPPED-BY-QA: it was absent here, so the QA gate carried
    # `pillar` forward and left `campaign` behind. Two consequences, one
    # per downstream stage. The approval card lost the tag from its title
    # and its whole "Attribution: every link carries utm_campaign=..."
    # line, and the publish step had no slug to register -- which is the
    # exact join key process 8's measurement depends on, so every ingested
    # metric row would have quarantined as unmatched even after the map
    # got a writer.
    #
    # Not caught by the process-6 tests because they constructed the
    # gate's result_ref by hand rather than producing it through a real
    # draft, so the field was present in the fixture and absent in life.
    # test_campaign_survives_the_qa_gate walks the real path instead.
    "campaign",
)


def _carried_brief_fields(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Copies whatever BRIEF_CARRIED_KEYS the ancestor actually has, so a
    pass-through task neither drops them nor invents nulls for a lineage
    (the daily loop's own qa-review) that never had a brief to begin
    with."""
    return {key: ancestor_ref[key] for key in BRIEF_CARRIED_KEYS if key in ancestor_ref}

def draft_client_advocacy_harvest_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Function 26. No consent-record fixture is wired into this
    environment yet, so this reliably returns naming_decision =
    blocked-no-consent every real run today -- correct, safe, default-deny
    behaviour per the function's own prompt, not a bug. A real consent
    register integration is a separate follow-up, not required for
    Thursday's QA gate to function (this task is not one of its 6
    dependencies either -- see qa_review_brand_steward_handler)."""
    pillar = _monday_plan_pillar(task_id, db)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_26
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("client-advocacy-harvester", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_26,
            status="running",
            input_payload={"pillar": pillar, "consent_record": None},
        )

        system_prompt = _read_prompt("26-client-advocacy-harvester")
        user_content = json.dumps({"pillar": pillar, "consent_record": None})

        with emit_task_span(
            "draft-client-advocacy-harvest",
            function_id=FUNCTION_ID_26,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with clients.build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "naming_decision": output.get("naming_decision"),
            "consent_status": output.get("consent_status"),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

