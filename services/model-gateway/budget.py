"""Per-function (agent_name-keyed) daily budget enforcement.

Policy is data (policy/budgets.yaml). Two outcomes matter:

* soft breach — the request is never blocked; the resolved risk tier is
  downgraded one step (e.g. opus -> sonnet) so work keeps flowing at lower
  cost. "Downgrade, don't block" is the deliberate design choice here.
* hard breach — the daily limit is fully exhausted. This module reports the
  state; the caller (completion.py) records the escalation as a
  gate_decisions row and returns HTTP 429 with that row's id as the
  queued-task reference. The provider is never called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

BUDGET_POLICY_PATH = Path(__file__).resolve().parent / "policy" / "budgets.yaml"

BudgetState = Literal["ok", "soft_breach", "hard_breach"]

_policy: dict[str, Any] | None = None


def load_policy() -> dict[str, Any]:
    global _policy
    if _policy is None:
        _policy = yaml.safe_load(BUDGET_POLICY_PATH.read_text(encoding="utf-8")) or {}
    return _policy


def reset_policy() -> None:
    """Drop the cached policy (test hook)."""
    global _policy
    _policy = None


def limits_for(agent_name: str) -> tuple[float, float]:
    """Return (daily_usd_limit, soft_breach_ratio) for an agent_name."""
    policy = load_policy()
    defaults = policy.get("default") or {}
    override = (policy.get("agents") or {}).get(agent_name) or {}
    limit = float(override.get("daily_usd_limit", defaults.get("daily_usd_limit", 20.0)))
    ratio = float(override.get("soft_breach_ratio", defaults.get("soft_breach_ratio", 0.8)))
    return limit, ratio


def downgrade_tier(tier: str) -> str:
    """One step along the configured downgrade path."""
    path = load_policy().get("downgrade_path") or {}
    return str(path.get(tier, tier))


async def check_and_apply_budget(repo, agent_run_id: str, tier: str) -> tuple[str, BudgetState]:
    """Return (resolved_tier, budget_state) for this request."""
    agent_name = await repo.get_agent_name(agent_run_id)
    spend = float(await repo.get_daily_spend(agent_name))
    limit, ratio = limits_for(agent_name)

    if spend >= limit:
        # Hard breach: no further downgrade is offered; the caller escalates.
        return tier, "hard_breach"
    if spend >= limit * ratio:
        return downgrade_tier(tier), "soft_breach"
    return tier, "ok"
