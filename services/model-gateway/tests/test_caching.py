"""AC-13 — an identical task_ref never double-calls or double-spends."""

from __future__ import annotations

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
