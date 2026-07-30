"""Audit-row writes into the frozen Vault ``gate_decisions`` table.

Two automated gateway events are recorded here, both as evidentiary rows on
an existing frozen table (no schema change):

* redaction-firewall blocks  -> outcome ``rejected``
* budget hard-breach queues  -> outcome ``escalated``

Evidentiary shape (mirrors contracts/vault-schema/schema.sql's design notes):
INSERT-only, never UPDATE — ``gate_decisions`` deliberately has no
``updated_at`` column, and a re-decision is a new row referencing the same
``agent_run_id``. Every insert supplies a non-null ``agent_run_id`` FK and
relies on the table's own ``timestamptz`` defaults for ``decided_at`` /
``created_at``.

Queryability: these rows are read the same way a human reads any other Vault
row — ad hoc SQL through the existing ``caj-vault-query`` Container Apps Job.
A bare ``--env-vars QUERY=...`` does NOT work (it silently drops the job's
psql command and DB connection string); use the full ``--yaml`` override
documented in ``infra/modules/vault-query-job.bicep``'s header comment.
No dashboard, console, or UI component is introduced by this build.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

# Namespaced decided_by values for automated (non-human) decisions. Both
# callers import these constants rather than hand-typing the strings, so the
# two audit paths can never drift apart.
BUDGET_GATE_DECIDER = "system:model-gateway:budget-gate"
REDACTION_GATE_DECIDER = "system:model-gateway:redaction-firewall"

OUTCOME_REJECTED = "rejected"
OUTCOME_ESCALATED = "escalated"

# Append-only SQL. There is deliberately no UPDATE statement for this table
# anywhere in this service.
INSERT_GATE_DECISION_SQL = """
INSERT INTO gate_decisions (agent_run_id, decided_by, outcome, reason)
VALUES (%s, %s, %s, %s)
RETURNING id
"""


@dataclass
class FakeGateDecisionsStore:
    """In-memory, append-only stand-in for the ``gate_decisions`` table."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def insert(
        self,
        *,
        agent_run_id: str,
        decided_by: str,
        outcome: str,
        reason: str | None = None,
    ) -> str:
        if not agent_run_id:
            raise ValueError("gate_decisions.agent_run_id is NOT NULL — refusing to insert")
        row_id = str(uuid.uuid4())
        self.rows.append(
            {
                "id": row_id,
                "agent_run_id": agent_run_id,
                "decided_by": decided_by,
                "outcome": outcome,
                "reason": reason,
            }
        )
        return row_id


async def insert_gate_decision(
    repo: Any,
    agent_run_id: str,
    decided_by: str,
    outcome: str,
    reason: str | None = None,
) -> str:
    """Append one gate_decisions row and return its id.

    The returned id doubles as the queued-task reference handed back to the
    caller on a budget hard-breach (HTTP 429).
    """
    if not agent_run_id:
        raise ValueError("gate_decisions.agent_run_id is NOT NULL — refusing to insert")
    return await repo.insert_gate_decision(
        agent_run_id=agent_run_id,
        decided_by=decided_by,
        outcome=outcome,
        reason=reason,
    )
