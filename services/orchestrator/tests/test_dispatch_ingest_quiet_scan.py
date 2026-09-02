"""A quiet market day must not dead-letter the loop.

F-INGEST-QUIET-SCAN.

THE CONTRADICTION. Function 09 asked for two incompatible things:

    prompt.md hard rule 1   "Return **at least 3** signals and at most 8."
    schema.json             "signals": {"minItems": 3, "maxItems": 8}

    prompt.md hard rule 9   "Never pad the batch back up to the minimum
                             with items from that list, or with items you
                             cannot attribute -- a scan that honestly
                             found little is more useful than one that
                             restates last week."

    _build_ingest_user_content, on every run carrying an exclusion list:
                            "do not pad to reach the minimum"

On a day yielding fewer than three attributable NEW signals -- a slow
week, a public holiday, or simply a 30-day horizon on a daily loop where
most of what is in-window has already been captured -- the model had two
options and both were wrong. Pad, breaking rule 9 and, for anything
unattributed, rule 2. Or fall short, failing schema validation, which
dead-letters ingest-signals and cascades to all 13 descendants.

The system failed the scan for telling the truth. That is not a tuning
problem; the contract disagreed with itself.

WHAT CHANGED. minItems is 1, and hard rule 1 now asks for "3 to 8 on an
ordinary day" while explicitly deferring to rule 9. A short honest batch
writes its signals row and the loop runs. A batch under
INGEST_ORDINARY_SIGNAL_COUNT is logged as ingest_signals_quiet_scan at
WARNING, carrying the same evidence counts as the rejection diagnostic,
so "the market was quiet" stays checkable against what the scan was
given.

WHY THE ORDER MATTERED. Doing this alone would have been a regression.
The three-week ca-mcp-web fixture outage ALSO presented as an empty
batch -- the model was handed four copies of a 176-byte stub and
correctly found nothing in them. Relaxing the floor without a way to tell
"quiet market" from "broken retrieval" would have converted that loud
failure into a silent one, which is the exact confusion that cost three
weeks. F-INGEST-CONTENT-FLOOR landed first and fails a stub scan at
retrieval, before the model call, so a short batch reaching this point is
one whose evidence has already been shown to be real. The last test here
pins that ordering.

WHAT IS STILL A FAILURE. Zero. A scan that found nothing at all in
evidence that passed the content floor is not a quiet day being reported
honestly -- there is nothing to write and nothing downstream can cite.
That stays loud.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import FIXTURE_SOURCE_BODY, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

PROFILE = dispatch.DEFAULT_SCAN_PROFILE_ID
FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"


def _signal(headline: str, url: str, pillar: str = "Fabric-native") -> dict[str, Any]:
    return {
        "headline": headline,
        "so_what": "Moves the buyer conversation this profile watches",
        "source_url": url,
        "pillar": pillar,
        "confidence": "medium",
    }


def _batch(signals: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "topic": (
            "Microsoft Fabric adoption and multi-entity finance consolidation "
            "in South African enterprises"
        ),
        "horizon_days": 30,
        "summary": (
            "A quiet window: little moved inside the 30 days this scan covers, "
            "and what did is below."
        ),
        "signals": signals,
    }


class _CannedGateway:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls = 0

    def __enter__(self) -> "_CannedGateway":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, *, agent_run_id: str, model: str = "claude-haiku", **_kw: Any):
        self.calls += 1
        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": json.dumps(self._payload),
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


class _HealthyMCPClient:
    def __enter__(self) -> "_HealthyMCPClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def call_tool(self, _tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"source": "live", "url": arguments.get("url"), "body": FIXTURE_SOURCE_BODY}


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


@pytest.fixture()
def healthy(monkeypatch, clients):
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _HealthyMCPClient())
    return clients


def _run(monkeypatch, payload: dict[str, Any]) -> tuple[FakeTaskDB, str]:
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: _CannedGateway(payload))
    dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)
    return db, task_id


# ---------------------------------------------------------------------
# The contract no longer contradicts itself
# ---------------------------------------------------------------------


def test_the_schema_no_longer_demands_a_count_the_prompt_forbids_padding_to():
    schema = dispatch._load_function_output_schema(dispatch.FUNCTION_ID_09)
    signals = schema["properties"]["signals"]

    assert signals["minItems"] == 0, (
        "any minimum re-creates the contradiction: hard rule 9 forbids padding to "
        "reach one, so a floor of N leaves a scan that honestly found fewer than N "
        "no legal answer. minItems 1 fixed the short-batch case and left the ZERO "
        "case broken, which is what dead-lettered deploy run 9 and cascaded to ~20 "
        "descendants"
    )
    # The ceiling is a real editorial limit and stays.
    assert signals["maxItems"] == 8


def test_the_prompt_asks_for_three_without_demanding_it():
    prompt = dispatch._read_prompt("09-market-intelligence-director")

    # Rule 9 is the constraint the minimum used to fight; it must survive.
    assert "Never" in prompt and "pad the batch back up to the minimum" in prompt
    # And rule 1 must no longer be phrased as a hard floor.
    assert "at least 3" not in prompt
    assert "3 to 8 on an ordinary day" in prompt
    # And zero must be stated as legal, not merely left unforbidden: the
    # model reads this prompt, and "return the ones you have" alone does
    # not tell it that having none is an acceptable answer.
    assert "Zero is also a correct answer" in prompt


def test_the_warning_threshold_matches_what_the_prompt_asks_for():
    """A drifting pair here is how the contradiction returns."""
    assert dispatch.INGEST_ORDINARY_SIGNAL_COUNT == 3
    assert "3 to 8" in dispatch._read_prompt("09-market-intelligence-director")


# ---------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------


def test_a_two_signal_scan_completes_and_is_reported_as_quiet(healthy, monkeypatch, caplog):
    payload = _batch(
        [
            _signal("Fabric capacity tooling shipped", FABRIC_URL),
            _signal("CFO survey on reconciliation cycles", MONEYWEB_URL, "Consolidation at scale"),
        ]
    )

    with caplog.at_level("WARNING", logger="orchestrator.dispatch"):
        db, task_id = _run(monkeypatch, payload)

    assert db.get_task(task_id)["state"] == "completed"
    # The signals row is written: downstream has something real to cite.
    assert db.get_result_ref(task_id)["vault_signal_id"]

    # log_event puts fields on record.extra_fields, not in the message.
    records = [r for r in caplog.records if r.msg == "ingest_signals_quiet_scan"]
    assert records, "a short batch must be reported, not passed over in silence"
    fields = records[0].extra_fields
    assert fields["emitted_signal_count"] == 2
    assert fields["ordinary_signal_count"] == 3
    assert fields["profile_id"] == PROFILE
    # The evidence counts are the point: they are what makes "quiet"
    # checkable rather than something to be taken on trust.
    assert fields["used_count"] >= 1
    assert fields["evidence_chars"] > 0


def test_a_single_signal_scan_is_still_a_valid_answer(healthy, monkeypatch):
    """The floor is 1, not 2 -- one attributable signal is a scan."""
    db, task_id = _run(monkeypatch, _batch([_signal("One real move", FABRIC_URL)]))

    assert db.get_task(task_id)["state"] == "completed"
    assert db.get_result_ref(task_id)["vault_signal_id"]


def test_an_ordinary_three_signal_scan_is_not_reported_as_quiet(healthy, monkeypatch, caplog):
    payload = _batch(
        [
            _signal("Fabric capacity tooling shipped", FABRIC_URL),
            _signal("CFO survey on reconciliation", MONEYWEB_URL, "Consolidation at scale"),
            _signal(
                "Consolidation tender scored group reporting",
                MONEYWEB_URL,
                "Finance-grade trust",
            ),
        ]
    )

    with caplog.at_level("WARNING", logger="orchestrator.dispatch"):
        db, task_id = _run(monkeypatch, payload)

    assert db.get_task(task_id)["state"] == "completed"
    assert not [r for r in caplog.records if r.msg == "ingest_signals_quiet_scan"], (
        "an ordinary day must not warn -- a warning that fires most mornings is "
        "the background noise this whole change exists to avoid producing"
    )


def test_an_empty_batch_completes_and_marks_itself_quiet(healthy, monkeypatch, caplog):
    """Zero IS a quiet day reported honestly.

    This test asserted the opposite until deploy run 9 showed what the
    old behaviour cost: 3 of 4 sources already captured, `[] should be
    non-empty`, three retries, dead-lettered, and ~20 descendants
    cascade-dead-lettered -- including eleven fan-out scanners that never
    read this batch at all.

    Completing is what keeps those eleven running. The quiet marker is
    what stops the brief chain from each stage discovering the empty
    batch for itself.
    """
    with caplog.at_level("WARNING", logger="orchestrator.dispatch"):
        db, task_id = _run(monkeypatch, _batch([]))

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == dispatch.QUIET_SCAN_STATUS
    assert ref["signal_count"] == 0
    # The signals row is still written, so the day is auditable rather
    # than absent from the Vault.
    assert ref["vault_signal_id"]
    # Quiet is a claim about the market; it stays checkable against the
    # evidence counts that produced it.
    assert "ingest_signals_quiet_scan" in caplog.text


def test_a_short_batch_is_not_marked_quiet(healthy, monkeypatch):
    """The marker means ZERO, not "fewer than ordinary".

    A short batch is a normal completed scan and must stay
    indistinguishable from any other one downstream -- if it carried the
    marker, two real signals would silently skip the brief.
    """
    payload = _batch([_signal("Fabric capacity tooling shipped", FABRIC_URL)])
    db, task_id = _run(monkeypatch, payload)

    ref = db.get_result_ref(task_id)
    assert ref.get("status") != dispatch.QUIET_SCAN_STATUS
    assert ref["signal_count"] == 1


# ---------------------------------------------------------------------
# The ordering that makes the relaxation safe
# ---------------------------------------------------------------------


def test_a_quiet_batch_on_stub_evidence_never_reaches_this_path(clients, monkeypatch):
    """The regression this change could have introduced, pinned.

    The three-week fixture outage produced an empty batch from four
    176-byte stubs -- indistinguishable, at THIS point in the handler,
    from a genuinely quiet market. It never gets here: the content floor
    fails the scan at retrieval, before the model is called at all. So a
    short batch arriving at the quiet-scan branch is always one built on
    evidence that has already been shown to be real.
    """
    from tests.test_dispatch_ingest_content_floor import OUTAGE_FIXTURE_BODY

    class _StubMCP:
        def __enter__(self):
            return self

        def __exit__(self, *_exc_info: object) -> None:
            pass

        def call_tool(self, _tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return {"source": "live", "url": arguments.get("url"), "body": OUTAGE_FIXTURE_BODY}

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _StubMCP())
    gateway = _CannedGateway(_batch([_signal("Would have looked quiet", FABRIC_URL)]))
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    with pytest.raises(dispatch.DispatchError, match="characters of evidence"):
        dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert gateway.calls == 0, "the model was called on stub evidence"
