"""The ingest floors must look INSIDE the sources, not only count them.

F-INGEST-CONTENT-FLOOR.

WHAT WENT WRONG. deploy-mcp was evicted from the `cmos-dev-deploy`
concurrency group on every merge from 10 Aug to 2 Sep 2026, so ca-mcp-web
went 23 days without a deploy and kept serving a 176-byte synthetic
fixture as the body of EVERY fetch_url call. The daily scan therefore ran
against four copies of a stub.

Every guard in dispatch.py passed:

  * `fetch_url_failed` never fired -- the calls returned 200 with a body.
  * `_assert_ingest_floor` passed -- four URLs across three hostnames is
    comfortably over the 1/1 rollout floor and would clear the 2/2
    default too.
  * `ingest_signals_degraded` never fired -- nothing failed and nothing
    was redacted.

The only number that told the truth was `evidence_chars: 704`, which is
176 x 4, byte-exact -- and that field exists on the
`ingest_signals_output_rejected` diagnostic, which fires AFTER the model
call, on the failure path, and was added only once someone went looking.

The floors counted URLs and hostnames and never once looked at what came
back in them. That is the hole: a source that returns nothing is
indistinguishable, to a floor that counts sources, from one that returns
an article.

WHAT NOW HAPPENS. A source whose SHAPED body (feed items, or
de-marked-up page text -- the same shaping F-INGEST-EVIDENCE-WINDOW
introduced, so 8 KB of <channel> preamble does not qualify either) is
shorter than `min_source_chars` still reaches the model, but stops
counting toward `min_sources` and `min_distinct_domains`. So the scan
above now fails at retrieval, before any model spend, naming the four
URLs and their character counts.

WHY IT IS A FLOOR AND NOT A LOG LINE. A warning is what the system
already had: `ingest_signals_degraded` is a WARNING, and nobody read it
for three weeks, because a WARNING on a green pipeline is indistinguish-
able from background. The scan has to fail.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import FIXTURE_SOURCE_BODY, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

# The exact payload ca-mcp-web served for every URL between 10 Aug and
# 2 Sep 2026, reproduced at its real length. 176 bytes, four sources,
# `evidence_chars: 704`.
OUTAGE_FIXTURE_BODY = "x" * 176


class _BodyMCPClient:
    """Returns one fixed body for every URL, like the stale mcp-web did."""

    def __init__(self, body: str) -> None:
        self._body = body
        self.calls: list[str] = []

    def __enter__(self) -> "_BodyMCPClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def call_tool(self, _tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(arguments.get("url", ""))
        return {"source": "live", "url": arguments.get("url"), "body": self._body}


class _CountingGatewayClient:
    """Counts completions so a test can prove the floor failed BEFORE spend."""

    def __init__(self) -> None:
        from tests.fakes import FakeGatewayClient

        self._inner = FakeGatewayClient()
        self.calls = 0

    def __enter__(self) -> "_CountingGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls += 1
        return self._inner.complete(**kw)


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


# ---------------------------------------------------------------------
# The unit: which sources count
# ---------------------------------------------------------------------


def test_a_source_under_the_floor_is_not_counted_as_evidence():
    substantive, thin = dispatch._substantive_sources(
        [
            {"url": "https://a.example/feed", "body": "y" * 900},
            {"url": "https://b.example/feed", "body": OUTAGE_FIXTURE_BODY},
        ],
        500,
    )

    assert [item["url"] for item in substantive] == ["https://a.example/feed"]
    # The thin list carries the number, not just the fact -- diagnosis
    # from the failure message alone was the thing missing in August.
    assert thin == [{"url": "https://b.example/feed", "body_chars": 176}]


def test_a_missing_body_counts_as_zero_rather_than_raising():
    """fetch_url returning no body at all is a thin source, not a crash."""
    _, thin = dispatch._substantive_sources([{"url": "https://a.example", "body": None}], 500)

    assert thin == [{"url": "https://a.example", "body_chars": 0}]


def test_the_floor_is_read_from_the_profile_not_hardcoded():
    profile = dispatch._resolve_scan_profile(dispatch.DEFAULT_SCAN_PROFILE_ID)

    # Present in scan-profiles.yaml's defaults, so every profile inherits it.
    assert profile["min_source_chars"] == 500
    assert dispatch._ingest_min_source_chars(profile) == 500
    # A profile may override it; the code default applies only when absent.
    assert dispatch._ingest_min_source_chars({"min_source_chars": 50}) == 50
    assert dispatch._ingest_min_source_chars({}) == dispatch.DEFAULT_MIN_INGEST_SOURCE_CHARS


def test_the_default_floor_sits_clear_of_the_outage_fixture():
    """A regression guard on the CONSTANT, not on behaviour.

    Lowering this below 176 would silently restore the hole, and would do
    so in a one-line diff that looks like tuning.
    """
    assert dispatch.DEFAULT_MIN_INGEST_SOURCE_CHARS > len(OUTAGE_FIXTURE_BODY)


# ---------------------------------------------------------------------
# The regression: the live outage, end to end
# ---------------------------------------------------------------------


def test_the_three_week_fixture_outage_now_fails_at_retrieval(clients, monkeypatch):
    """The exact August shape: every URL returns 176 bytes.

    Before this change the scan proceeded to the model, which emitted an
    empty batch, which failed schema validation, which dead-lettered
    ingest-signals and cascaded to all 13 descendants -- with the reason
    recorded as `[] is too short`, an output problem, when the cause was
    that there had been no input.
    """
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")
    monkeypatch.setattr(
        dispatch, "build_mcp_web_client", lambda: _BodyMCPClient(OUTAGE_FIXTURE_BODY)
    )
    gateway = _CountingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    with pytest.raises(dispatch.DispatchError) as excinfo:
        dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    message = str(excinfo.value)
    # Fails at retrieval, naming the stage, the floor and the real counts.
    assert "retrieval" in message
    assert "0 source(s)" in message
    assert "500 characters" in message
    assert "'body_chars': 176" in message
    # And before any model spend, which is the point of checking here.
    assert gateway.calls == 0


def test_a_healthy_scan_is_unaffected_by_the_floor(clients, monkeypatch):
    """The floor must not narrow a normal morning.

    FIXTURE_SOURCE_BODY is ordinary evidence-length prose; a scan over it
    completes exactly as it did before.
    """
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")
    monkeypatch.setattr(
        dispatch, "build_mcp_web_client", lambda: _BodyMCPClient(FIXTURE_SOURCE_BODY)
    )

    dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert db.get_task(task_id)["state"] == "completed"
    assert db.get_result_ref(task_id)["vault_signal_id"]


def test_one_thin_source_among_healthy_ones_is_reported_but_does_not_fail(
    clients, monkeypatch, caplog
):
    """A partially-degraded scan is the case the warning exists for.

    The market-intelligence profile runs at 1/1 during its rollout, so
    three healthy sources plus one stub still clears the floor -- and
    that is precisely the shape the outage would have taken on a day it
    had one working source, so it must not pass silently.
    """
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")

    profile = dispatch._resolve_scan_profile(dispatch.DEFAULT_SCAN_PROFILE_ID)
    stub_url = profile["urls"][0]

    class _MixedMCPClient(_BodyMCPClient):
        def call_tool(self, _tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            url = arguments.get("url", "")
            body = OUTAGE_FIXTURE_BODY if url == stub_url else FIXTURE_SOURCE_BODY
            return {"source": "live", "url": url, "body": body}

    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _MixedMCPClient(""))

    with caplog.at_level("WARNING", logger="orchestrator.dispatch"):
        dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert db.get_task(task_id)["state"] == "completed"

    # log_event puts its fields in record.extra_fields, not in the
    # rendered message -- caplog.text carries only the event name.
    records = [r for r in caplog.records if r.msg == "ingest_source_below_content_floor"]
    assert records, "a thin source among healthy ones must still be reported"
    fields = records[0].extra_fields
    assert fields["stage"] == "retrieval"
    assert fields["min_source_chars"] == 500
    assert fields["thin_sources"] == [{"url": stub_url, "body_chars": 176}]


def test_the_eleven_scanner_fanout_path_has_the_same_floor():
    """The scanners share the floors but not the handler.

    dispatch.py has TWO fetch-and-floor blocks -- ingest_signals_handler
    for function 09, and the fan-out block the eleven scanners run
    through. The first version of this change fixed only the first, and
    the fan-out call site failed with a TypeError rather than quietly
    keeping the old behaviour, which is the only reason it was caught.
    Pinned here so a future signature change cannot re-open it.
    """
    import inspect

    source = inspect.getsource(dispatch)
    # Not a magic count: the invariant is that every block reading the
    # source/domain floors also reads the content floor. A new fetch path
    # that reads one and not the other is the exact regression.
    assert source.count("_ingest_min_source_chars(") == source.count("_ingest_floors("), (
        "a block reads _ingest_floors() without reading _ingest_min_source_chars() -- "
        "it would count sources it has never looked inside, which is what let a "
        "176-byte fixture pass for three weeks"
    )
    assert "min_source_chars" in inspect.getsource(dispatch._assert_ingest_floor)
