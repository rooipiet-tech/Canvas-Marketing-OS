"""Process 9, "report on cost and performance": the loop that did not exist.

infra/modules/scheduling/month-end-reporting-trigger.bicep has fired a
`month-end-reporting` heartbeat on the last day of every month since it
shipped, and no loop file carried that loop_id -- the trigger's own header
says so. worker.handle_heartbeat_message logs `heartbeat_unknown_loop` and
returns an empty list, so every month-end produced one warning line and
nothing else.

The report is deterministic: no model call, no function package. Every
figure is read back from what the other nine processes recorded. And every
section states whether its number means anything yet -- a report that
prints "0 posts published" while the publisher sits in dry-run is not a
measurement, it is a misdiagnosis that blames the marketing for a switch
nobody turned on.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

import pytest
import yaml
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

REPO_ROOT = Path(__file__).resolve().parents[3]
TRIGGER = REPO_ROOT / "infra/modules/scheduling/month-end-reporting-trigger.bicep"
LOOP = REPO_ROOT / "services/orchestrator/loops/month-end-reporting-loop.yaml"


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


@pytest.fixture()
def quiet_teams(monkeypatch):
    """Teams no-ops without a webhook in real life; pinned here so the
    test asserts on the report, not on the notifier."""
    from orchestrator import teams_notify

    monkeypatch.setattr(teams_notify, "notify_brief_ready", lambda **kw: False)


def _run(db: FakeTaskDB) -> str:
    task_id = str(uuid.uuid4())
    db.seed(task_id, "report-month-end")
    dispatch.report_month_end_handler(task_id, _envelope(task_id, "report-month-end"), db)
    return task_id


def _body(clients) -> str:
    briefs = list(clients._briefs.values())
    assert len(briefs) == 1
    return briefs[0]["body"]


# --- the wiring that was broken -------------------------------------------


def test_the_loop_id_matches_the_heartbeat_literal():
    """THE bug. loop_id is matched against the literal in the Logic App's
    heartbeat body, not against any filename, so the two halves can drift
    apart with nothing complaining -- which is exactly what happened, for
    every month since the trigger shipped. Renaming either half without
    the other silently returns the system to one warning a month."""
    fired = re.search(r"loop_id:\s*'([^']+)'", TRIGGER.read_text(encoding="utf-8"))
    assert fired, "could not find the heartbeat's loop_id in the trigger template"
    declared = yaml.safe_load(LOOP.read_text(encoding="utf-8"))["loop_id"]
    assert declared == fired.group(1)


def test_the_task_type_is_registered(clients):
    """A loop whose task_type is unregistered falls through to
    legacy_task_pass_through and completes having done nothing -- the same
    silence, one layer down."""
    task_type = yaml.safe_load(LOOP.read_text(encoding="utf-8"))["tasks"][0]["task_type"]
    assert task_type in dispatch.DISPATCH_TABLE


# --- the report ------------------------------------------------------------


def test_the_report_states_cost_delivery_and_performance(clients, quiet_teams):
    db = FakeTaskDB()
    db.report_costs = {
        "total": "12.5",
        "calls": 40,
        "by_provider": [{"provider": "anthropic", "amount": "12.5", "calls": 40}],
        "by_agent": [{"agent_name": "research-brief-writer", "amount": "12.5", "calls": 40}],
    }
    db.report_publishes = {"by_status": [{"status": "published", "count": 6}], "total": 6}
    db.report_kpis = {
        "engagement": [
            {
                "source": "buffer",
                "post_archetype": "carousel",
                "engagement_rate": "0.041000",
                "posts": 6,
            }
        ],
        "reliability": [{"channel": "buffer", "scheduled": 6, "published": 6}],
        "cost_per_accepted_asset": [],
    }
    db.report_attribution = {
        "quarantined_by_reason": [],
        "quarantined_total": 0,
        "registered_campaigns": 3,
    }

    task_id = _run(db)
    body = _body(clients)

    assert "$12.50" in body
    assert "research-brief-writer" in body
    assert "published: 6" in body
    assert "0.041000" in body
    assert "6 published of 6 scheduled" in body
    assert db.get_result_ref(task_id)["total_cost"] == "12.5"
    assert db.get_task(task_id)["state"] == "completed"


def test_a_dry_run_month_says_nothing_was_posted(clients, quiet_teams):
    """The caveat that matters most while the pipeline is young. Six
    "published" rows that were all dry-run would otherwise read as six
    posts that nobody engaged with."""
    db = FakeTaskDB()
    db.report_publishes = {
        "by_status": [{"status": "published_dry_run", "count": 6}],
        "total": 6,
    }

    _run(db)
    body = _body(clients)

    assert "NOTHING WAS ACTUALLY POSTED" in body
    assert "PUBLISHER_DRY_RUN" in body
    assert "configuration state, not a marketing result" in body


def test_a_live_month_drops_the_dry_run_caveat(clients, quiet_teams):
    """Derived, not hardcoded: the caveat has to disappear by itself when
    the condition clears, or it ages into a lie."""
    db = FakeTaskDB()
    db.report_publishes = {
        "by_status": [
            {"status": "published", "count": 4},
            {"status": "published_dry_run", "count": 1},
        ],
        "total": 5,
    }

    _run(db)

    assert "NOTHING WAS ACTUALLY POSTED" not in _body(clients)


def test_unattributed_metrics_are_reported_with_their_reason(clients, quiet_teams):
    """An unregistered campaign and a missing utm parameter are different
    failures with different fixes, so the report says which."""
    db = FakeTaskDB()
    db.report_attribution = {
        "quarantined_by_reason": [
            {"reason": "unmatched_utm_campaign", "rows": 118},
            {"reason": "missing_utm_campaign", "rows": 4},
        ],
        "quarantined_total": 122,
        "registered_campaigns": 2,
    }

    _run(db)
    body = _body(clients)

    assert "122 ingested metric row(s) could not be attributed" in body
    assert "118x unmatched_utm_campaign" in body
    assert "4x missing_utm_campaign" in body


def test_an_empty_map_is_called_out_as_the_cause(clients, quiet_teams):
    """Process 8's finding, surfaced where a person sees it: with nothing
    registered, no metric can match, so a zero engagement figure says
    nothing about the content."""
    db = FakeTaskDB()

    _run(db)
    body = _body(clients)

    assert "No campaign is registered in analytics.utm_campaign_map" in body
    assert "nothing published this month" in body
    assert "No model calls were metered this month" in body


def test_an_empty_map_after_a_publish_names_the_other_fault(clients, quiet_teams):
    """Two different faults wear the same empty map. Saying "nothing
    published yet" when six things did sends a reader to the wrong
    place -- which is the misdiagnosis this whole report exists to
    avoid."""
    db = FakeTaskDB()
    db.report_publishes = {"by_status": [{"status": "published", "count": 6}], "total": 6}

    _run(db)
    body = _body(clients)

    assert "even though 6 asset(s) published this month" in body
    assert "nothing published this month" not in body


def test_the_report_is_filed_and_recorded(clients, quiet_teams):
    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert ref["brief_id"]
    assert ref["month"] == f"{date.today():%Y-%m}"
    assert ref["caveats"] > 0
    assert ref["teams_notified"] is False


def test_no_model_is_called(clients, quiet_teams, monkeypatch):
    """Deterministic by construction. A report of numbers should not be a
    model's paraphrase of numbers."""
    def _boom():
        raise AssertionError("the month-end report must not call a model")

    monkeypatch.setattr(dispatch, "build_gateway_client", _boom)
    db = FakeTaskDB()

    _run(db)

    assert _body(clients)


# --- the window ------------------------------------------------------------


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 8, 31), (date(2026, 8, 1), date(2026, 9, 1))),
        (date(2026, 2, 28), (date(2026, 2, 1), date(2026, 3, 1))),
        (date(2026, 12, 31), (date(2026, 12, 1), date(2027, 1, 1))),
    ],
)
def test_the_month_window_including_the_december_rollover(today, expected):
    assert dispatch._month_window(today) == expected


def test_money_formatting_survives_a_missing_or_bad_total():
    """The total arrives as a string straight from SUM(...)::text, and an
    empty month can return something unparseable. A report that raises on
    its own cost line is worse than one that prints $0.00."""
    assert dispatch._fmt_money("2.000000") == "$2.00"
    assert dispatch._fmt_money(None) == "$0.00"
    assert dispatch._fmt_money("not-a-number") == "$0.00"
