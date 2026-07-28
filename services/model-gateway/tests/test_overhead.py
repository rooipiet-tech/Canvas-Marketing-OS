"""AC-20 — p95 gateway-only overhead < 150ms at ~20 rps.

The provider adapter is stubbed and returns immediately, so the measured
latency is gateway work only: routing, redaction, budget, caching, metering
and the ASGI round trip. The repository is FakeRepository (zero I/O), which
means this number says nothing about real-Postgres latency — an accepted,
deliberate limit of a locally-runnable load test.
"""

from __future__ import annotations

import asyncio
import time

from conftest import completion_payload, run

TOTAL_REQUESTS = 120
BATCH_SIZE = 4
BATCH_INTERVAL_SECONDS = 0.2  # 4 requests per 200ms => ~20 rps


async def _timed_post(client, payload) -> float:
    started = time.perf_counter()
    response = await client.post("/v1/completions", json=payload)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert response.status_code == 200, response.text
    return elapsed_ms


async def _drive(client) -> list[float]:
    latencies: list[float] = []
    payload = completion_payload()
    for _ in range(TOTAL_REQUESTS // BATCH_SIZE):
        batch_started = time.perf_counter()
        latencies.extend(
            await asyncio.gather(*(_timed_post(client, payload) for _ in range(BATCH_SIZE)))
        )
        remaining = BATCH_INTERVAL_SECONDS - (time.perf_counter() - batch_started)
        if remaining > 0:
            await asyncio.sleep(remaining)
    return latencies


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * len(ordered))) - 1))
    return ordered[index]


def test_gateway_overhead_p95_under_150ms(app_client, fake_repo, stub_provider):
    latencies = run(_drive(app_client.aclient))

    assert len(latencies) >= 100
    p95_ms = _percentile(latencies, 0.95)
    mean_ms = sum(latencies) / len(latencies)
    print(
        f"gateway-only overhead over {len(latencies)} requests at ~20 rps: "
        f"p95={p95_ms:.1f}ms mean={mean_ms:.1f}ms max={max(latencies):.1f}ms"
    )

    assert p95_ms < 150, f"p95 gateway overhead {p95_ms:.1f}ms exceeds the 150ms budget"
