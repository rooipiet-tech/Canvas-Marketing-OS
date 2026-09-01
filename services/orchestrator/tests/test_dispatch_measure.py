"""Process 8, "measure": the two joins the measurement stage reads and
nothing in production ever wrote.

  * analytics.utm_campaign_map. reconcile_utm looks every ingested row's
    utm_campaign up in this table and quarantines anything it cannot match
    with `unmatched_utm_campaign`. The only INSERT anywhere in the
    repository was in tests/conftest.py, so in production the map was
    permanently empty and 100% of ingested Buffer/GA4/Search Console/
    LinkedIn rows quarantined. Measurement could not attribute anything to
    anything, ever -- and process 4 giving the whole week one coherent
    utm_campaign made no difference while nothing registered it.

  * analytics.scheduled_posts. rollup_publishing_reliability divides a
    channel's observed published_count by this table's scheduled_count and
    SKIPS any channel with no row, so with nothing writing it the KPI
    produced no rows at all. "Did we publish what we said we would"
    reported nothing rather than reporting a problem, which is the worse
    of the two failures.

Both are written at publication, because that is the one moment the slug,
the campaign and the asset are known together.
"""

from __future__ import annotations

import uuid

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope
from tests.test_dispatch_publish import _FakeGatekeeper, _FakePublisher, _run_sweep, _seed_approved


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


def test_publishing_registers_the_campaign_slug(clients, wired):
    """Without this row, every metric carrying the slug quarantines."""
    db = FakeTaskDB()
    campaign_id = str(uuid.uuid4())
    task_id = _seed_approved(db, clients, campaign="fabric-native", campaign_id=campaign_id)

    _run_sweep(db)

    assert "fabric-native" in db.utm_campaign_map
    mapped_campaign, mapped_asset = db.utm_campaign_map["fabric-native"]
    assert mapped_campaign == campaign_id
    assert mapped_asset == db.get_result_ref(task_id)["vault_asset_id"]


def test_publishing_records_the_reliability_denominator(clients, wired):
    db = FakeTaskDB()
    _seed_approved(db, clients, campaign="fabric-native")

    _run_sweep(db)

    # The channel vocabulary is analytics_ingest.rollup's own
    # _CHANNEL_TABLE; a value outside it makes the KPI skip the row.
    assert db.scheduled_posts == {"buffer": 1}


def test_a_weeks_six_assets_share_one_map_entry(clients, wired):
    """One slug per week is the point of deriving it from the pillar, so
    the second and later registrations are expected no-ops. The map must
    not thrash between assets, and the denominator must still count each
    post."""
    db = FakeTaskDB()
    first = _seed_approved(db, clients, campaign="fabric-native")
    _seed_approved(db, clients, campaign="fabric-native")
    _seed_approved(db, clients, campaign="fabric-native")

    _run_sweep(db)

    assert list(db.utm_campaign_map) == ["fabric-native"]
    assert db.utm_campaign_map["fabric-native"][1] == db.get_result_ref(first)["vault_asset_id"]
    assert db.scheduled_posts["buffer"] == 3


def test_an_asset_that_did_not_publish_registers_nothing(clients, monkeypatch):
    """A pending approval must not put a slug in the map: the map is what
    says "metrics carrying this tag belong to us", and nothing carrying it
    exists yet."""
    gatekeeper = _FakeGatekeeper(status="pending")
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    monkeypatch.setattr(dispatch, "build_publisher_client", lambda: _FakePublisher())
    db = FakeTaskDB()
    _seed_approved(db, clients, campaign="fabric-native")

    _run_sweep(db)

    assert db.utm_campaign_map == {}
    assert db.scheduled_posts == {}


def test_a_failed_registration_does_not_unpublish_a_live_post(clients, wired, monkeypatch):
    """The post is live. A raise here would mark a published asset
    unpublished and republish it on the next sweep -- far worse than a
    measurement gap the next publish under the same slug repairs."""
    _gatekeeper, publisher = wired
    db = FakeTaskDB()
    task_id = _seed_approved(db, clients, campaign="fabric-native")

    def _boom(*_a, **_k):
        raise RuntimeError("analytics schema unreachable")

    monkeypatch.setattr(db, "register_utm_campaign", _boom)

    sweep_id = _run_sweep(db)

    assert len(publisher.calls) == 1
    assert db.get_result_ref(sweep_id)["results"][0]["status"] == "published"
    assert db.get_result_ref(task_id)["publish_attempt_id"] == "attempt-1"
    # And it is not republished on the next sweep.
    _run_sweep(db)
    assert len(publisher.calls) == 1


def test_a_publish_with_no_slug_is_recorded_as_unregistered(clients, wired):
    """An asset published without a campaign tag cannot be attributed.
    That is a real gap and the row says so rather than implying success."""
    db = FakeTaskDB()
    task_id = _seed_approved(db, clients, campaign=None)

    _run_sweep(db)

    assert db.utm_campaign_map == {}
    assert db.get_result_ref(task_id)["utm_campaign_registered"] is False


def test_the_slug_reaching_publish_is_the_one_the_links_carry(clients, wired):
    """End to end, no hand-seeded rows between the draft and the map: the
    tag written into the published CTA links is the tag registered for
    attribution. If these two ever diverge, every metric quarantines while
    every check still passes."""
    from tests.test_dispatch_draft_contracts import _run_week

    db = FakeTaskDB()
    ids = _run_week(db)
    draft_id = ids["draft-insight-to-story"]
    drafted_slug = db.get_result_ref(draft_id)["campaign"]

    gate_ids = []
    for task_type in ("qa-review-brand-steward", "qa-review-fact-check"):
        gate_id = str(uuid.uuid4())
        db.seed(gate_id, task_type, depends_on=[draft_id])
        dispatch.DISPATCH_TABLE[task_type](gate_id, _envelope(gate_id, task_type), db)
        gate_ids.append(gate_id)

    friday_id = str(uuid.uuid4())
    db.seed(friday_id, "schedule-social-buffer", depends_on=gate_ids)
    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    assert db.get_result_ref(friday_id)["campaign"] == drafted_slug
    _run_sweep(db)
    assert drafted_slug in db.utm_campaign_map
