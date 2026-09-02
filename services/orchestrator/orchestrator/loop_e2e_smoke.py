"""Live loop-proof-circuit smoke test (plan step 20; AC-01, AC-18, AC-30).

Run inside caj-loop-e2e-smoke (infra/modules/orchestrator/
loop-e2e-smoke-job.bicep) via `python -m orchestrator.loop_e2e_smoke`.
Mirrors orchestrator/smoke_test.py's proven pattern (a GENUINE live check,
not a self-comparison): publishes a synthetic daily-signal-loop heartbeat
onto the REAL `event` queue, derives the deterministic task_ids that
heartbeat's event_id produces (decompose()'s own uuid5 scheme), then
polls the LIVE deployed ca-orchestrator app's own GET /runs/{task_ref}
endpoint (step 17) — keyed on the request-linkedin-approval task_id, the
deepest S8 proof-circuit stage — until the proof circuit reaches a
terminal state.

Success bar (matching the GOAL's own smoke wording): signal -> brief ->
draft -> QA -> approval-card-created. The Buffer write stays dry-run
regardless (structurally enforced by Publisher's own dry-run-force logic,
step 14) -- this smoke never calls POST /publish at all.

F-SMOKE-GREEN-REDEFINITION (2026-08-05, round 19b part 2 of Pieter's
"Both" ruling): "approval-card-created" is the success bar for a run whose
content clears QA -- but as of round 19b this smoke runs the REAL pipeline
against a REAL orchestrator image with REAL LLM-generated draft content
(not a hand-picked fixture), so content-qa-review (function 02, Brand
Steward QA) can genuinely and correctly reject it, exactly as it did on
2026-08-05's run (an unsupported-claim superlative). That is proof the
gate works, not a smoke defect. A run that stops at a legitimate QA_BLOCKED
verdict is therefore ALSO a pass -- distinguished, stage-by-stage, from
every other non-completed outcome (a stage stuck mid-flight, an infra
dead-letter unconnected to QA, an application bug in a non-QA stage),
which still fail the smoke exactly as before. See
`_is_legitimate_qa_block` / `_is_expected_qa_block_cascade` /
`evaluate_final_state`.

Bounded polling only (MAX_ATTEMPTS x SLEEP_SECONDS) — never `while True`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from orchestrator import decompose
from orchestrator.loop_loader import load_loop
from orchestrator.models import HeartbeatEvent
from orchestrator.servicebus import producer
from orchestrator.servicebus.consumer import build_client

_ROOT = Path(__file__).resolve().parent.parent
_LOOP_PATH = _ROOT / "loops" / "daily-signal-loop.yaml"

MAX_ATTEMPTS = 40
SLEEP_SECONDS = 15

# The deepest S8 proof-circuit stage (daily-signal-loop.yaml's own
# task_id) -- polling THIS task_ref's lineage (via GET /runs/{task_ref})
# covers the whole signal->brief->draft->QA->approval-card chain in one
# request, since request_approval_handler's depends_on chain walks all the
# way back through content-qa-review -> draft-linkedin-post -> qa ->
# draft -> score -> ingest.
TARGET_SOURCE_TASK_ID = "request-linkedin-approval"

# F-TERMINAL-STATES (2026-08-03, this run): "failed" is ONLY ever set by
# dispatch.py's one QA_BLOCKED transition (a deliberate business-logic
# stop). Every application-level/infra failure instead goes through
# state_machine.record_failure, whose own exhausted-retries outcome is
# "dead_lettered" -- a DIFFERENT string that this set never included.
# Concretely: with "dead_lettered" missing here, a stage that legitimately
# failed and was correctly dead-lettered by the retry state machine could
# never be counted "terminal" by this poll -- it looked identical to a
# stage that was silently still stuck, so every run always burned the
# full MAX_ATTEMPTS x SLEEP_SECONDS budget and reported a generic
# "never reached a terminal state" instead of surfacing the real,
# specific failure immediately. This does NOT change PASS/FAIL semantics
# below -- main()'s own non_completed check already treats any state
# other than "completed" (dead_lettered included) as a smoke failure; it
# only lets the poll loop recognize "settled" (by any outcome) instead of
# waiting out the full bound on every genuine failure.
TERMINAL_STATES = {"completed", "failed", "dead_lettered"}


def build_heartbeat() -> HeartbeatEvent:
    return HeartbeatEvent(
        envelope_version="1",
        event_type="heartbeat",
        event_id=uuid.uuid4(),
        loop_id="daily-signal-loop",
        fired_at=datetime.now(timezone.utc),
        source="caj-loop-e2e-smoke",
    )


def predict_batch(heartbeat: HeartbeatEvent) -> list[dict]:
    """The whole decomposed task batch this heartbeat will produce.

    decompose() is deterministic (uuid5 over event_id + source task id),
    so predicting the batch here yields exactly the task_ids the worker
    will create -- which is what lets this smoke ask about tasks it never
    published directly.
    """
    return decompose.decompose(load_loop(_LOOP_PATH), heartbeat)


def predict_target_task_id(batch: list[dict]) -> str:
    by_source = {t["source_task_id"]: t for t in batch}
    target = by_source.get(TARGET_SOURCE_TASK_ID)
    if target is None:
        raise RuntimeError(
            f"{TARGET_SOURCE_TASK_ID!r} not found in daily-signal-loop.yaml's decomposed "
            "task batch -- has the proof circuit been removed (AC-31's reconciliation)?"
        )
    return target["task_id"]


def poll_run_state(runs_url: str, task_ref: str) -> dict | None:
    """Bounded poll of GET /runs/{task_ref} until every stage in the
    lineage reaches a terminal state (completed or failed)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # follow_redirects=True: defense-in-depth against the same
            # http-> 301 https redirect L-0063 hit for the sibling
            # orchestrator-smoke job's status URL (smoke_test.py's
            # poll_status) -- Container Apps internal ingress always
            # terminates TLS and 301s a plain http:// request.
            resp = httpx.get(
                f"{runs_url.rstrip('/')}/{task_ref}", timeout=10.0, follow_redirects=True
            )
            if resp.status_code == 404:
                print(f"attempt {attempt}/{MAX_ATTEMPTS}: task_ref not yet known (404)")
            else:
                resp.raise_for_status()
                body = resp.json()
                stages = body.get("stages", [])
                terminal_count = sum(1 for s in stages if s.get("state") in TERMINAL_STATES)
                print(
                    f"attempt {attempt}/{MAX_ATTEMPTS}: {terminal_count}/{len(stages)} "
                    "stages terminal"
                )
                if stages and terminal_count == len(stages):
                    return body
        except Exception as exc:  # noqa: BLE001
            print(f"attempt {attempt}/{MAX_ATTEMPTS}: runs poll failed: {exc}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(SLEEP_SECONDS)
    return None


def _is_legitimate_qa_block(stage: dict) -> bool:
    """True if `stage` is content-qa-review's OWN deliberate QA_BLOCKED
    stop -- Brand Steward QA (function 02) correctly rejecting content --
    not an infra/application defect. Distinguished from every other
    non-completed state by task_type PLUS the explicit `pass: False`
    verdict dispatch.py's qa_review_handler writes to result_ref before
    the FAILED/QA_BLOCKED transition (dispatch.py, the `if not passed:`
    branch just above `db.transition(task_id, TaskStateEnum.FAILED,
    TransitionReason.QA_BLOCKED)`). A "failed" qa-review stage with no
    result_ref, or with result_ref.pass anything other than exactly
    False, is NOT recognized here -- it falls through to "unexplained"
    and fails the smoke, on the principle that only a verdict shaped
    exactly like dispatch.py's own known-good QA_BLOCKED write counts."""
    if stage.get("task_type") != "qa-review" or stage.get("state") != "failed":
        return False
    result_ref = stage.get("result_ref") or {}
    return result_ref.get("pass") is False


def _is_expected_qa_block_cascade(stage: dict, stages: list[dict]) -> bool:
    """True if `stage` is request-approval, dead_lettered, and this run
    also contains a legitimate qa-review block per
    `_is_legitimate_qa_block`. This is the EXPECTED downstream
    consequence of the _PERMANENTLY_BLOCKED_STATES cascade (PR #62/#69):
    once qa-review is permanently FAILED/QA_BLOCKED, request-approval
    correctly cascades straight to dead_lettered rather than hanging --
    that cascade is proof the mechanism works, not a separate defect, and
    is only accepted here when a legitimate QA block actually explains
    it (never accepted on its own)."""
    if stage.get("task_type") != "request-approval" or stage.get("state") != "dead_lettered":
        return False
    return any(_is_legitimate_qa_block(s) for s in stages)


def evaluate_final_state(final_state: dict) -> tuple[bool, str]:
    """Pure pass/fail judgement over a completed GET /runs/{task_ref}
    payload (every stage already terminal, per poll_run_state). Returns
    (passed, message) -- kept separate from main() so the classification
    logic is unit-testable without a live orchestrator/Service Bus.

    PASS iff every stage is "completed", OR every non-completed stage is
    explained by a legitimate QA block (the qa-review stage itself, or
    request-approval's expected dead_lettered cascade from it). Any other
    non-completed stage -- unexplained -- fails the smoke, unchanged from
    the pre-round-19b behaviour."""
    stages = final_state.get("stages", [])
    non_completed = [s for s in stages if s.get("state") != "completed"]

    if not non_completed:
        return True, f"PASS: proof circuit reached completed for all {len(stages)} stages"

    unexplained = [
        s
        for s in non_completed
        if not _is_legitimate_qa_block(s) and not _is_expected_qa_block_cascade(s, stages)
    ]
    if unexplained:
        return (
            False,
            f"FAIL: {len(unexplained)} stage(s) did not reach completed and are not "
            f"an explained QA block: {unexplained}",
        )

    blocked = next(s for s in non_completed if _is_legitimate_qa_block(s))
    violations = (blocked.get("result_ref") or {}).get("violations")
    completed_count = len(stages) - len(non_completed)
    return (
        True,
        f"PASS (QA_BLOCKED): proof circuit reached a legitimate Brand Steward QA "
        f"rejection at {blocked['task_id']} (violations={violations}) -- pipeline "
        f"proven end-to-end, QA gate confirmed working. {completed_count}/{len(stages)} "
        f"stages completed, {len(non_completed)} explained by the QA block.",
    )


# --- scan coverage report (F-SMOKE-SCAN-COVERAGE) ---------------------------
#
# WHY THIS EXISTS. This smoke polls the request-linkedin-approval lineage,
# which is 7 stages: the ingest -> score -> draft -> QA -> approval chain.
# The eleven fan-out scanners hang off `ingest` in PARALLEL with that
# chain, so none of them is in the lineage and the smoke has never said a
# word about any of them. It passed identically whether all eleven
# scanned, or all eleven reported "not configured", or a sourced one
# dead-lettered.
#
# That was tolerable while every scanner was sourceless and the answer was
# always the same. It stopped being tolerable on 2 Sep 2026, when
# competitor-discovery and fabric-ecosystem were given real source urls
# that had never been probed -- so "did those five urls actually return
# anything" became a live question that the deploy could not answer, and
# the only way to find out was to go and read ca-orchestrator's own logs,
# which this workflow dumps ONLY on failure.
#
# Each scanner's own result_ref already carries the answer. This walks the
# predicted batch, asks GET /runs/{task_id} for each scanning task, and
# prints one line per profile.
#
# DELIBERATELY NOT A PASS/FAIL CHANGE. The smoke's contract is "the proof
# circuit works end to end", and a scanner whose sources went quiet is a
# sourcing question, not a pipeline defect -- failing the deploy on it
# would make an unrelated feed outage block shipping. The states that ARE
# defects already fail loudly where they belong: a scanner whose sources
# all come back thin fails its own task at the retrieval floor
# (F-INGEST-CONTENT-FLOOR), and that shows up here as a dead_lettered
# line. Making a dead_lettered scanner fail this smoke is a reasonable
# next step and a separate decision.
SCAN_REPORT_MARKER = "scan-coverage"

# Scanners run in parallel with the proof circuit, so by the time the
# deepest chain stage is terminal they normally are too. This is the
# allowance for the case where one is still mid-flight -- small, because
# a still-running scanner is reported as such rather than waited out.
SCAN_SETTLE_ATTEMPTS = 4


def scanning_tasks(batch: list[dict]) -> list[dict]:
    """Every decomposed task that scans a profile, in loop order.

    Keyed on `params.profile_id` rather than on a hardcoded task-type
    list: a profile is exactly the thing this report is about, and a
    twelfth scanner added to the loop is picked up with no edit here.
    Includes `ingest` itself, which is the market-intelligence scan.
    """
    return [t for t in batch if (t.get("params") or {}).get("profile_id")]


def fetch_stage(runs_url: str, task_id: str) -> dict | None:
    """That task's own stage from its run-state payload, or None.

    GET /runs/{task_ref} returns the task plus its ancestors, so this
    picks the requested task out of the lineage rather than assuming a
    position in it.
    """
    try:
        resp = httpx.get(
            f"{runs_url.rstrip('/')}/{task_id}", timeout=10.0, follow_redirects=True
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        stages = resp.json().get("stages", [])
    except Exception as exc:  # noqa: BLE001 - a report must never sink the smoke
        print(f"{SCAN_REPORT_MARKER}: could not read {task_id}: {exc}")
        return None
    return next((s for s in stages if s.get("task_id") == task_id), None)


def describe_scan(task: dict, stage: dict | None) -> str:
    """One line for one profile: what it did, or why it did nothing."""
    profile = (task.get("params") or {}).get("profile_id", "?")
    source = task.get("source_task_id", "?")

    if stage is None:
        return f"{SCAN_REPORT_MARKER}: {source} ({profile}) -> no run state (task not created?)"

    state = stage.get("state", "?")
    ref = stage.get("result_ref") or {}
    status = ref.get("status")

    if status == "not_configured":
        return (
            f"{SCAN_REPORT_MARKER}: {source} ({profile}) -> {state}, not_configured "
            "-- no source urls in scan-profiles.yaml"
        )

    # A scanned profile reports what its sources actually yielded. Both
    # counts matter and neither substitutes for the other: cards=0 with
    # sources_used=3 is a quiet market, cards=0 with sources_used=1 of 3
    # is a sourcing problem wearing the same face.
    if "card_count" in ref or "sources_used" in ref:
        return (
            f"{SCAN_REPORT_MARKER}: {source} ({profile}) -> {state}, "
            f"cards={ref.get('card_count', '?')} "
            f"sources_used={ref.get('sources_used', '?')}"
            f"/{ref.get('sources_configured', '?')} "
            f"repeats={ref.get('repeat_count', 0)}"
        )

    if state != "completed":
        return (
            f"{SCAN_REPORT_MARKER}: {source} ({profile}) -> {state} "
            f"-- see ca-orchestrator logs for this task_id: {stage.get('task_id')}"
        )

    return f"{SCAN_REPORT_MARKER}: {source} ({profile}) -> {state}, result_ref={ref}"


def report_scan_coverage(runs_url: str, batch: list[dict]) -> list[str]:
    """Print one line per scanning profile. Returns the lines, for tests.

    Never raises: a diagnostic that can fail the deploy it is diagnosing
    is worse than no diagnostic. fetch_stage already swallows transport
    errors, so the outer guard here is for the rest -- a malformed
    result_ref, an unexpected payload shape -- which would otherwise turn
    a green pipeline red for the sake of a log line.

    Never changes the smoke's exit code either; see the module comment
    above on why this reports rather than gates.
    """
    try:
        return _report_scan_coverage(runs_url, batch)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        line = f"{SCAN_REPORT_MARKER}: report failed ({exc}) -- smoke verdict unaffected"
        print(line)
        return [line]


def _report_scan_coverage(runs_url: str, batch: list[dict]) -> list[str]:
    targets = scanning_tasks(batch)
    if not targets:
        line = f"{SCAN_REPORT_MARKER}: no task in this loop carries a profile_id"
        print(line)
        return [line]

    stages: dict[str, dict | None] = {}
    for attempt in range(1, SCAN_SETTLE_ATTEMPTS + 1):
        for task in targets:
            task_id = task["task_id"]
            if stages.get(task_id) is None or stages[task_id].get("state") not in TERMINAL_STATES:
                stages[task_id] = fetch_stage(runs_url, task_id)
        pending = [
            t
            for t in targets
            if (stages.get(t["task_id"]) or {}).get("state") not in TERMINAL_STATES
        ]
        if not pending or attempt == SCAN_SETTLE_ATTEMPTS:
            break
        print(
            f"{SCAN_REPORT_MARKER}: {len(pending)} scan(s) still in flight, "
            f"waiting {SLEEP_SECONDS}s ({attempt}/{SCAN_SETTLE_ATTEMPTS})"
        )
        time.sleep(SLEEP_SECONDS)

    lines = [describe_scan(task, stages.get(task["task_id"])) for task in targets]
    for line in lines:
        print(line)
    return lines


def main() -> int:
    runs_url = os.environ.get("ORCHESTRATOR_RUNS_URL")
    namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
    if not runs_url or not namespace:
        print("FAIL: ORCHESTRATOR_RUNS_URL and SERVICE_BUS_NAMESPACE must both be set")
        return 1

    heartbeat = build_heartbeat()
    batch = predict_batch(heartbeat)
    target_task_id = predict_target_task_id(batch)

    client = build_client(use_local_double=False, namespace=namespace)
    heartbeat_dict = json.loads(heartbeat.model_dump_json())
    producer.publish("event", heartbeat_dict, client)
    print(
        f"published heartbeat event_id={heartbeat.event_id} loop_id={heartbeat.loop_id} "
        f"-> target task_ref={target_task_id}"
    )

    final_state = poll_run_state(runs_url, target_task_id)
    if final_state is None:
        print("FAIL: proof circuit never reached a terminal state within the bounded poll")
        # Reported even here: on a run where the proof circuit stalled,
        # what the twelve scans did is diagnostic rather than decorative.
        report_scan_coverage(runs_url, batch)
        return 1

    passed, message = evaluate_final_state(final_state)
    print(message)

    # Printed on pass AND fail, and before the early return, so the
    # coverage lines are in the log of every run rather than only the ones
    # somebody already had a reason to open.
    report_scan_coverage(runs_url, batch)

    if not passed:
        return 1

    print(f"approval_decision_status: {final_state.get('approval_decision_status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
