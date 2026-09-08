"""Per-completion metering into the frozen Vault ``costs`` table.

Every successful completion writes exactly three rows against the same
agent_run_id — the table's ``unit`` column is free text, so no schema change
is needed to carry all three signals:

    unit = 'usd'     amount = computed provider cost
    unit = 'tokens'  amount = input_tokens + output_tokens
    unit = 'ms'      amount = measured provider + gateway latency

Never called on a cache hit: metering only runs inside completion.py's
compute closure, which the active Cache's get_or_compute (caching.py) skips
entirely when a task_ref has already been served — so a retried task never
double-spends, across replicas as well as within one (TD-06).
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Indicative USD price per million tokens, keyed by risk tier. Policy is
# data (policy/pricing.yaml), same convention as routing.yaml/budgets.yaml
# in this directory: refreshing a vendor's rate never touches this module.
PRICING_POLICY_PATH = Path(__file__).resolve().parent / "policy" / "pricing.yaml"

_price_per_mtok: dict[str, tuple[float, float]] | None = None
_default_price_per_mtok: tuple[float, float] | None = None


def _load_pricing() -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    global _price_per_mtok, _default_price_per_mtok
    if _price_per_mtok is None:
        document = yaml.safe_load(PRICING_POLICY_PATH.read_text(encoding="utf-8")) or {}
        tiers = document.get("tiers") or {}
        _price_per_mtok = {
            tier: (float(entry["input_usd_per_mtok"]), float(entry["output_usd_per_mtok"]))
            for tier, entry in tiers.items()
        }
        default = document.get("default") or {}
        _default_price_per_mtok = (
            float(default.get("input_usd_per_mtok", 3.0)),
            float(default.get("output_usd_per_mtok", 15.0)),
        )
    return _price_per_mtok, _default_price_per_mtok


def reset_pricing() -> None:
    """Drop the cached policy (test hook)."""
    global _price_per_mtok, _default_price_per_mtok
    _price_per_mtok = None
    _default_price_per_mtok = None


def estimate_usd(tier: str, input_tokens: int, output_tokens: int) -> float:
    """Compute the USD cost of one completion for a risk tier."""
    price_per_mtok, default_price = _load_pricing()
    input_price, output_price = price_per_mtok.get(tier, default_price)
    usd = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return round(usd, 6)


async def record_completion_costs(
    repo,
    agent_run_id: str,
    provider: str,
    usd: float,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
) -> str:
    """Write the three costs rows; return the usd row's id (the cost_id)."""
    return await repo.insert_costs_rows(
        agent_run_id=agent_run_id,
        provider=provider,
        usd=usd,
        tokens=int(input_tokens) + int(output_tokens),
        ms=float(latency_ms),
    )
