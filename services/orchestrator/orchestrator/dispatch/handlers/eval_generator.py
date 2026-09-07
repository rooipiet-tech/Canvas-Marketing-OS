from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
from .decision_quality import DECISION_HISTORY_WINDOW_DAYS, FUNCTION_ACTION_CLASS

# ---------------------------------------------------------------------
# Fn 127 -- Eval Generator (Appendix D W1 + PR 7)
# ---------------------------------------------------------------------
#
# SCOPE CUT, DOCUMENTED. prompt.md names four case sources in priority
# order. This PR wires SOURCE 1 ONLY -- production failures (rejected_all/
# expired_unresolved decisions, real GET /decision-history rows, never a
# fabricated example) -- because it is the only one directly derivable
# from data this repo can already read. Sources 2-4 (chosen-vs-rejected
# preference pairs, rubric-mutation synthesis, adversarial round-21
# cases) each need additional plumbing this PR does not add: preference
# pairs need the REJECTED option's own text, which GET /decision-history
# carries via each row's embedded `card.options[]` but this PR does not
# yet cross-reference; rubric expansion needs a per-function rule list
# this repo has no machine-readable form of (prompt.md files are prose);
# adversarial cases could reuse dispatch.py's own _strip_instruction_
# shaped_content patterns as a generator seed, a natural follow-up.

FUNCTION_ID_127 = "127-eval-generator"
PRODUCTION_FAILURE_OUTCOMES = frozenset({"rejected_all", "expired_unresolved"})
EVAL_CASE_BATCH_SIGNAL_TYPE = "eval_case_batch"


def _production_failures_for(function_id: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = []
    for row in rows:
        if int(row["produced_by_function"]) != function_id:
            continue
        if row.get("outcome") not in PRODUCTION_FAILURE_OUTCOMES:
            continue
        card = row.get("card") or {}
        failures.append(
            {
                "card_id": row["card_id"],
                "rejection_code": row.get("rejection_code"),
                "card_kind": row.get("kind"),
                "options": [
                    {"option_id": o.get("option_id"), "summary": o.get("summary")}
                    for o in (card.get("options") or [])
                ],
            }
        )
    return failures


def eval_generator_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 127 (prompt.md task step 1, source 1 only -- see module-section
    docstring above). One task per FUNCTION_ACTION_CLASS entry with at
    least one real production failure in the trailing window; a function
    with none completes cleanly with no card, same "honest empty, not a
    failure" philosophy as _make_source_discovery_handler's own
    no-candidates path."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=DECISION_HISTORY_WINDOW_DAYS)).isoformat()
    with clients.build_vault_client() as vault, clients.build_gateway_client() as gateway:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_127
        )
        rows = vault.list_decision_history(since=since, limit=2000)

        generated: list[dict[str, Any]] = []
        for target_function_id in sorted(FUNCTION_ACTION_CLASS):
            failures = _production_failures_for(target_function_id, rows)
            if not failures:
                continue

            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("eval-generator", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_127,
                status="running",
                input_payload={
                    "target_function_id": target_function_id,
                    "failure_count": len(failures),
                },
            )
            payload = {"target_function_id": target_function_id, "production_failures": failures}
            _validate_function_input(FUNCTION_ID_127, payload)

            with emit_task_span(
                "eval-generator",
                function_id=FUNCTION_ID_127,
                task_ref=task_id,
                model="claude-haiku",
                run_id=str(envelope.campaign_id),
            ) as span:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-haiku",
                    system_prompt=_read_prompt(FUNCTION_ID_127),
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    max_tokens=3072,
                )
                set_span_attribute(span, "cost", cost)

            output = _parse_json_content(response["content"])
            core._validate_function_output(FUNCTION_ID_127, output)

            failure_card_ids = {f["card_id"] for f in failures}
            for case in output["cases"]:
                if case["source_card_id"] not in failure_card_ids:
                    raise DispatchError(
                        f"eval-generator: model echoed a source_card_id not in this "
                        f"batch's production_failures: {case['source_card_id']!r}"
                    )

            batch = vault.create_signal(
                source=f"function-{FUNCTION_ID_127}",
                signal_type=EVAL_CASE_BATCH_SIGNAL_TYPE,
                payload={"target_function_id": target_function_id, "cases": output["cases"]},
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_127,
            )

            sample_size = min(20, len(output["cases"]))
            options = [
                {
                    "option_id": "A",
                    "label": "Activate full set",
                    "summary": f"Activate all {len(output['cases'])} generated case(s)."[:400],
                    "payload_ref": f"vault://signal/{batch['id']}",
                    "evidence_refs": [
                        {
                            "source_type": "vault_asset",
                            "ref": f"vault://signal/{batch['id']}",
                            "quote": output["rationale"][:300],
                            "authority": "primary",
                        }
                    ],
                    "predicted_outcome": (
                        "Full regression coverage for this batch's failure pattern."
                    ),
                    "risks": ["No prior sampled agreement rate for this function yet."],
                    "distinctness_axis": "activates every generated case immediately",
                },
                {
                    "option_id": "B",
                    "label": "Sample first",
                    "summary": (
                        f"Spot-check {sample_size} of {len(output['cases'])} case(s) before "
                        "activating the rest."
                    )[:400],
                    "payload_ref": f"vault://signal/{batch['id']}",
                    "evidence_refs": [
                        {
                            "source_type": "vault_asset",
                            "ref": f"vault://signal/{batch['id']}",
                            "quote": output["rationale"][:300],
                            "authority": "primary",
                        }
                    ],
                    "predicted_outcome": (
                        "Lower risk of a wrong verdict propagating into the harness."
                    ),
                    "risks": [],
                    "distinctness_axis": "activates a bounded sample first, full set held back",
                },
                {
                    "option_id": "C",
                    "label": "Hold",
                    "summary": "Do not activate any generated case yet."[:400],
                    "payload_ref": f"vault://signal/{batch['id']}",
                    "evidence_refs": [
                        {
                            "source_type": "vault_asset",
                            "ref": f"vault://signal/{batch['id']}",
                            "quote": output["rationale"][:300],
                            "authority": "primary",
                        }
                    ],
                    "predicted_outcome": "No new harness coverage this cycle.",
                    "risks": [],
                    "distinctness_axis": "activates nothing this cycle",
                },
            ]
            card = build_card(
                kind="system.prompt_change",
                level=0,  # overridden to non_negotiable/realtime by build_card itself
                title=(
                    f"Fn {target_function_id}: {len(output['cases'])} generated eval case(s)"
                )[:120],
                decision_question="Activate these generated regression cases?",
                options=options,
                # prompt.md: "Recommend B for the first suite of each
                # function" -- no sampled-agreement history exists yet
                # for any function (nothing has ever been ratified), so B
                # is always the honest recommendation today.
                recommended="B",
                evidence_refs=[
                    {
                        "source_type": "vault_asset",
                        "ref": f"vault://signal/{batch['id']}",
                        "authority": "primary",
                    }
                ],
                produced_by={"function_id": 127, "prompt_version": "0.1.0"},
                register_rows=["H14"],
                rationale=output["rationale"],
                lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
            )
            created = vault.create_option_card(
                {
                    "card_id": card["card_id"],
                    "kind": card["kind"],
                    "autonomy_level": card["autonomy_level"],
                    "risk_tier": card["risk_tier"],
                    "agent_run_id": agent_run["id"],
                    "produced_by_function": 127,
                    "card": card,
                    "created_at": card["created_at"],
                    "expires_at": card["expires_at"],
                }
            )
            vault.update_agent_run(
                agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
            )
            generated.append(
                {
                    "target_function_id": target_function_id,
                    "card_id": created["card_id"],
                    "case_count": len(output["cases"]),
                }
            )

    db.set_result_ref(
        task_id,
        {
            "status": "generated" if generated else "no_production_failures",
            "generated": generated,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


