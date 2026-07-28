"""Audit-write shape: append-only, non-null agent_run_id, no UPDATE path.

Covers the evidentiary invariants AC-34 states, at the unit level.
"""

from __future__ import annotations

import gate_decisions
import pytest
from conftest import AGENT_RUN_ID, run


def test_insert_is_append_only_and_returns_the_row_id(fake_repo):
    first = run(
        gate_decisions.insert_gate_decision(
            fake_repo,
            agent_run_id=AGENT_RUN_ID,
            decided_by=gate_decisions.REDACTION_GATE_DECIDER,
            outcome=gate_decisions.OUTCOME_REJECTED,
            reason="pattern matched",
        )
    )
    second = run(
        gate_decisions.insert_gate_decision(
            fake_repo,
            agent_run_id=AGENT_RUN_ID,
            decided_by=gate_decisions.BUDGET_GATE_DECIDER,
            outcome=gate_decisions.OUTCOME_ESCALATED,
            reason="daily budget exhausted",
        )
    )

    rows = fake_repo.gate_decisions.rows
    assert [r["id"] for r in rows] == [first, second]
    for row in rows:
        assert row["agent_run_id"] == AGENT_RUN_ID


def test_missing_agent_run_id_is_refused(fake_repo):
    with pytest.raises(ValueError):
        run(
            gate_decisions.insert_gate_decision(
                fake_repo,
                agent_run_id="",
                decided_by=gate_decisions.BUDGET_GATE_DECIDER,
                outcome=gate_decisions.OUTCOME_ESCALATED,
            )
        )
    assert fake_repo.gate_decisions.rows == []


def test_sql_is_insert_only():
    sql = gate_decisions.INSERT_GATE_DECISION_SQL.upper()
    assert "INSERT INTO GATE_DECISIONS" in sql
    assert "UPDATE" not in sql
