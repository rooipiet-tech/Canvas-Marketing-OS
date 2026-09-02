"""Process 7, "schedule/publish": the step that did not exist.

Both content loops terminated at the approval request. request-approval,
schedule-social-buffer and publish-newsletter each raise an approval card
and complete, and no task in either graph depended on any of them.
ca-publisher -- deployed, with a real Buffer path, a gate-token verifier
and a JTI ledger -- was never called by anything, because the orchestrator
had no publisher client at all. The pipeline's last act was to ask a
human, and nothing consumed the answer.

The publish step runs on its own loop because request-approval completes
the instant /gate-check responds and never waits on the human, so by the
time anyone clicks Approve the run that asked is over. On its own
heartbeat, an approval granted three days late still publishes.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from orchestrator.clients.publisher_client import PublisherClientError
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

ASSET_TEXT = "One number everyone agrees on. https://www.canvasintelligence.com/insights"


class _FakeGatekeeper:
    """Approval status and the second /gate-check, which is the mint."""

    def __init__(self, *, status: str = "approved", gate_token: str | None = "tok-1") -> None:
        self._status = status
        self._gate_token = gate_token
        self.status_calls: list[dict[str, Any]] = []
        self.gate_check_calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeGatekeeper":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def get_approval_status(self, **kw: Any) -> dict[str, Any]:
        self.status_calls.append(kw)
        return {"status": self._status, "decided_by": "pieter@canvas", "decided_at": "now"}

    def gate_check(self, **kw: Any) -> dict[str, Any]:
        self.gate_check_calls.append(kw)
        return {
            "decision_id": str(uuid.uuid4()),
            "outcome": "approved" if self._gate_token else "blocked",
            "gate_token": self._gate_token,
        }


class _FakePublisher:
    def __init__(self, *, status: str = "published", reason: str = "published_dry_run") -> None:
        self._status = status
        self._reason = reason
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakePublisher":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def publish(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {
            "attempt_id": "attempt-1",
            "status": self._status,
            "reason": self._reason,
        }


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


@pytest.fixture()
def wired(monkeypatch, clients):
    gatekeeper = _FakeGatekeeper()
    publisher = _FakePublisher()
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    monkeypatch.setattr(dispatch, "build_publisher_client", lambda: publisher)
    return gatekeeper, publisher


def _seed_approved(
    db: FakeTaskDB,
    vault: Any,
    *,
    task_type: str = "schedule-social-buffer",
    function_id: str = dispatch.REAL_PUBLISH_FUNCTION_ID,
    text: str = ASSET_TEXT,
    **overrides: Any,
) -> str:
    """An approval task that completed having raised an approval."""
    task_id = str(uuid.uuid4())
    db.seed(task_id, task_type)
    asset = vault.create_asset(
        asset_type="linkedin_post",
        agent_run_id=None,
        campaign_id=None,
        function_id=dispatch.FUNCTION_ID_39,
        content_bytes=text.encode("utf-8"),
        approval_state="draft",
    )
    ref = {
        "approval_id": str(uuid.uuid4()),
        "decision_id": str(uuid.uuid4()),
        "outcome": "escalated",
        "content_hash": asset["content_hash"],
        "agent_run_id": str(uuid.uuid4()),
        "function_id": function_id,
        "subject": "Insight-to-story LinkedIn post",
        "vault_asset_id": asset["id"],
        "campaign": "fabric-native",
        "campaign_id": str(uuid.uuid4()),
    }
    ref.update(overrides)
    db.set_result_ref(task_id, ref)
    db.transition(task_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)
    return task_id


def _run_sweep(db: FakeTaskDB) -> str:
    sweep_id = str(uuid.uuid4())
    db.seed(sweep_id, "publish-approved-assets")
    dispatch.publish_approved_assets_handler(
        sweep_id, _envelope(sweep_id, "publish-approved-assets"), db
    )
    return sweep_id


def test_the_task_type_is_registered(clients):
    """It was not, so a publish task would have fallen through to
    legacy_task_pass_through and completed having done nothing."""
    assert "publish-approved-assets" in dispatch.DISPATCH_TABLE


def test_an_approved_asset_is_published(clients, wired):
    gatekeeper, publisher = wired
    db = FakeTaskDB()
    approval_id = _seed_approved(db, clients)

    sweep_id = _run_sweep(db)

    assert len(publisher.calls) == 1
    sent = publisher.calls[0]
    assert base64.b64decode(sent["asset_bytes_b64"]).decode("utf-8") == ASSET_TEXT
    assert sent["gate_token"] == "tok-1"
    ref = db.get_result_ref(sweep_id)
    assert ref["published"] == 1
    assert db.get_result_ref(approval_id)["publish_attempt_id"] == "attempt-1"


def test_the_second_gate_check_is_what_mints_the_token(clients, wired):
    """The first /gate-check escalated and returned no token; the second,
    after a human approved that exact triple, is the mint. It also
    re-evaluates the kill switch and the policy at publish time rather
    than trusting a decision made when the approval was raised."""
    gatekeeper, _publisher = wired
    db = FakeTaskDB()
    _seed_approved(db, clients)

    _run_sweep(db)

    assert len(gatekeeper.gate_check_calls) == 1
    minted = gatekeeper.gate_check_calls[0]
    status_read = gatekeeper.status_calls[0]
    # The same triple both times, or the Gatekeeper cannot match the
    # approved row and the token binds the wrong content.
    for key in ("agent_run_id", "function_id", "content_hash"):
        assert minted[key] == status_read[key]


def test_a_pending_approval_publishes_nothing_and_stays_selectable(clients, monkeypatch):
    """The ordinary case on most sweeps. It must not be an error, and the
    row must remain a candidate for the next heartbeat."""
    gatekeeper = _FakeGatekeeper(status="pending")
    publisher = _FakePublisher()
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    monkeypatch.setattr(dispatch, "build_publisher_client", lambda: publisher)
    db = FakeTaskDB()
    approval_id = _seed_approved(db, clients)

    sweep_id = _run_sweep(db)

    assert publisher.calls == []
    assert db.get_task(sweep_id)["state"] == "completed"
    assert db.get_result_ref(sweep_id)["results"][0]["status"] == "not_approved:pending"
    assert "publish_attempt_id" not in db.get_result_ref(approval_id)
    assert db.find_awaiting_publication(dispatch.PUBLISH_CANDIDATE_TASK_TYPES)


def test_a_rejected_approval_never_publishes(clients, monkeypatch):
    gatekeeper = _FakeGatekeeper(status="rejected")
    publisher = _FakePublisher()
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    monkeypatch.setattr(dispatch, "build_publisher_client", lambda: publisher)
    db = FakeTaskDB()
    _seed_approved(db, clients)

    _run_sweep(db)

    assert publisher.calls == []
    assert gatekeeper.gate_check_calls == []


def test_an_already_published_asset_is_not_published_twice(clients, wired):
    """publish_attempt_id on the row is what the query filters on. The
    publisher's single-use gate token is the second line of defence, not
    the first."""
    _gatekeeper, publisher = wired
    db = FakeTaskDB()
    _seed_approved(db, clients)

    _run_sweep(db)
    _run_sweep(db)

    assert len(publisher.calls) == 1


def test_the_newsletter_is_found_and_declined(clients, wired):
    """app/esp_client.py is a complete adapter nothing imports except its
    own test, POST /publish has no ESP branch, and no API key or list id
    exists in infra. Sending publish.blog_article to the publisher would
    put an email digest on LinkedIn, so it is declined on the row rather
    than routed to the wrong channel."""
    _gatekeeper, publisher = wired
    db = FakeTaskDB()
    _seed_approved(
        db,
        clients,
        task_type="publish-newsletter",
        function_id=dispatch.REAL_NEWSLETTER_FUNCTION_ID,
    )

    sweep_id = _run_sweep(db)

    assert publisher.calls == []
    result = db.get_result_ref(sweep_id)["results"][0]
    assert result["status"] == "no_publish_route"
    assert result["function_id"] == dispatch.REAL_NEWSLETTER_FUNCTION_ID


def test_one_bad_asset_does_not_stop_the_others(clients, wired):
    """Same reasoning the weekly loop's per-draft gating was rebuilt
    around in round 34: one failure must never cascade to a sibling."""
    _gatekeeper, publisher = wired
    db = FakeTaskDB()
    _seed_approved(db, clients, vault_asset_id=None)
    _seed_approved(db, clients)

    sweep_id = _run_sweep(db)

    assert len(publisher.calls) == 1
    statuses = {row["status"] for row in db.get_result_ref(sweep_id)["results"]}
    assert statuses == {"no_vault_asset_id", "published"}


def test_no_token_means_no_publish(clients, monkeypatch):
    """The policy re-evaluated to something other than approved at publish
    time -- a kill switch, a policy edit, an approval that expired between
    the status read and the mint. No token, no publish."""
    gatekeeper = _FakeGatekeeper(gate_token=None)
    publisher = _FakePublisher()
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    monkeypatch.setattr(dispatch, "build_publisher_client", lambda: publisher)
    db = FakeTaskDB()
    _seed_approved(db, clients)

    sweep_id = _run_sweep(db)

    assert publisher.calls == []
    assert db.get_result_ref(sweep_id)["results"][0]["status"] == "no_gate_token"


def test_an_empty_sweep_reaches_no_service(clients, monkeypatch):
    """Most heartbeats have nothing to do. That must cost no Vault,
    Gatekeeper or Publisher connection at all."""
    def _boom():
        raise AssertionError("no client should be built for an empty sweep")

    monkeypatch.setattr(dispatch, "build_vault_client", _boom)
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", _boom)
    monkeypatch.setattr(dispatch, "build_publisher_client", _boom)
    db = FakeTaskDB()

    sweep_id = _run_sweep(db)

    assert db.get_task(sweep_id)["state"] == "completed"
    assert db.get_result_ref(sweep_id) == {"candidates": 0, "published": 0, "results": []}


def test_a_publisher_outage_is_recorded_not_raised(clients, monkeypatch):
    """A sweep that dead-letters on one unreachable service would have to
    be re-driven by hand; a sweep that records the failure retries itself
    on the next heartbeat, because the row keeps no publish_attempt_id."""
    gatekeeper = _FakeGatekeeper()

    class _BrokenPublisher(_FakePublisher):
        def publish(self, **kw: Any) -> dict[str, Any]:
            # The real error type, not dispatch.PublisherClientError --
            # that attribute does not exist, so this test previously
            # passed by raising AttributeError into the same broad
            # handler and proved nothing about a publisher outage.
            raise PublisherClientError("POST /publish returned HTTP 503")

    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    monkeypatch.setattr(dispatch, "build_publisher_client", lambda: _BrokenPublisher())
    db = FakeTaskDB()
    approval_id = _seed_approved(db, clients)

    sweep_id = _run_sweep(db)

    assert db.get_task(sweep_id)["state"] == "completed"
    assert db.get_result_ref(sweep_id)["results"][0]["status"] == "error"
    assert "publish_attempt_id" not in db.get_result_ref(approval_id)
    assert db.find_awaiting_publication(dispatch.PUBLISH_CANDIDATE_TASK_TYPES)
