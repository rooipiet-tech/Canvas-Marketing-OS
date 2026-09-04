"""Decision store. Production: two vault tables (option_cards, approval_decisions)
with a FK from approval_decisions.card_id and from option_cards.lineage.agent_run_id
to a REAL agent_runs row - the PR #105 lesson. This module is the interface plus an
in-memory implementation for tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol


class DecisionStore(Protocol):
    def put_card(self, card: dict[str, Any]) -> None: ...
    def pending(self) -> list[dict[str, Any]]: ...
    def record(self, decision: dict[str, Any]) -> None: ...
    def decisions(self, *, since: datetime | None = None) -> list[dict[str, Any]]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._cards: dict[str, dict] = {}
        self._decisions: list[dict] = []

    def put_card(self, card: dict[str, Any]) -> None:
        self._cards[card["card_id"]] = card

    def pending(self) -> list[dict[str, Any]]:
        decided = {d["card_id"] for d in self._decisions}
        return [c for c in self._cards.values() if c["card_id"] not in decided]

    def record(self, decision: dict[str, Any]) -> None:
        card = self._cards[decision["card_id"]]
        if decision["outcome"] == "chosen":
            decision["was_recommended"] = (
                decision["chosen_option_id"] == card["recommended_option_id"]
            )
        decision.setdefault("decided_at", datetime.now(timezone.utc).isoformat())
        self._decisions.append(decision)

    def decisions(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        if since is None:
            return list(self._decisions)
        return [d for d in self._decisions if datetime.fromisoformat(d["decided_at"]) >= since]


def hit_rate(decisions: list[dict[str, Any]]) -> float:
    chosen = [d for d in decisions if d["outcome"] == "chosen"]
    return sum(1 for d in chosen if d.get("was_recommended")) / len(chosen) if chosen else 0.0


def rejection_all_rate(decisions: list[dict[str, Any]]) -> float:
    return (
        sum(1 for d in decisions if d["outcome"] == "rejected_all") / len(decisions)
        if decisions
        else 0.0
    )


SQL_DDL = """
CREATE TABLE IF NOT EXISTS option_cards (
  card_id UUID PRIMARY KEY,
  kind TEXT NOT NULL,
  autonomy_level SMALLINT NOT NULL,
  risk_tier TEXT NOT NULL,
  agent_run_id UUID REFERENCES agent_runs(id),
  produced_by_function SMALLINT NOT NULL,
  card JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_decisions (
  id BIGSERIAL PRIMARY KEY,
  card_id UUID NOT NULL REFERENCES option_cards(card_id),
  outcome TEXT NOT NULL,
  chosen_option_id CHAR(1),
  was_recommended BOOLEAN,
  rejection_code TEXT,
  decided_by TEXT NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL,
  latency_seconds INTEGER,
  signature TEXT NOT NULL,
  UNIQUE (card_id)          -- one decision per card; replay-rejected like gate_decisions
);
CREATE TABLE IF NOT EXISTS standing_permissions (
  permission_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  rule JSONB NOT NULL,
  granted_by TEXT, granted_at TIMESTAMPTZ, review_by DATE
);
"""
