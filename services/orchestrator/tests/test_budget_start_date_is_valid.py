"""Guard for infra/modules/cost-management/budget.bicep's pinned dates.

Azure rejects a monthly budget whose start date is not the first of a month,
and (at create time) one that is before the current month. The pinned
`budgetStartDate` went stale because no deploy reached the budget resource
until two months after it was written (deploy-pipeline run 83).

This test is deliberately NOT clock-dependent. Whether an unchanged past
start date is accepted on a redeploy of an existing budget is unverified, so
failing CI every month would be a false alarm. It checks the shape Azure
requires, and that the end date follows the start date.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

BUDGET = (
    Path(__file__).resolve().parents[3]
    / "infra" / "modules" / "cost-management" / "budget.bicep"
)


def _default(name: str) -> str:
    text = BUDGET.read_text(encoding="utf-8")
    m = re.search(rf"^param\s+{name}\s+string\s*=\s*'([^']+)'", text, re.M)
    assert m, f"{name} default not found in {BUDGET}"
    return m.group(1)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _is_first_of_month_midnight(value: str) -> bool:
    d = _parse(value)
    return (d.day, d.hour, d.minute, d.second) == (1, 0, 0, 0)


def test_budget_start_date_is_first_of_a_month_at_midnight():
    assert _is_first_of_month_midnight(_default("budgetStartDate"))


def test_budget_end_date_is_after_start_and_first_of_a_month():
    start = _parse(_default("budgetStartDate"))
    end = _parse(_default("budgetEndDate"))
    assert end > start
    assert end.day == 1


def test_guard_can_fail():
    """Non-vacuity: the checker accepts a good date and rejects bad ones."""
    assert _is_first_of_month_midnight("2026-11-01T00:00:00Z")
    assert not _is_first_of_month_midnight("2026-11-15T00:00:00Z")
    assert not _is_first_of_month_midnight("2026-11-01T06:00:00Z")
