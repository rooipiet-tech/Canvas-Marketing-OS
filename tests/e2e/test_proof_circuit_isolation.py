"""AC-30 — the S8 proof circuit is unmistakably distinguishable from
S11's weekly production pipeline in every downstream system it touches:
request-approval's function_id is the REAL production
'publish.social_post' (no new autonomy.yaml entry), and its
approval_inbox row carries the 'loop-proof' tag in a queryable field
(preview_reference AND preview_title — both required, per PV3-02: only
preview_title is what console/app/templates/approvals.html actually
renders).
"""

from __future__ import annotations

from typing import Any

import psycopg


def test_request_approval_uses_the_real_publish_function_id(
    live_daily_loop_run: dict[str, Any],
) -> None:
    final_state = live_daily_loop_run["final_state"]
    approval_stage = next(
        (s for s in final_state.get("stages", []) if s["task_type"] == "request-approval"), None
    )
    assert approval_stage is not None
    result_ref = approval_stage.get("result_ref") or {}
    assert result_ref.get("function_id") == "publish.social_post", (
        "the proof circuit must use the REAL production function_id, never a "
        f"dedicated smoke/test one -- got {result_ref.get('function_id')!r}"
    )


def test_approval_inbox_row_carries_both_loop_proof_tags(
    live_daily_loop_run: dict[str, Any], live_db_url: str
) -> None:
    final_state = live_daily_loop_run["final_state"]
    approval_stage = next(
        (s for s in final_state.get("stages", []) if s["task_type"] == "request-approval"), None
    )
    assert approval_stage is not None
    decision_id = (approval_stage.get("result_ref") or {}).get("decision_id")
    assert decision_id, "request-approval's result_ref carries no decision_id"

    with psycopg.connect(live_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT preview_reference, preview_title FROM governance.approval_inbox "
                "WHERE gate_decision_id = %s",
                (decision_id,),
            )
            row = cur.fetchone()

    assert row is not None, f"no approval_inbox row found for gate_decision_id={decision_id}"
    preview_reference, preview_title = row
    assert preview_reference is not None and preview_reference.startswith("loop-proof://"), (
        f"preview_reference must start with 'loop-proof://', got {preview_reference!r}"
    )
    assert preview_title is not None and preview_title.startswith("[LOOP-PROOF] "), (
        f"preview_title (the field console's approvals.html actually renders) must start "
        f"with '[LOOP-PROOF] ', got {preview_title!r}"
    )


def test_every_proof_circuit_agent_run_is_tagged(
    live_daily_loop_run: dict[str, Any], live_db_url: str
) -> None:
    """Every Vault agent_run the S8 proof circuit creates (draft-content,
    and SPECIFICALLY the proof circuit's own qa-review invocation --
    never the daily loop's separate brief-QA qa-review stage, which
    shares the same task_type but is NOT proof-circuit-tagged) carries
    agent_name='loop-proof-circuit'.

    Disambiguated by result_ref shape, not task_id naming: the
    proof-circuit's qa-review pass-case result_ref carries a
    vault_asset_id (it validated draft-content's LinkedIn post); the
    daily loop's brief-QA pass-case result_ref carries a brief_id instead
    (see orchestrator/dispatch.py's qa_review_handler).
    """
    final_state = live_daily_loop_run["final_state"]
    stages = final_state.get("stages", [])

    draft_content_refs = [
        s.get("result_ref") or {} for s in stages if s["task_type"] == "draft-content"
    ]
    proof_circuit_qa_refs = [
        s.get("result_ref") or {}
        for s in stages
        if s["task_type"] == "qa-review" and (s.get("result_ref") or {}).get("vault_asset_id")
    ]

    agent_run_ids = [ref.get("agent_run_id") for ref in draft_content_refs + proof_circuit_qa_refs]
    agent_run_ids = [a for a in agent_run_ids if a]
    assert agent_run_ids, "no proof-circuit agent_run_ids found in this run's stages"

    with psycopg.connect(live_db_url) as conn:
        with conn.cursor() as cur:
            for agent_run_id in agent_run_ids:
                cur.execute("SELECT agent_name FROM agent_runs WHERE id = %s", (agent_run_id,))
                row = cur.fetchone()
                assert row is not None, f"agent_run {agent_run_id} not found"
                assert row[0] == "loop-proof-circuit", (
                    f"agent_run {agent_run_id} has agent_name={row[0]!r}, expected "
                    "'loop-proof-circuit'"
                )
