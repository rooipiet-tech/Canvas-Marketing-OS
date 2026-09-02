"""Tests for ingest-signals' redaction-firewall fallback
(F-INGEST-REDACTION, 4 Aug 2026, heartbeat round 14).

ingest-signals' user content is real fetched body text from live,
uncontrolled news sources (the active scan profile) -- unlike a static system
prompt, model-gateway's redaction firewall correctly scans it, and real
news text routinely contains a "full-name-like" span whether or not it's
actually PII. Before this fix, a single blocked source failed the WHOLE
ingest task. This proves the fallback: one blocked source is dropped and
the task still completes with what remains; every source blocked still
fails the task (never silently succeeds with zero evidence); a
GatewayClientError with a DIFFERENT (or no) error_code is never treated as
recoverable -- it must propagate unchanged, exactly as before this fix.

Uses tests/fakes.py's patch_dispatch_clients for the vault/gatekeeper
fakes (unchanged), but overrides build_gateway_client/build_mcp_web_client
directly in each test with small local doubles so the test controls
exactly which fetched source (if any) trips a simulated REDACTION_BLOCKED
response -- no changes to tests/fakes.py's shared fakes were needed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from orchestrator.clients.gateway_client import GatewayClientError
from tests.fakes import FIXTURE_SOURCE_BODY, FakeGatewayClient, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope


class _FixedBodyMCPClient:
    """Like tests/fakes.py's FakeMCPClient, but lets a test control the
    per-URL fetched body so specific sources can be made to trip the
    redaction firewall."""

    def __init__(self, bodies: dict[str, str]) -> None:
        self._bodies = bodies

    def __enter__(self) -> "_FixedBodyMCPClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        url = arguments.get("url")
        return {
            "source": "fixture",
            "url": url,
            "body": self._bodies.get(url, FIXTURE_SOURCE_BODY),
        }


class _RedactionBlockingGatewayClient:
    """Wraps FakeGatewayClient but raises a REDACTION_BLOCKED
    GatewayClientError (matching the real gateway_client.py's shape --
    status_code + error_code populated) whenever the request's user
    content contains one of the configured trigger markers. Delegates to
    the real FakeGatewayClient for anything else, so the eventual
    successful call still produces a schema-valid response."""

    def __init__(self, blocked_markers: set[str]) -> None:
        self._inner = FakeGatewayClient()
        self._blocked_markers = blocked_markers

    def __enter__(self) -> "_RedactionBlockingGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(
        self, *, model: str, system_prompt: str, user_content: str, agent_run_id: str, **kw: Any
    ) -> dict[str, Any]:
        if any(marker in user_content for marker in self._blocked_markers):
            raise GatewayClientError(
                "gateway returned HTTP 400 for /v1/completions: "
                '{"error":{"code":"REDACTION_BLOCKED","message":"blocked (test)"}}',
                status_code=400,
                error_code="REDACTION_BLOCKED",
            )
        return self._inner.complete(
            model=model,
            system_prompt=system_prompt,
            user_content=user_content,
            agent_run_id=agent_run_id,
            **kw,
        )


class _AlwaysErrorsGatewayClient:
    """Raises a GatewayClientError with a DIFFERENT error_code (or none)
    on every call -- proves the fallback does NOT treat arbitrary gateway
    failures as recoverable."""

    def __init__(self, *, error_code: str | None) -> None:
        self._error_code = error_code

    def __enter__(self) -> "_AlwaysErrorsGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **_kw: Any) -> dict[str, Any]:
        raise GatewayClientError(
            "gateway returned HTTP 500 for /v1/completions: internal error (test)",
            status_code=500,
            error_code=self._error_code,
        )


def _blocked_body(detail: str) -> str:
    """A body long enough to be evidence that also trips the firewall.

    The trigger leads so the assertion reads plainly; the bulk is the
    shared fixture prose, which is what keeps the source above
    DEFAULT_MIN_INGEST_SOURCE_CHARS and therefore counting toward the
    retrieval floor.
    """
    return f"PII_TRIGGER {detail} {FIXTURE_SOURCE_BODY}"


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def test_ingest_signals_skips_one_redaction_blocked_source_and_completes(clients, monkeypatch):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")

    sources = dispatch._resolve_scan_profile(dispatch.DEFAULT_SCAN_PROFILE_ID)
    blocked_url = sources["urls"][0]
    # A real page that trips the redaction firewall is a full article that
    # happens to name someone, not a 50-character stub -- and since
    # F-INGEST-CONTENT-FLOOR a stub would be dropped at retrieval and
    # never reach the firewall at all, which is not what this test is for.
    bodies = {blocked_url: _blocked_body("an article mentioning John Smith by name")}
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _FixedBodyMCPClient(bodies))
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _RedactionBlockingGatewayClient({"PII_TRIGGER"}),
    )

    dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["vault_signal_id"]


def test_ingest_signals_dead_letters_when_every_source_is_redaction_blocked(clients, monkeypatch):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")

    sources = dispatch._resolve_scan_profile(dispatch.DEFAULT_SCAN_PROFILE_ID)
    bodies = {url: _blocked_body(url) for url in sources["urls"]}
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _FixedBodyMCPClient(bodies))
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _RedactionBlockingGatewayClient({"PII_TRIGGER"}),
    )

    with pytest.raises(dispatch.DispatchError, match="redaction firewall"):
        dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    # Never silently marked completed with zero real evidence.
    assert db.get_task(task_id)["state"] != "completed"


class _RecordingGatewayClient:
    """Wraps FakeGatewayClient and records every kwarg each `complete()`
    call was made with, so a test can assert exactly which calls set
    `content_class` and to what value — without needing a real gateway."""

    def __init__(self) -> None:
        self._inner = FakeGatewayClient()
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_RecordingGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return self._inner.complete(**kw)


# F-INGEST-PUBLIC-SOURCE (4 Aug 2026, heartbeat round 15, Pieter's explicit
# ruling: "Only ingest-signals' articles" — see redaction.py's INCIDENT 2
# note and dispatch.py's _complete_ingest_with_redaction_fallback docstring
# for the full reasoning). Proves the narrow scope end-to-end: the ONE call
# site that should ever set content_class="public_source_content" actually
# does, and every fetched source in the request carries it consistently.


def test_ingest_signals_sets_public_source_content_class(clients, monkeypatch):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")

    recorder = _RecordingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: recorder)

    dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert db.get_task(task_id)["state"] == "completed"
    assert recorder.calls, "expected at least one gateway.complete() call"
    assert all(
        call.get("content_class") == "public_source_content" for call in recorder.calls
    ), "every ingest-signals completion call must be labelled public_source_content"


def test_ingest_signals_does_not_swallow_non_redaction_gateway_errors(clients, monkeypatch):
    """A gateway failure that is NOT a REDACTION_BLOCKED (wrong error_code,
    or none at all -- e.g. a real 500) must propagate unchanged, not get
    treated as a droppable source."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")

    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _AlwaysErrorsGatewayClient(error_code=None)
    )

    with pytest.raises(GatewayClientError):
        dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _AlwaysErrorsGatewayClient(error_code="SOME_OTHER_CODE"),
    )
    db2 = FakeTaskDB()
    task_id2 = str(uuid.uuid4())
    db2.seed(task_id2, "ingest-signals")
    with pytest.raises(GatewayClientError):
        dispatch.ingest_signals_handler(task_id2, _envelope(task_id2, "ingest-signals"), db2)
