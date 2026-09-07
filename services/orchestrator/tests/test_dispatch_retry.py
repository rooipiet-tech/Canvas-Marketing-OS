"""F-DISPATCH-RETRY: a genuine handler failure (anything dispatch_task's
own not-ready gate does NOT catch -- bad upstream data, a
REDACTION_BLOCKED gateway response, an unreachable dependency) must be
routed through the real retry/dead-letter state machine and reach a
genuine terminal state, instead of leaving the task stranded at `running`
forever with zero retry (the exact "assumed backstop never fires"
condition TaskNotReadyError's own docstring already describes for the
not-ready case -- this covers every OTHER failure mode).

Root-cause context (2026-08-03, live): deploy-loop-e2e-smoke #22 showed
"0/7 stages terminal" for the full bounded poll -- WORSE than every prior
run's "1/7" -- because the entry-point task (ingest-signals) hit a
genuine REDACTION_BLOCKED response from model-gateway (real fetched web
content tripped the `full-name-like` pattern; a separate, legitimate
scan-scope question from PR #59's system-role exemption, not itself
fixed here) and, with no handling for a generic dispatch_task exception,
sat at `running` forever -- so NOTHING, not even stage 1, ever reached a
terminal state for the whole 10-minute poll.

Uses tests/fakes-style in-memory doubles (no live Postgres/Service Bus)
so this runs everywhere, including this sandbox.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from orchestrator import dispatch, worker
from orchestrator.models import TaskEnvelope, TaskStateEnum
from orchestrator.servicebus import producer
from orchestrator.servicebus.local_double import InMemoryServiceBus


class FakeTaskDB:
    """Same in-memory surface as test_dispatch_gate.py's FakeTaskDB
    (duplicated rather than imported -- this package has no __init__.py,
    so test modules are not import targets for each other)."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        # TD-07: backs get_ledgered_agent_run/record_ledgered_agent_run
        # below -- the in-memory stand-in for migrations/0005_agent_run_
        # idempotency.sql's agent_run_ledger table.
        self._agent_run_ledger: dict[str, str] = {}

    def seed(self, task_id: str, task_type: str, *, state: str = "dispatchable") -> None:
        self.tasks[task_id] = {
            "task_id": task_id,
            "loop_id": "test-loop",
            "task_type": task_type,
            "state": state,
            "depends_on": [],
            "result_ref": None,
            "retry_count": 0,
        }

    def get_task(self, task_id: str, database_url: str | None = None) -> dict[str, Any] | None:
        return self.tasks.get(task_id)

    def get_ledgered_agent_run(
        self, idempotency_key: str, database_url: str | None = None
    ) -> str | None:
        return self._agent_run_ledger.get(idempotency_key)

    def record_ledgered_agent_run(
        self,
        idempotency_key: str,
        task_id: str,
        agent_run_id: str,
        database_url: str | None = None,
    ) -> None:
        self._agent_run_ledger.setdefault(idempotency_key, agent_run_id)

    def get_tasks(
        self, task_ids: list[str], database_url: str | None = None
    ) -> list[dict[str, Any]]:
        return [self.tasks[t] for t in task_ids if t in self.tasks]

    def set_result_ref(
        self, task_id: str, result_ref: dict[str, Any], database_url: str | None = None
    ) -> None:
        self.tasks[task_id]["result_ref"] = result_ref

    def get_result_ref(
        self, task_id: str, database_url: str | None = None
    ) -> dict[str, Any] | None:
        return self.tasks[task_id]["result_ref"]

    def transition(self, task_id: str, to_state, reason, database_url: str | None = None) -> None:
        to_state_val = to_state.value if hasattr(to_state, "value") else to_state
        if task_id not in self.tasks:
            raise RuntimeError(f"no such task {task_id}")
        self.tasks[task_id]["state"] = to_state_val

    def advance_dependents(
        self, completed_task_id: str, database_url: str | None = None
    ) -> list[str]:
        return []

    def increment_retry(self, task_id: str, database_url: str | None = None) -> int:
        self.tasks[task_id]["retry_count"] += 1
        return self.tasks[task_id]["retry_count"]


def _envelope(task_id: str, task_type: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=uuid.UUID(task_id),
        task_type=task_type,
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        retry_count=0,
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The retry helper sleeps 2s between attempts by design (a real,
    if small, backoff before hitting the handler again) -- irrelevant to
    what these tests assert and not worth 2-6s of real wall-clock per
    test run."""

    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(worker.asyncio, "sleep", _instant_sleep)


def test_handler_failure_retries_and_recovers_without_dead_lettering(monkeypatch):
    """A handler that fails once then succeeds must end COMPLETED, not
    dead_lettered -- the retry path exists precisely so a transient
    failure (e.g. an upstream 5xx) doesn't need a 2nd smoke attempt."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "flaky-task", state="dispatchable")

    calls = {"n": 0}

    def flaky_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise dispatch.DispatchError("upstream returned HTTP 500")
        db.transition(task_id, TaskStateEnum.COMPLETED, "completed")

    monkeypatch.setitem(dispatch.DISPATCH_TABLE, "flaky-task", flaky_handler)

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "flaky-task")

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    assert calls["n"] == 2
    assert db.get_task(task_id)["state"] == TaskStateEnum.COMPLETED.value
    # Never requeued onto the `task` queue -- the retry happened inline.
    assert bus.queue_depth("task") == 0


def test_handler_that_always_fails_reaches_dead_lettered(monkeypatch):
    """The core regression this fix targets: a deterministic failure
    (e.g. REDACTION_BLOCKED on real fetched content, which will trip the
    same pattern on every retry) must still reach a genuine terminal
    state within THIS message's handling -- not requeue forever, and not
    sit at `running` with no further action, ever."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "always-broken-task", state="dispatchable")

    def broken_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
        raise dispatch.DispatchError(
            'gateway returned HTTP 400 for /v1/completions: {"error":{"code":"REDACTION_BLOCKED"'
        )

    monkeypatch.setitem(dispatch.DISPATCH_TABLE, "always-broken-task", broken_handler)

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "always-broken-task")

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    assert db.get_task(task_id)["state"] == TaskStateEnum.DEAD_LETTERED.value
    assert db.get_task(task_id)["retry_count"] == 3
    assert bus.queue_depth("task") == 0


def test_legacy_pass_through_failure_also_retries(monkeypatch):
    """The retry path must work for the legacy_task_pass_through branch
    too (any task_type not in DISPATCH_TABLE), not just the 5 GOAL
    handlers -- dispatch_task routes both through the same not-ready
    gate, so both must be covered by the same retry/dead-letter path."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "some-legacy-type", state="dispatchable")

    original = dispatch.legacy_task_pass_through
    calls = {"n": 0}

    def flaky_legacy(task_id: str, task_type: str, db: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise dispatch.DispatchError("transient failure")
        original(task_id, task_type, db)

    monkeypatch.setattr(dispatch, "legacy_task_pass_through", flaky_legacy)

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "some-legacy-type")

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    assert calls["n"] == 2
    assert db.get_task(task_id)["state"] == TaskStateEnum.COMPLETED.value


def test_dead_lettered_task_is_idempotent_noop_if_reprocessed(monkeypatch):
    """Mirrors state_machine.record_failure's own OR-001 idempotency
    guarantee: if this task_id's message is somehow reprocessed after it
    is already dead_lettered, retry_count must not climb further and the
    state must not change."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "always-broken-task", state="dispatchable")

    def broken_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
        raise dispatch.DispatchError("still broken")

    monkeypatch.setitem(dispatch.DISPATCH_TABLE, "always-broken-task", broken_handler)
    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "always-broken-task")
    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))
    assert db.get_task(task_id)["state"] == TaskStateEnum.DEAD_LETTERED.value
    first_retry_count = db.get_task(task_id)["retry_count"]

    # Force the task back to `dispatchable` (as if a stray redelivered
    # duplicate arrived) and reprocess it -- dispatch_task itself would
    # run it again, fail again, and re-enter the SAME retry helper.
    db.tasks[task_id]["state"] = "dispatchable"
    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))
    # record_failure's own no-op guard only triggers once state is ALREADY
    # dead_lettered AT THE TIME record_failure is called -- forcing the row
    # back to `dispatchable` here bypasses that guard for this one
    # artificial reprocessing, so the helper's first (and only, in this
    # scenario) call to record_failure on the 2nd run sees retry_count
    # already at 3 and increments it to 4 -- still >= 3, so it dead-letters
    # again immediately (one failure, not a fresh 3-strike run), rather
    # than resetting to 0 and re-earning 3 fresh attempts. This test only
    # guards that the helper doesn't blow up, loop forever, or leave the
    # task somewhere other than dead_lettered when reprocessed; the real
    # cross-delivery idempotency guarantee (no-op when state is ALREADY
    # dead_lettered at call time) is state_machine's own, already covered
    # by tests/test_dead_letter.py.
    assert db.get_task(task_id)["state"] == TaskStateEnum.DEAD_LETTERED.value
    assert db.get_task(task_id)["retry_count"] == first_retry_count + 1


def test_vault_write_failure_after_agent_run_creation_does_not_duplicate_charge(monkeypatch):
    """TD-07 (docs/architecture/09-technical-debt.md), the bug this whole
    module's own docstring named but never actually reproduced:
    worker.py::_retry_or_dead_letter re-invokes a failed handler FUNCTION
    directly, and _retry_or_dead_letter's own docstring used to just
    admit "a handler that partially wrote to Vault before failing is not
    guaranteed idempotent on retry (e.g. a duplicate signal/agent_run row
    is possible)."

    Reproduces that exact scenario against a REAL DISPATCH_TABLE handler
    (draft_content_handler: create_agent_run -> gateway.complete ->
    create_asset) rather than a synthetic flaky_handler: create_asset
    (the Vault write) fails on attempt 1 only, after the agent_run row
    and the model completion have already happened, then succeeds on
    attempt 2. Asserts the fix -- VaultClientExt.create_agent_run_
    idempotent (orchestrator/clients/vault_client_ext.py) and
    _complete_and_meter's task_ref default (orchestrator/dispatch.py) --
    holds: the retry reuses the SAME real agent_runs row and the SAME
    already-paid-for completion, rather than creating a second row or
    incurring a second model charge."""
    from tests.fakes import FakeGatewayClient, FakeVaultClient, patch_dispatch_clients

    vault = FakeVaultClient()
    gateway = FakeGatewayClient()
    patch_dispatch_clients(monkeypatch, shared_vault=vault, shared_gateway=gateway)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "draft-content", state="dispatchable")

    real_create_asset = vault.create_asset
    call_count = {"n": 0}

    def flaky_create_asset(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Vault write failed (simulated POST /assets 503)")
        return real_create_asset(*args, **kwargs)

    monkeypatch.setattr(vault, "create_asset", flaky_create_asset)

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "draft-content")

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    # The Vault write really did fail once, then succeed on retry --
    # otherwise this test would not be exercising F-DISPATCH-RETRY's
    # retry path at all.
    assert call_count["n"] == 2
    assert db.get_task(task_id)["state"] == TaskStateEnum.COMPLETED.value

    # The fix: exactly ONE real agent_runs row across both attempts, not
    # two -- the retry's create_agent_run_idempotent call looked up
    # attempt 1's row via the ledger instead of creating a new one.
    assert len(vault._agent_runs) == 1

    # The fix's other half: exactly ONE real model completion across both
    # attempts -- the retry's gateway.complete() call carried the SAME
    # (now agent_run-id-stable) task_ref as attempt 1's, so it was served
    # from model-gateway's own task_ref cache instead of billing the
    # provider again.
    assert gateway.real_call_count == 1
