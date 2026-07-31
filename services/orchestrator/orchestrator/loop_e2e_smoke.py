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

TERMINAL_STATES = {"completed", "failed"}


def build_heartbeat() -> HeartbeatEvent:
    return HeartbeatEvent(
        envelope_version="1",
        event_type="heartbeat",
        event_id=uuid.uuid4(),
        loop_id="daily-signal-loop",
        fired_at=datetime.now(timezone.utc),
        source="caj-loop-e2e-smoke",
    )


def predict_target_task_id(heartbeat: HeartbeatEvent) -> str:
    loop = load_loop(_LOOP_PATH)
    predicted = decompose.decompose(loop, heartbeat)
    by_source = {t["source_task_id"]: t for t in predicted}
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
            resp = httpx.get(f"{runs_url.rstrip('/')}/{task_ref}", timeout=10.0)
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


def main() -> int:
    runs_url = os.environ.get("ORCHESTRATOR_RUNS_URL")
    namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
    if not runs_url or not namespace:
        print("FAIL: ORCHESTRATOR_RUNS_URL and SERVICE_BUS_NAMESPACE must both be set")
        return 1

    heartbeat = build_heartbeat()
    target_task_id = predict_target_task_id(heartbeat)

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
        return 1

    stages = final_state.get("stages", [])
    non_completed = [s for s in stages if s.get("state") != "completed"]
    if non_completed:
        # A qa_blocked-style stop is a legitimate business outcome, not
        # itself proof the SMOKE failed -- but it IS a smoke failure for
        # THIS deliberately-clean synthetic content path, since nothing
        # here should trip Brand Steward QA.
        print(f"FAIL: {len(non_completed)} stage(s) did not reach completed: {non_completed}")
        return 1

    print(f"PASS: proof circuit reached completed for all {len(stages)} stages")
    print(f"approval_decision_status: {final_state.get('approval_decision_status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
