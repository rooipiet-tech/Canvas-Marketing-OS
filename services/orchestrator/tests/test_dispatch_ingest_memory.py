"""Tests for ingest-signals' cross-run memory (F-INGEST-NO-MEMORY).

Every scan started cold. The market-intelligence profile runs DAILY
against a THIRTY-day horizon, so one story stayed in-window -- and
eligible to be re-reported -- for up to thirty consecutive runs.
`vault_signal_lookup` was declared in function 09's tools.yaml from the
start and implemented nowhere, and dedupe-signal-cards, which would have
caught repeats downstream, is one of the seventeen no-ops.

The exclusion list is given to the model rather than applied as a hard
post-filter: schema.json requires at least 3 signals, so dropping repeats
could push an honest "nothing much happened" batch under the floor and
fail the scan for telling the truth. Repeats are counted and surfaced
instead. These tests pin both halves -- what the model is told, and what
gets measured.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

TOPIC = (
    "Microsoft Fabric adoption and multi-entity finance consolidation "
    "in South African enterprises"
)
OTHER_TOPIC = "Construction sector: contract-level ledgers and project accounting"

FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"


def _profile(**overrides: Any) -> dict[str, Any]:
    profile = {
        "profile_id": "market-intelligence",
        "topic": TOPIC,
        "horizon_days": 30,
        "min_sources": 2,
        "min_distinct_domains": 2,
        "source_chars": 8000,
        "urls": [FABRIC_URL, MONEYWEB_URL],
    }
    profile.update(overrides)
    return profile


def _batch(topic: str, headlines: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "topic": topic,
        "horizon_days": 30,
        "summary": "x" * 60,
        "signals": [
            {
                "headline": headline,
                "so_what": "why it matters",
                "source_url": url,
                "pillar": "Fabric-native",
                "confidence": "high",
            }
            for headline, url in headlines
        ],
    }


class _StubVault:
    """Only the surface _already_captured touches."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, raises: bool = False) -> None:
        self._rows = rows or []
        self._raises = raises
        self.limits: list[int] = []

    def list_signals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if self._raises:
            raise RuntimeError("vault unreachable (test)")
        self.limits.append(limit)
        return self._rows[:limit]


def _row(topic: str, headlines: list[tuple[str, str]], *, age_days: float = 1.0) -> dict[str, Any]:
    received = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "id": str(uuid.uuid4()),
        "signal_type": "market_signal_batch",
        "payload": _batch(topic, headlines),
        "received_at": received.isoformat(),
    }


# ---------------------------------------------------------------------
# What counts as already captured
# ---------------------------------------------------------------------


def test_recent_signals_for_this_profile_are_recalled():
    vault = _StubVault([_row(TOPIC, [("Fabric capacity tooling ships", FABRIC_URL)])])

    captured = dispatch._already_captured(vault, _profile())

    assert captured == [{"headline": "Fabric capacity tooling ships", "source_url": FABRIC_URL}]


def test_another_profiles_signals_are_not_recalled():
    """Matched on topic, which is unique per profile -- a construction scan
    must not suppress a Fabric headline."""
    vault = _StubVault([_row(OTHER_TOPIC, [("A construction group consolidates", MONEYWEB_URL)])])

    assert dispatch._already_captured(vault, _profile()) == []


def test_signals_older_than_the_horizon_are_not_recalled():
    """Past the horizon a story is legitimately new again -- that is what
    the horizon means."""
    vault = _StubVault([_row(TOPIC, [("Old news", FABRIC_URL)], age_days=45)])

    assert dispatch._already_captured(vault, _profile(horizon_days=30)) == []


def test_a_shorter_horizon_recalls_less():
    vault = _StubVault([_row(TOPIC, [("Nine days ago", FABRIC_URL)], age_days=9)])

    assert dispatch._already_captured(vault, _profile(horizon_days=30))
    assert dispatch._already_captured(vault, _profile(horizon_days=7)) == []


def test_non_market_signal_rows_are_ignored():
    row = _row(TOPIC, [("Something", FABRIC_URL)])
    row["signal_type"] = "some_other_signal_kind"

    assert dispatch._already_captured(_StubVault([row]), _profile()) == []


def test_an_unparseable_timestamp_is_treated_as_in_window():
    """Conservative on purpose: an undated row excludes rather than
    admits, and one bad row must not sink today's scan."""
    row = _row(TOPIC, [("Undated", FABRIC_URL)])
    row["received_at"] = "not-a-timestamp"

    assert dispatch._already_captured(_StubVault([row]), _profile())


def test_an_unreachable_vault_degrades_to_a_cold_scan(caplog):
    """Losing memory is worse than having it, but far better than failing
    a scan that could otherwise run."""
    with caplog.at_level("WARNING"):
        assert dispatch._already_captured(_StubVault(raises=True), _profile()) == []

    assert "ingest_signals_memory_unavailable" in caplog.text


def test_the_recall_list_is_capped():
    many = [(f"Headline {n}", f"https://example.com/{n}") for n in range(80)]
    vault = _StubVault([_row(TOPIC, many)])

    assert len(dispatch._already_captured(vault, _profile())) == (
        dispatch.RECENT_SIGNAL_HEADLINE_CAP
    )


# ---------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------


def test_captured_items_are_named_in_the_prompt_with_a_do_not_pad_instruction():
    content = dispatch._build_ingest_user_content(
        _profile(),
        [{"url": FABRIC_URL, "body": "evidence"}],
        [{"headline": "Fabric capacity tooling ships", "source_url": FABRIC_URL}],
    )

    assert "Already captured in this horizon" in content
    assert "Fabric capacity tooling ships" in content
    assert "do not pad" in content


def test_no_memory_means_no_exclusion_block_at_all():
    """A first-ever run must not be handed an empty, confusing section."""
    content = dispatch._build_ingest_user_content(
        _profile(), [{"url": FABRIC_URL, "body": "evidence"}], []
    )

    assert "Already captured" not in content
    assert "Retrieved evidence" in content


# ---------------------------------------------------------------------
# What gets measured
# ---------------------------------------------------------------------


def test_repeats_are_counted_by_url_and_by_headline():
    captured = [
        {"headline": "Fabric capacity tooling ships", "source_url": FABRIC_URL},
        {"headline": "A syndicated story", "source_url": "https://first-publisher.example/a"},
    ]
    output = _batch(
        TOPIC,
        [
            ("A different way of putting it", FABRIC_URL),  # same article, new wording
            ("a syndicated story", "https://second-publisher.example/b"),  # re-publication
            ("Genuinely new movement", MONEYWEB_URL),
        ],
    )

    assert dispatch._count_repeats(output, captured) == 2


def test_nothing_is_dropped_only_counted():
    """The batch the Vault receives is the batch the model returned --
    dropping repeats could push an honest scan under schema.json's
    three-signal floor."""
    captured = [{"headline": "Seen before", "source_url": FABRIC_URL}]
    output = _batch(TOPIC, [("Seen before", FABRIC_URL)] * 3)

    assert dispatch._count_repeats(output, captured) == 3
    assert len(output["signals"]) == 3


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def test_a_second_run_is_told_what_the_first_one_captured(clients, monkeypatch):
    """End to end against the shared Vault fake: run the handler twice and
    the second call's prompt carries the first run's headlines."""
    sent: list[str] = []

    from tests.fakes import FakeGatewayClient

    class _Recorder:
        def __init__(self) -> None:
            self._inner = FakeGatewayClient()

        def __enter__(self) -> "_Recorder":
            return self

        def __exit__(self, *_exc_info: object) -> None:
            pass

        def complete(self, **kw: Any) -> dict[str, Any]:
            sent.append(kw["user_content"])
            return self._inner.complete(**kw)

    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: _Recorder())

    for _ in range(2):
        db = FakeTaskDB()
        task_id = str(uuid.uuid4())
        db.seed(task_id, "ingest-signals")
        dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert "Already captured" not in sent[0]
    assert "Already captured" in sent[1]
    assert "Microsoft ships new Fabric capacity tooling" in sent[1]


def test_a_repeated_batch_completes_but_records_the_repeat_count(clients, monkeypatch, caplog):
    db = FakeTaskDB()
    first = str(uuid.uuid4())
    db.seed(first, "ingest-signals")
    dispatch.ingest_signals_handler(first, _envelope(first, "ingest-signals"), db)
    assert db.get_result_ref(first)["repeat_count"] == 0

    second = str(uuid.uuid4())
    db.seed(second, "ingest-signals")
    with caplog.at_level("WARNING"):
        dispatch.ingest_signals_handler(second, _envelope(second, "ingest-signals"), db)

    ref = db.get_result_ref(second)
    # The fake gateway returns the same canned batch every call, so the
    # second run is a total repeat -- exactly the situation this measures.
    assert ref["repeat_count"] == 3
    assert ref["already_captured_count"] == 3
    assert db.get_task(second)["state"] == "completed"
    assert "ingest_signals_repeats" in caplog.text
