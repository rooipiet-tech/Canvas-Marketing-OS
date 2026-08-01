"""Shared fixtures for the /tests/e2e suite (plan step 18; AC-14).

Runs against REAL cmos-dev services — ca-orchestrator, ca-gatekeeper,
ca-vault, model-gateway (via the orchestrator's own dispatch handlers),
and the real Service Bus namespace. Every fixture that needs live
infrastructure SKIPS CLEANLY (never fails) when the relevant env var/live
resolution is unavailable — mirrors services/orchestrator/tests/
conftest.py's and services/gatekeeper|publisher/tests/conftest.py's
identical "skip when no live infra" convention, generalised here to a
whole deployed environment rather than just a local Postgres.

Base URL resolution order for every service (L-0025, never a hardcoded
FQDN): an explicit env var override first, then a live
`az containerapp show` lookup — exactly orchestrator/clients/
azure_fqdn.py's own resolve_live_fqdn() convention, reused directly here
(services/orchestrator is added to sys.path below so this suite can
import the real orchestrator package rather than re-implementing its
dispatch/decompose/models logic).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_SERVICE_ROOT = REPO_ROOT / "services" / "orchestrator"
if str(ORCHESTRATOR_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_SERVICE_ROOT))

from orchestrator.clients.azure_fqdn import resolve_live_fqdn  # noqa: E402

MAX_POLL_ATTEMPTS = 40
POLL_SLEEP_SECONDS = 15


def _resolve(env_var: str, app_name: str) -> str | None:
    override = os.environ.get(env_var)
    if override:
        return override
    return resolve_live_fqdn(app_name)


@pytest.fixture(scope="session")
def orchestrator_base_url() -> str:
    url = _resolve("E2E_ORCHESTRATOR_BASE_URL", "ca-orchestrator")
    if not url:
        pytest.skip(
            "E2E_ORCHESTRATOR_BASE_URL not set and ca-orchestrator's live FQDN could not be "
            "resolved (no `az` login / not deployed) — this e2e suite requires live cmos-dev"
        )
    return url


@pytest.fixture(scope="session")
def gatekeeper_base_url() -> str:
    url = _resolve("E2E_GATEKEEPER_BASE_URL", "ca-gatekeeper")
    if not url:
        pytest.skip("E2E_GATEKEEPER_BASE_URL not set and ca-gatekeeper's live FQDN unresolvable")
    return url


@pytest.fixture(scope="session")
def vault_base_url() -> str:
    url = _resolve("E2E_VAULT_BASE_URL", "ca-vault")
    if not url:
        pytest.skip("E2E_VAULT_BASE_URL not set and ca-vault's live FQDN unresolvable")
    return url


@pytest.fixture(scope="session")
def service_bus_namespace() -> str:
    namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
    if not namespace:
        pytest.skip("SERVICE_BUS_NAMESPACE not set — cannot publish a live heartbeat")
    return namespace


@pytest.fixture(scope="session")
def live_db_url() -> str:
    """The shared Azure Postgres server's connection string (DATABASE_URL/
    PG_URL) — infra/main.bicep threads the SAME administrator credential
    to the orchestrator/vault/governance migration jobs, so task_state,
    the Vault's public-schema tables (assets/agent_runs/costs), and
    governance.approval_inbox are all reachable from one DSN in the real
    deployed environment (different schemas, one server)."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("PG_URL")
    if not url:
        pytest.skip(
            "DATABASE_URL/PG_URL not set — this test needs a direct read of task_state/"
            "governance/Vault tables on the shared cmos-dev Postgres server"
        )
    return url


def run_campaign_id(heartbeat: Any, loop_id: str = "daily-signal-loop") -> str:
    """The SAME deterministic uuid5 worker.py's handle_heartbeat_message
    computes for TaskEnvelope.campaign_id — every task in one heartbeat's
    run shares this id, and every dispatch.py handler's result_ref stores
    it too, so a test can independently derive it from the heartbeat
    alone (no extra round trip needed)."""
    return str(uuid.uuid5(heartbeat.event_id, f"campaign:{loop_id}"))


@pytest.fixture(scope="session")
def env_vars_configured(
    orchestrator_base_url: str,
    gatekeeper_base_url: str,
    vault_base_url: str,
    service_bus_namespace: str,
) -> dict[str, str]:
    """Convenience aggregate — depending on this single fixture skips a
    test cleanly if ANY of the 4 core live dependencies is unavailable."""
    return {
        "orchestrator": orchestrator_base_url,
        "gatekeeper": gatekeeper_base_url,
        "vault": vault_base_url,
        "service_bus_namespace": service_bus_namespace,
    }


def publish_daily_signal_heartbeat(service_bus_namespace: str) -> tuple[Any, list[dict]]:
    """Publishes ONE real daily-signal-loop heartbeat onto the live
    `event` queue and returns (heartbeat, predicted_task_batch) — the
    SAME mechanism orchestrator/smoke_test.py and orchestrator/
    loop_e2e_smoke.py already use, reused directly rather than
    reimplemented."""
    from orchestrator import decompose
    from orchestrator.loop_loader import load_loop
    from orchestrator.models import HeartbeatEvent
    from orchestrator.servicebus import producer
    from orchestrator.servicebus.consumer import build_client

    loop = load_loop(ORCHESTRATOR_SERVICE_ROOT / "loops" / "daily-signal-loop.yaml")
    heartbeat = HeartbeatEvent(
        envelope_version="1",
        event_type="heartbeat",
        event_id=uuid.uuid4(),
        loop_id="daily-signal-loop",
        fired_at=datetime.now(timezone.utc),
        source="tests-e2e",
    )
    predicted = decompose.decompose(loop, heartbeat)

    client = build_client(use_local_double=False, namespace=service_bus_namespace)
    producer.publish("event", json.loads(heartbeat.model_dump_json()), client)
    return heartbeat, predicted


def poll_run_state(orchestrator_base_url: str, task_ref: str) -> dict | None:
    """Bounded poll of GET /runs/{task_ref} until every stage in the
    lineage reaches a terminal state (completed or failed)."""
    import httpx

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        try:
            resp = httpx.get(f"{orchestrator_base_url.rstrip('/')}/runs/{task_ref}", timeout=10.0)
            if resp.status_code == 200:
                body = resp.json()
                stages = body.get("stages", [])
                terminal = {"completed", "failed"}
                if stages and all(s.get("state") in terminal for s in stages):
                    return body
        except Exception:  # noqa: BLE001 - keep polling
            pass
        if attempt < MAX_POLL_ATTEMPTS:
            time.sleep(POLL_SLEEP_SECONDS)
    return None


@pytest.fixture(scope="session")
def live_daily_loop_run(env_vars_configured: dict[str, str]) -> dict[str, Any]:
    """Session-scoped: triggers ONE real daily-signal-loop heartbeat and
    polls until the S8 proof circuit reaches a terminal state, so every
    test in this module that only needs to OBSERVE one full live run
    (AC-01, AC-03, AC-10, AC-15, AC-24, AC-30) shares it rather than each
    triggering its own (expensive: real LLM calls, real Vault writes,
    real Gatekeeper decisions)."""
    heartbeat, predicted = publish_daily_signal_heartbeat(
        env_vars_configured["service_bus_namespace"]
    )
    by_source = {t["source_task_id"]: t for t in predicted}
    target = by_source["request-linkedin-approval"]

    final_state = poll_run_state(env_vars_configured["orchestrator"], target["task_id"])
    if final_state is None:
        pytest.fail(
            "live daily-signal-loop run never reached a terminal state within the bounded poll "
            f"(heartbeat event_id={heartbeat.event_id})"
        )
    return {
        "heartbeat": heartbeat,
        "predicted": predicted,
        "by_source": by_source,
        "final_state": final_state,
    }
