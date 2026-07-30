"""L-0026 — startup check: routing.yaml's provider_model values against the
provider's own live model list.

Exercises main._validate_routing_against_live_models() directly rather than
through app_client/ASGITransport: FastAPI's lifespan only fires through a
real ASGI lifespan protocol driver (uvicorn in production), and
conftest.py's app_client fixture talks to the app over a bare
httpx.ASGITransport, which never sends lifespan events at all — calling the
function directly is the honest way to test it under this suite's existing
transport choice, not a workaround.
"""

from __future__ import annotations

import main
import pytest
import routing
from conftest import run


@pytest.fixture(autouse=True)
def _stub_anthropic_list_model_ids(monkeypatch):
    """Default: no network call. Individual tests override the return value."""

    async def _default(self):
        return {r.provider_model for r in (routing.resolve(m) for m in routing.known_models())}

    monkeypatch.setattr(main.AnthropicProvider, "list_model_ids", _default)


def test_all_configured_models_present_logs_nothing(gateway_log):
    run(main._validate_routing_against_live_models())

    assert gateway_log.json_lines() == []


def test_a_retired_model_logs_one_clear_error_per_route(monkeypatch, gateway_log):
    live_ids = {"claude-opus-4-8", "claude-sonnet-4-6"}  # haiku's id missing

    async def _list_model_ids(self):
        return live_ids

    monkeypatch.setattr(main.AnthropicProvider, "list_model_ids", _list_model_ids)

    run(main._validate_routing_against_live_models())

    events = [line for line in gateway_log.json_lines() if line.get("event") == "routing_model_retired"]
    assert len(events) == 1
    assert events[0]["model"] == "claude-haiku"
    assert events[0]["provider_model"] not in live_ids
    # DR-5-style discipline: names what's wrong, not a caller's data — there
    # is none here, but the message should still be self-contained and
    # actionable without needing to read this test to understand it.
    assert "routing.yaml" in events[0]["message"]


def test_provider_unreachable_logs_a_warning_and_never_raises(monkeypatch, gateway_log):
    async def _list_model_ids(self):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main.AnthropicProvider, "list_model_ids", _list_model_ids)

    run(main._validate_routing_against_live_models())  # must not raise

    events = gateway_log.json_lines()
    assert len(events) == 1
    assert events[0]["event"] == "routing_model_validation_skipped"
