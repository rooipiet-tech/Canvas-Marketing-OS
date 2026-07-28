"""AC-13 — an identical task_ref never double-calls or double-spends."""

from __future__ import annotations

import caching
from conftest import completion_payload

TASK_REF = "task-9f2c1a44-retry"


def test_identical_task_ref_calls_provider_once(app_client, fake_repo, stub_provider):
    payload = completion_payload(task_ref=TASK_REF)

    first = app_client.post("/v1/completions", json=payload)
    second = app_client.post("/v1/completions", json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    # The provider adapter is invoked exactly once across both requests.
    assert stub_provider.call_count == 1

    first_body, second_body = first.json(), second.json()
    assert first_body["content"] == second_body["content"]
    assert first_body["usage"] == second_body["usage"]
    assert first_body["cache_hit"] is False
    assert second_body["cache_hit"] is True

    # One completion's worth of costs rows, not two — a retried task_ref
    # never double-spends.
    assert len(fake_repo.costs) == 3


def test_distinct_task_refs_are_not_shared(app_client, fake_repo, stub_provider):
    app_client.post("/v1/completions", json=completion_payload(task_ref="task-a"))
    app_client.post("/v1/completions", json=completion_payload(task_ref="task-b"))

    assert stub_provider.call_count == 2
    assert len(fake_repo.costs) == 6


def test_completed_cache_is_bounded_and_evicts_oldest_first(
    app_client, fake_repo, stub_provider, monkeypatch
):
    """The cache must never grow once per distinct task_ref forever — the
    container is memory-capped, so old entries are evicted oldest-first."""
    monkeypatch.setattr(caching, "MAX_ENTRIES", 3)

    for index in range(5):
        response = app_client.post(
            "/v1/completions", json=completion_payload(task_ref=f"evict-{index}")
        )
        assert response.status_code == 200, response.text
        assert caching.size() <= 3

    assert caching.size() == 3
    # The two oldest are gone; the three most recent are retained.
    assert caching.get("evict-0") is None
    assert caching.get("evict-1") is None
    for index in (2, 3, 4):
        assert caching.get(f"evict-{index}") is not None

    # An evicted task_ref simply re-computes (correct, not a cache hit);
    # a retained one is still served from cache without a provider call.
    calls_so_far = stub_provider.call_count
    assert app_client.post(
        "/v1/completions", json=completion_payload(task_ref="evict-4")
    ).json()["cache_hit"] is True
    assert stub_provider.call_count == calls_so_far

    assert app_client.post(
        "/v1/completions", json=completion_payload(task_ref="evict-0")
    ).json()["cache_hit"] is False
    assert stub_provider.call_count == calls_so_far + 1


def test_in_flight_map_is_emptied_once_a_computation_finishes(
    app_client, fake_repo, stub_provider
):
    """_pending is bounded by concurrency only because entries are popped on
    both the success and the failure path — assert the success path here."""
    app_client.post("/v1/completions", json=completion_payload(task_ref="pending-1"))
    app_client.post("/v1/completions", json=completion_payload(task_ref="pending-2"))

    assert caching._pending == {}
