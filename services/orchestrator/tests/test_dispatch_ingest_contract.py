"""Tests for ingest-signals' runtime contract enforcement (F-A, F-E).

Two gaps these cover, both of which previously let the daily scan report
success while producing something that could not satisfy its own contract:

  F-A  functions/09-market-intelligence-director/schema.json existed and
       was correct, but nothing validated against it at runtime -- the
       model's JSON was parsed and written straight to the Vault. The
       package's 5 golden evals score a deterministic MOCK, so they could
       never have caught a live model returning a short, unattributed or
       wrongly-tagged batch.
  F-E  a failed fetch was a warning and the redaction fallback dropped
       sources one at a time, so the task succeeded on ONE surviving
       source -- which structurally cannot satisfy prompt.md hard rule 3
       ("at least 2 distinct domains").

The floors are read from scan-profiles.yaml, so each test here states the
floors it relies on through a patched sources dict rather than depending
on whatever the checked-in YAML currently says.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from orchestrator.clients.gateway_client import GatewayClientError
from tests.fakes import FIXTURE_SOURCE_BODY, FakeGatewayClient, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"
MONEYWEB_TECH_URL = "https://www.moneyweb.co.za/news/tech/feed/"
BUSINESSTECH_URL = "https://businesstech.co.za/news/feed/"

ALL_URLS = [FABRIC_URL, MONEYWEB_URL, MONEYWEB_TECH_URL, BUSINESSTECH_URL]


def _sources(urls: list[str], *, min_sources: int = 2, min_domains: int = 2) -> dict[str, Any]:
    """A resolved scan profile -- defaults already merged, as
    _resolve_scan_profile returns one."""
    return {
        "profile_id": "test-profile",
        "function_id": "09-market-intelligence-director",
        "topic": "test topic",
        "horizon_days": 30,
        "min_sources": min_sources,
        "min_distinct_domains": min_domains,
        "source_chars": 8000,
        "urls": urls,
    }


def _fixed_profile(urls: list[str], **kw: Any):
    """Stands in for _resolve_scan_profile, which takes a profile_id."""
    return lambda _profile_id: _sources(urls, **kw)


class _SelectiveMCPClient:
    """fetch_url succeeds only for `ok_urls`; every other URL raises, the
    way a retired page or a timeout does in production."""

    def __init__(self, ok_urls: set[str]) -> None:
        self._ok_urls = ok_urls

    def __enter__(self) -> "_SelectiveMCPClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def call_tool(self, _tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        url = arguments.get("url", "")
        if url not in self._ok_urls:
            raise RuntimeError(f"fetch failed (test): {url}")
        return {"source": "fixture", "url": url, "body": FIXTURE_SOURCE_BODY}


class _CannedOutputGatewayClient:
    """Returns caller-supplied content as the model's reply, and counts
    calls so a test can prove a floor failure happened BEFORE model spend."""

    def __init__(self, output: Any) -> None:
        self._output = output
        self.calls = 0

    def __enter__(self) -> "_CannedOutputGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, *, agent_run_id: str, model: str = "claude-haiku", **_kw: Any) -> dict:
        self.calls += 1
        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": json.dumps(self._output),
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


def _valid_output(**overrides: Any) -> dict[str, Any]:
    output = {
        "topic": "test topic",
        "horizon_days": 30,
        "summary": (
            "Fabric adoption accelerated across South African enterprises in this "
            "30 day window, with consolidation tooling drawing renewed CFO attention."
        ),
        "signals": [
            {
                "headline": "Microsoft ships new Fabric capacity tooling",
                "so_what": "Lowers the cost floor for mid-market Fabric adoption",
                "source_url": FABRIC_URL,
                "pillar": "Fabric-native",
                "confidence": "high",
            },
            {
                "headline": "SA business press covers finance data consolidation",
                "so_what": "Confirms CFO appetite for one governed source of truth",
                "source_url": MONEYWEB_URL,
                "pillar": "Consolidation at scale",
                "confidence": "medium",
            },
            {
                "headline": "SA tech press covers analytics platform spend",
                "so_what": "Signals budget available for productised platforms",
                "source_url": BUSINESSTECH_URL,
                "pillar": "Productised speed",
                "confidence": "low",
            },
        ],
    }
    output.update(overrides)
    return output


def _run(db: FakeTaskDB, task_id: str) -> None:
    dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)


def _seeded() -> tuple[FakeTaskDB, str]:
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")
    return db, task_id


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


# ---------------------------------------------------------------------
# F-A — schema.json is the contract at runtime, not only in CI
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "output,expected_fragment",
    [
        pytest.param(
            _valid_output(signals=_valid_output()["signals"][:2]),
            "signals",
            id="fewer-than-three-signals",
        ),
        pytest.param(
            _valid_output(
                signals=[
                    {**_valid_output()["signals"][0], "source_url": "http://insecure.example.com"},
                    *_valid_output()["signals"][1:],
                ]
            ),
            "source_url",
            id="non-https-source-url",
        ),
        pytest.param(
            _valid_output(
                signals=[
                    {**_valid_output()["signals"][0], "pillar": "Made-up pillar"},
                    *_valid_output()["signals"][1:],
                ]
            ),
            "pillar",
            id="pillar-outside-the-enum",
        ),
        pytest.param(
            _valid_output(
                signals=[
                    {**_valid_output()["signals"][0], "confidence": "very high"},
                    *_valid_output()["signals"][1:],
                ]
            ),
            "confidence",
            id="confidence-outside-the-enum",
        ),
        pytest.param(
            {"topic": "test topic", "horizon_days": 30, "signals": []},
            "summary",
            id="missing-required-summary",
        ),
    ],
)
def test_schema_violating_output_fails_the_task(clients, monkeypatch, output, expected_fragment):
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CannedOutputGatewayClient(output)
    )

    with pytest.raises(dispatch.DispatchError) as exc:
        _run(db, task_id)

    assert "schema.json validation" in str(exc.value)
    assert expected_fragment in str(exc.value)
    # The whole point: no signals row, and never a silent COMPLETED.
    assert db.get_task(task_id)["state"] != "completed"
    assert not clients._signals


def test_valid_output_still_completes_and_records_scan_completeness(clients, monkeypatch):
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CannedOutputGatewayClient(_valid_output())
    )

    _run(db, task_id)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["vault_signal_id"]
    assert ref["sources_configured"] == 4
    assert ref["sources_used"] == 4


def test_single_domain_signal_batch_fails_prompt_hard_rule_three(clients, monkeypatch):
    """schema.json structurally cannot express "the set of hostnames across
    this array has at least 2 members", so a batch citing one domain three
    times validates cleanly and would otherwise reach the Vault looking
    like three corroborated signals."""
    db, task_id = _seeded()
    one_domain = _valid_output()
    for signal in one_domain["signals"]:
        signal["source_url"] = MONEYWEB_URL
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CannedOutputGatewayClient(one_domain)
    )

    with pytest.raises(dispatch.DispatchError, match="distinct domain"):
        _run(db, task_id)

    assert db.get_task(task_id)["state"] != "completed"
    assert not clients._signals


# ---------------------------------------------------------------------
# F-E — completeness floors, checked before and after model spend
# ---------------------------------------------------------------------


def test_retrieval_below_source_floor_fails_before_any_model_call(clients, monkeypatch):
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch, "build_mcp_web_client", lambda: _SelectiveMCPClient({FABRIC_URL})
    )
    gateway = _CannedOutputGatewayClient(_valid_output())
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    with pytest.raises(dispatch.DispatchError, match="retrieval"):
        _run(db, task_id)

    # Failing before spend is the point: one surviving source can never
    # satisfy the contract, so paying for the completion is pure waste.
    assert gateway.calls == 0
    assert db.get_task(task_id)["state"] != "completed"


def test_two_sources_on_one_host_fail_the_domain_floor(clients, monkeypatch):
    """The two moneyweb feeds are 2 sources but 1 domain — which is why the
    floor is checked on hostnames and not on source count alone."""
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch,
        "build_mcp_web_client",
        lambda: _SelectiveMCPClient({MONEYWEB_URL, MONEYWEB_TECH_URL}),
    )
    gateway = _CannedOutputGatewayClient(_valid_output())
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    with pytest.raises(dispatch.DispatchError, match="1 domain"):
        _run(db, task_id)

    assert gateway.calls == 0


def test_degraded_but_above_floor_still_completes(clients, monkeypatch, caplog):
    """Losing a source is a degraded scan, not a failed one — and the
    degradation is recorded rather than left to inference."""
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch,
        "build_mcp_web_client",
        lambda: _SelectiveMCPClient({FABRIC_URL, MONEYWEB_URL, BUSINESSTECH_URL}),
    )
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CannedOutputGatewayClient(_valid_output())
    )

    with caplog.at_level("WARNING"):
        _run(db, task_id)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["sources_configured"] == 4
    assert ref["sources_used"] == 3
    assert "ingest_signals_degraded" in caplog.text


def test_redaction_fallback_below_floor_fails_after_the_call(clients, monkeypatch):
    """The retrieval check can pass and the fallback can still drop the set
    below the floor, since it removes one source per REDACTION_BLOCKED
    retry — so the same floor is re-applied to what actually reached the
    model."""
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))

    class _BlockUntilOneLeft:
        def __init__(self) -> None:
            self._inner = FakeGatewayClient()

        def __enter__(self) -> "_BlockUntilOneLeft":
            return self

        def __exit__(self, *_exc_info: object) -> None:
            pass

        def complete(self, *, user_content: str, **kw: Any) -> dict[str, Any]:
            # Blocks while more than one source is still in the request.
            if sum(user_content.count(url) for url in ALL_URLS) > 1:
                raise GatewayClientError(
                    "gateway returned HTTP 400 for /v1/completions: REDACTION_BLOCKED",
                    status_code=400,
                    error_code="REDACTION_BLOCKED",
                )
            return self._inner.complete(user_content=user_content, **kw)

    monkeypatch.setattr(dispatch, "build_gateway_client", _BlockUntilOneLeft)

    with pytest.raises(dispatch.DispatchError, match="redaction fallback"):
        _run(db, task_id)

    assert db.get_task(task_id)["state"] != "completed"
    assert not clients._signals


def test_floors_come_from_scan_profiles_yaml_not_from_code(clients, monkeypatch):
    """Relaxing a floor is a reviewed YAML line, not a code change — so a
    1/1 configuration accepts a single-source scan that the checked-in
    2/2 configuration rejects."""
    db, task_id = _seeded()
    monkeypatch.setattr(
        dispatch,
        "_resolve_scan_profile",
        _fixed_profile(ALL_URLS, min_sources=1, min_domains=1),
    )
    monkeypatch.setattr(
        dispatch, "build_mcp_web_client", lambda: _SelectiveMCPClient({FABRIC_URL})
    )
    single_domain = _valid_output()
    for signal in single_domain["signals"]:
        signal["source_url"] = FABRIC_URL
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CannedOutputGatewayClient(single_domain)
    )

    _run(db, task_id)

    assert db.get_task(task_id)["state"] == "completed"
    assert db.get_result_ref(task_id)["sources_used"] == 1


def test_the_shipped_profile_resolves_both_floors_explicitly():
    """The floors must resolve to real numbers rather than falling through
    to a code default nobody reviewed — that drift is what this file exists
    to prevent. The VALUE is a rollout decision (market-intelligence runs
    at a temporary 1/1 for its first fortnight live; see the profile's own
    comment), so this asserts they are set and satisfiable, not what they
    are set to."""
    sources = dispatch._resolve_scan_profile(dispatch.DEFAULT_SCAN_PROFILE_ID)

    assert isinstance(sources["min_sources"], int)
    assert isinstance(sources["min_distinct_domains"], int)
    assert sources["min_sources"] >= 1
    # A floor the shipped source list could never satisfy would fail every
    # scan from the first morning.
    assert len(dispatch._distinct_domains(sources["urls"])) >= sources["min_distinct_domains"]


def test_the_code_defaults_stay_strict_so_an_absent_key_fails_closed():
    """A profile that omits the keys entirely must land on 2/2, not on
    whatever the currently-live profile happens to be relaxed to."""
    assert dispatch.DEFAULT_MIN_INGEST_SOURCES == 2
    assert dispatch.DEFAULT_MIN_INGEST_DOMAINS == 2
    assert dispatch._ingest_floors({}) == (2, 2)


# ---------------------------------------------------------------------
# The emitted-batch domain floor is capped by what retrieval delivered
# ---------------------------------------------------------------------
#
# This is what makes relaxing the RETRIEVAL floor a decision about how
# many sources a scan needs, rather than a quiet weakening of what the
# model is held to. Rule 3 stays fully enforced on any morning where it
# is satisfiable — which is the only kind of morning where enforcing it
# says anything at all.


def test_rule_three_is_enforced_in_full_when_retrieval_delivered_enough():
    """Two domains fetched, one cited: the model under-delivered against
    evidence it actually had, so this must fail whatever the configured
    floor is relaxed to."""
    output = _valid_output()
    for signal in output["signals"]:
        signal["source_url"] = FABRIC_URL

    with pytest.raises(dispatch.DispatchError, match="distinct domain"):
        dispatch._assert_signal_domain_floor(output, 2, 3)


def test_a_degraded_morning_is_not_punished_for_a_shortfall_it_could_not_avoid():
    """One domain fetched, one cited: the model cannot cite two domains
    when only one resolved, so the cap lets an honest batch through."""
    output = _valid_output()
    for signal in output["signals"]:
        signal["source_url"] = FABRIC_URL

    dispatch._assert_signal_domain_floor(output, 2, 1)  # must not raise


def test_the_cap_never_raises_the_floor_above_what_was_configured():
    """Plenty retrieved, a deliberately low configured floor: the config
    still governs."""
    output = _valid_output()
    for signal in output["signals"]:
        signal["source_url"] = FABRIC_URL

    dispatch._assert_signal_domain_floor(output, 1, 4)  # must not raise


def test_an_uncapped_call_still_enforces_the_configured_floor():
    """available_domains omitted keeps the original behaviour, so the cap
    is opt-in per call site rather than a silent global loosening."""
    output = _valid_output()
    for signal in output["signals"]:
        signal["source_url"] = FABRIC_URL

    with pytest.raises(dispatch.DispatchError, match="distinct domain"):
        dispatch._assert_signal_domain_floor(output, 2)


def _emitted_event(caplog, event: str) -> dict[str, Any] | None:
    """The structured fields logged for `event`, or None if never emitted.

    Read off the LogRecord rather than stdout: log_event passes its fields
    through extra={"extra_fields": ...}, so they live on the record and
    only reach text at the JSON formatter. caplog.text carries the bare
    message alone, and the handler binds sys.stdout at configure time so
    capsys cannot see the formatted line either.
    """
    for record in caplog.records:
        if record.getMessage() == event:
            return getattr(record, "extra_fields", {})
    return None


def test_a_rejected_output_says_what_the_scan_was_given(clients, monkeypatch, caplog):
    """F-INGEST-EMPTY-SCAN, live: deploy-loop-e2e-smoke #121 dead-lettered
    all 20 tasks in the daily loop on "at signals (1 violation(s)): [] is
    too short", and the log said nothing about what the scan had been
    handed. The only line carrying already_captured_count is
    ingest_signals_repeats, which runs AFTER validation and so never fired
    on this path.

    That gap matters because the two plausible causes want opposite fixes:
    thin retrieval (an evidence problem) versus the exclusion list
    crowding out everything the model would otherwise report (a memory
    problem). One says fix the sources, the other says fix the dedup.
    """
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _CannedOutputGatewayClient(
            {"topic": "test topic", "horizon_days": 30, "summary": "nothing new", "signals": []}
        ),
    )
    monkeypatch.setattr(
        dispatch,
        "_already_captured",
        lambda vault, sources, **kw: [
            {"headline": f"already known {n}", "source_url": FABRIC_URL} for n in range(37)
        ],
    )

    with caplog.at_level("ERROR"):
        with pytest.raises(dispatch.DispatchError):
            _run(db, task_id)

    line = _emitted_event(caplog, "ingest_signals_output_rejected")
    assert line is not None
    # The two numbers that separate the competing explanations.
    assert line["already_captured_count"] == 37
    assert line["emitted_signal_count"] == 0
    # And enough about retrieval to rule the evidence problem in or out.
    assert line["used_count"] == 4
    assert line["distinct_domain_count"] == 3
    assert line["evidence_chars"] > 0


def test_a_passing_output_stays_quiet(clients, monkeypatch, caplog):
    """The diagnostic above must not fire on the happy path -- a green run
    that logs an ERROR line trains people to ignore it."""
    db, task_id = _seeded()
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _fixed_profile(ALL_URLS))
    monkeypatch.setattr(
        dispatch, "build_gateway_client", lambda: _CannedOutputGatewayClient(_valid_output())
    )

    with caplog.at_level("ERROR"):
        _run(db, task_id)

    assert _emitted_event(caplog, "ingest_signals_output_rejected") is None
