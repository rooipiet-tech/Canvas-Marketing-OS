"""Vault persistence for the gateway (costs + gate_decisions).

Two implementations of the same ``Repository`` protocol:

* ``FakeRepository``  — in-memory, deterministic, zero I/O. Used by every
  test in ``tests/`` via FastAPI dependency override.
* ``PostgresRepository`` — real Postgres, backed by a single module-level
  ``psycopg2.pool.ThreadedConnectionPool`` (minconn=1, maxconn=10, sized for
  the Burstable B1ms instance's modest connection ceiling). Every method
  borrows/returns a connection inside try/finally and runs its blocking work
  through ``anyio.to_thread.run_sync`` so pooled I/O never blocks the event
  loop.

Latency note: the p95 gateway-overhead test (tests/test_overhead.py) drives
``FakeRepository`` exclusively, so its result measures gateway routing /
budget / redaction / metering / caching overhead only. Pooling here reduces
but does not eliminate real-Postgres latency risk, and the local p95 result
is not evidence about real-DB overhead — closing that gap needs live Azure
reach, which this build does not have.

Task_ref response caching is deliberately NOT part of this interface: it is
explicitly single-process-only (see caching.py) and has nothing to do with
Postgres.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

import config
from gate_decisions import INSERT_GATE_DECISION_SQL, FakeGateDecisionsStore

INSERT_COST_SQL = """
INSERT INTO costs (agent_run_id, provider, unit, amount)
VALUES (%s, %s, %s, %s)
RETURNING id
"""

SELECT_AGENT_NAME_SQL = "SELECT agent_name FROM agent_runs WHERE id = %s"

SELECT_DAILY_SPEND_SQL = """
SELECT COALESCE(SUM(c.amount), 0)
FROM costs c
JOIN agent_runs r ON r.id = c.agent_run_id
WHERE r.agent_name = %s
  AND c.unit = 'usd'
  AND c.incurred_at >= date_trunc('day', now())
"""


@runtime_checkable
class Repository(Protocol):
    """Everything the completion path needs from the Vault database."""

    async def insert_costs_rows(
        self,
        agent_run_id: str,
        provider: str,
        usd: float,
        tokens: int,
        ms: float,
    ) -> str:
        """Write the three per-completion cost rows; return the usd row id."""
        ...

    async def get_daily_spend(self, agent_name: str) -> float: ...

    async def get_agent_name(self, agent_run_id: str) -> str: ...

    async def insert_gate_decision(
        self,
        *,
        agent_run_id: str,
        decided_by: str,
        outcome: str,
        reason: str | None = None,
    ) -> str: ...


class FakeRepository:
    """In-memory repository used by tests (and by nothing in production)."""

    def __init__(self, default_agent_name: str = "test-agent") -> None:
        self.costs: list[dict[str, Any]] = []
        self.gate_decisions = FakeGateDecisionsStore()
        self.agent_names: dict[str, str] = {}
        self.daily_spend: dict[str, float] = {}
        self.default_agent_name = default_agent_name

    # -- seeding helpers (tests only) ---------------------------------
    def set_agent_name(self, agent_run_id: str, agent_name: str) -> None:
        self.agent_names[agent_run_id] = agent_name

    def set_daily_spend(self, agent_name: str, usd: float) -> None:
        self.daily_spend[agent_name] = float(usd)

    # -- Repository ---------------------------------------------------
    async def insert_costs_rows(
        self,
        agent_run_id: str,
        provider: str,
        usd: float,
        tokens: int,
        ms: float,
    ) -> str:
        if not agent_run_id:
            raise ValueError("costs.agent_run_id is NOT NULL — refusing to insert")
        rows = [
            {"unit": "usd", "amount": float(usd)},
            {"unit": "tokens", "amount": float(tokens)},
            {"unit": "ms", "amount": float(ms)},
        ]
        usd_row_id = ""
        for row in rows:
            row_id = str(uuid.uuid4())
            if row["unit"] == "usd":
                usd_row_id = row_id
            self.costs.append(
                {
                    "id": row_id,
                    "agent_run_id": agent_run_id,
                    "provider": provider,
                    "unit": row["unit"],
                    "amount": row["amount"],
                }
            )
        agent_name = await self.get_agent_name(agent_run_id)
        self.daily_spend[agent_name] = self.daily_spend.get(agent_name, 0.0) + float(usd)
        return usd_row_id

    async def get_daily_spend(self, agent_name: str) -> float:
        return float(self.daily_spend.get(agent_name, 0.0))

    async def get_agent_name(self, agent_run_id: str) -> str:
        return self.agent_names.get(agent_run_id, self.default_agent_name)

    async def insert_gate_decision(
        self,
        *,
        agent_run_id: str,
        decided_by: str,
        outcome: str,
        reason: str | None = None,
    ) -> str:
        return self.gate_decisions.insert(
            agent_run_id=agent_run_id,
            decided_by=decided_by,
            outcome=outcome,
            reason=reason,
        )


_pool = None


def _get_pool(dsn: str):
    """Lazily build the single module-level connection pool."""
    global _pool
    if _pool is None:
        from psycopg2.pool import ThreadedConnectionPool

        _pool = ThreadedConnectionPool(1, 10, dsn)
    return _pool


class PostgresRepository:
    """Real Vault repository, pooled and off-loaded to a worker thread."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or config.DATABASE_URL

    def _dsn_or_fail(self) -> str:
        if not self._dsn:
            raise RuntimeError("DATABASE_URL is not configured")
        return self._dsn

    def _execute(self, sql: str, params: tuple, fetch: bool = True):
        pool = _get_pool(self._dsn_or_fail())
        conn = pool.getconn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchone() if fetch else None
        finally:
            pool.putconn(conn)

    async def _run(self, sql: str, params: tuple, fetch: bool = True):
        import anyio

        return await anyio.to_thread.run_sync(lambda: self._execute(sql, params, fetch))

    async def insert_costs_rows(
        self,
        agent_run_id: str,
        provider: str,
        usd: float,
        tokens: int,
        ms: float,
    ) -> str:
        usd_row = await self._run(INSERT_COST_SQL, (agent_run_id, provider, "usd", usd))
        await self._run(INSERT_COST_SQL, (agent_run_id, provider, "tokens", tokens))
        await self._run(INSERT_COST_SQL, (agent_run_id, provider, "ms", ms))
        return str(usd_row[0]) if usd_row else ""

    async def get_daily_spend(self, agent_name: str) -> float:
        row = await self._run(SELECT_DAILY_SPEND_SQL, (agent_name,))
        return float(row[0]) if row and row[0] is not None else 0.0

    async def get_agent_name(self, agent_run_id: str) -> str:
        row = await self._run(SELECT_AGENT_NAME_SQL, (agent_run_id,))
        if not row:
            raise ValueError(f"no agent_runs row for id {agent_run_id!r}")
        return str(row[0])

    async def insert_gate_decision(
        self,
        *,
        agent_run_id: str,
        decided_by: str,
        outcome: str,
        reason: str | None = None,
    ) -> str:
        row = await self._run(
            INSERT_GATE_DECISION_SQL, (agent_run_id, decided_by, outcome, reason)
        )
        return str(row[0]) if row else ""


def get_repository() -> Repository:
    """FastAPI dependency. Tests override this with a FakeRepository."""
    return PostgresRepository()
