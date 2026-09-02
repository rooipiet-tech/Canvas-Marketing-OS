"""A2 (O2) — alerting lives in the repository, not in a portal.

`infra/` contained no metricAlerts, no scheduledQueryRules and no action
groups. Nothing paged on anything. The architecture map treats
portal-side alerting as a finding in its own right, and rightly: an
alert that exists only in a portal cannot be reviewed in a diff,
restored after a subscription change, or even known about by someone
reading the code.

These guards are cheap and they pin the one property that matters --
that the rules are IN the template and REACHED by main.bicep. A rule
that exists in a module nothing instantiates is exactly as useful as no
rule at all, and the failure is silent.

They deliberately do not assert thresholds. Those are first guesses,
marked as such in the module, and tuning them is the expected outcome of
running with them for a while. A test that froze them would make the
tuning harder rather than safer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ALERTS_BICEP = REPO_ROOT / "infra/modules/monitoring/alerts.bicep"
MAIN_BICEP = REPO_ROOT / "infra/main.bicep"
ORCHESTRATOR_APP = REPO_ROOT / "infra/modules/orchestrator/container-app.bicep"


def test_the_alerts_module_exists():
    assert ALERTS_BICEP.exists(), (
        "infra/modules/monitoring/alerts.bicep is gone -- A2's whole point is that "
        "alerting is defined in IaC rather than configured portal-side"
    )


def test_main_bicep_actually_instantiates_the_alerts():
    """The silent failure this guards.

    A module nothing instantiates deploys nothing. Everything would look
    correct in review and no alert would exist.
    """
    source = MAIN_BICEP.read_text(encoding="utf-8")

    assert "modules/monitoring/alerts.bicep" in source
    assert "logAnalyticsWorkspaceId: containerAppsEnvironment.outputs.logAnalyticsWorkspaceId" in (
        source
    ), "the alert rules must be scoped to the workspace the Container Apps env streams to"


@pytest.mark.parametrize(
    "concern",
    [
        # The four A2 names, each tied to the hole it closes.
        "alert-cmos-loop-stalled",  # nothing completing -- the 10 Aug-2 Sep shape
        "alert-cmos-dead-letter",  # F6: DeadLetterAlert has no in-process consumer
        "alert-cmos-budget-breach",  # a safe refusal that nobody is told about
        "alert-cmos-qa-block-rate",  # writers or gate drifting
        # B1 (2 Sep 2026) chose this alert over buying out Buffer's
        # free-tier cap, so the rule IS the decision -- losing it
        # silently reverts the decision to "hope the queue drains".
        "alert-cmos-buffer-queue-depth",
    ],
)
def test_each_alert_rule_is_defined(concern: str):
    assert concern in ALERTS_BICEP.read_text(encoding="utf-8")


def test_every_rule_is_wired_to_the_action_group():
    """A rule with no action group is a rule that notifies nothing.

    It still evaluates and still records a firing history -- which is why
    an empty email address is an acceptable default -- but it must at
    least be attached, so enabling paging later is a parameter change
    rather than an infrastructure change.
    """
    source = ALERTS_BICEP.read_text(encoding="utf-8")

    rule_count = source.count("Microsoft.Insights/scheduledQueryRules")
    wiring_count = source.count("actionGroups: actionGroupIds")

    assert rule_count >= 4
    assert wiring_count == rule_count, (
        f"{rule_count} alert rule(s) but {wiring_count} wired to the action group"
    )


def test_the_readiness_probe_stays_on_health():
    """A2's most consequential non-change, pinned.

    /readiness reports strictly more than /health, which makes pointing
    the container's Readiness probe at it look like an obvious
    improvement. It is not: a failing Container Apps Readiness probe
    removes the replica from rotation, so ca-orchestrator would go out of
    service over a missing Teams webhook or a brief database blip -- an
    outage caused by the thing meant to report outages.
    """
    source = ORCHESTRATOR_APP.read_text(encoding="utf-8")

    assert "path: '/health'" in source
    assert "path: '/readiness'" not in source, (
        "the container probe must not point at /readiness -- see this test's docstring"
    )


def test_the_deployment_declares_what_it_expects():
    """O1: absence must be distinguishable from breakage.

    Without an expectation flag, a missing integration is indistinguish-
    able from one that was never meant to be there.
    """
    source = ORCHESTRATOR_APP.read_text(encoding="utf-8")

    for flag in (
        "CMOS_EXPECT_DATABASE",
        "CMOS_EXPECT_SERVICE_BUS",
        "CMOS_EXPECT_VAULT",
        "CMOS_EXPECT_APP_INSIGHTS",
        "CMOS_EXPECT_TEAMS",
    ):
        assert flag in source, f"{flag} is not declared on ca-orchestrator"
