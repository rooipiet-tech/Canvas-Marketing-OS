"""Live heartbeat-injection-and-golden-compare smoke test (AC-028).

Run inside caj-orchestrator-smoke-test (infra/modules/orchestrator/smoke-test-job.bicep)
via `python -m orchestrator.smoke_test`. This is a GENUINE live check, not
a self-comparison: it publishes a synthetic daily-signal-loop heartbeat
onto the REAL `event` queue (via this job's own managed identity, Data
Sender role), derives the deterministic task_ids that heartbeat's event_id
will produce (decompose()'s own documented uuid5 scheme — used here only
to know which ids to poll for, never compared against itself), then polls
the LIVE deployed app's own GET /status endpoint (reached over the shared
Container Apps environment's internal DNS) until those task_ids appear,
and compares what THAT independently-running process reports against the
checked-in golden file.

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
_GOLDEN_PATH = _ROOT / "tests" / "golden" / "daily-signal-loop.expected.json"

MAX_ATTEMPTS = 20
SLEEP_SECONDS = 15


def build_heartbeat() -> HeartbeatEvent:
    return HeartbeatEvent(
        envelope_version="1",
        event_type="heartbeat",
        event_id=uuid.uuid4(),
        loop_id="daily-signal-loop",
        fired_at=datetime.now(timezone.utc),
        source="caj-orchestrator-smoke-test",
    )


def predict_task_ids(heartbeat: HeartbeatEvent) -> list[dict]:
    loop = load_loop(_LOOP_PATH)
    return decompose.decompose(loop, heartbeat)


def poll_status(status_url: str, expected_task_ids: set[str]) -> list[dict] | None:
    # INCIDENT (2026-07-31): Container Apps internal ingress terminates TLS
    # and 301-redirects a plain http:// request to the same URL over
    # https:// (confirmed live: execution caj-orchestrator-smoke-test-tdituuf
    # published its heartbeat, ca-orchestrator decomposed and completed all
    # 20 resulting tasks within 12 seconds, and every one of this poll's 20
    # attempts still failed — httpx does not follow redirects by default,
    # so raise_for_status() raised "Redirect response '301 Moved
    # Permanently'" on every single request without ever reaching /status).
    # infra/main.bicep's orchestratorStatusUrl now passes https:// directly,
    # so this should be a no-op in practice — kept as defense-in-depth
    # against any future http:// misconfiguration.
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = httpx.get(status_url, timeout=10.0, follow_redirects=True)
            resp.raise_for_status()
            tasks = resp.json()
            found_ids = {t.get("task_id") for t in tasks}
            matched = len(found_ids & expected_task_ids)
            print(
                f"attempt {attempt}/{MAX_ATTEMPTS}: found {matched}/{len(expected_task_ids)} "
                "expected task_ids"
            )
            if expected_task_ids <= found_ids:
                return tasks
        except Exception as exc:  # noqa: BLE001
            print(f"attempt {attempt}/{MAX_ATTEMPTS}: status poll failed: {exc}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(SLEEP_SECONDS)
    return None


def compare_against_golden(live_tasks: list[dict], predicted: list[dict], golden: dict) -> bool:
    by_id = {t["task_id"]: t for t in live_tasks}
    predicted_by_source = {t["source_task_id"]: t for t in predicted}

    golden_tasks = golden["tasks"]
    for golden_task in golden_tasks:
        predicted_task = predicted_by_source.get(golden_task["source_task_id"])
        if predicted_task is None:
            print(f"FAIL: no predicted task for source_task_id={golden_task['source_task_id']!r}")
            return False
        live_task = by_id.get(predicted_task["task_id"])
        if live_task is None:
            print(
                f"FAIL: predicted task_id={predicted_task['task_id']!r} not found in live /status"
            )
            return False
        if live_task["task_type"] != golden_task["task_type"]:
            print(
                f"FAIL: task_type mismatch for {predicted_task['task_id']!r}: "
                f"live={live_task['task_type']!r} golden={golden_task['task_type']!r}"
            )
            return False
    return True


def main() -> int:
    status_url = os.environ.get("ORCHESTRATOR_STATUS_URL")
    namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
    if not status_url or not namespace:
        print("FAIL: ORCHESTRATOR_STATUS_URL and SERVICE_BUS_NAMESPACE must both be set")
        return 1

    heartbeat = build_heartbeat()
    predicted = predict_task_ids(heartbeat)
    expected_task_ids = {t["task_id"] for t in predicted}

    client = build_client(use_local_double=False, namespace=namespace)
    heartbeat_dict = json.loads(heartbeat.model_dump_json())
    producer.publish("event", heartbeat_dict, client)
    print(f"published heartbeat event_id={heartbeat.event_id} loop_id={heartbeat.loop_id}")

    live_tasks = poll_status(status_url, expected_task_ids)
    if live_tasks is None:
        print("FAIL: expected task_ids never appeared in live /status within the bounded poll")
        return 1

    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    ok = compare_against_golden(live_tasks, predicted, golden)
    if not ok:
        return 1

    print("PASS: live smoke test matched golden file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
