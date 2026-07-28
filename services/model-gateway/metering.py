"""Per-completion metering into the frozen Vault ``costs`` table.

Every successful completion writes exactly three rows against the same
agent_run_id — the table's ``unit`` column is free text, so no schema change
is needed to carry all three signals:

    unit = 'usd'     amount = computed provider cost
    unit = 'tokens'  amount = input_tokens + output_tokens
    unit = 'ms'      amount = measured provider + gateway latency

Never called on a cache hit: metering only runs inside completion.py's
compute closure, which caching.get_or_compute skips entirely when a
task_ref has already been served — so a retried task never double-spends.
"""

from __future__ import annotations

# Indicative USD price per million tokens, keyed by risk tier. Deliberately
# data in one place: refreshing prices never touches the metering logic.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # tier: (input_usd_per_mtok, output_usd_per_mtok)
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.8, 4.0),
}
DEFAULT_PRICE_PER_MTOK = (3.0, 15.0)


def estimate_usd(tier: str, input_tokens: int, output_tokens: int) -> float:
    """Compute the USD cost of one completion for a risk tier."""
    input_price, output_price = PRICE_PER_MTOK.get(tier, DEFAULT_PRICE_PER_MTOK)
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
