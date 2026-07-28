"""Shared test fixtures for the model gateway.

Two deliberate design choices worth knowing before adding a test here:

1. Provider interception happens at the registry, not at any vendor adapter:
   the ``stub_provider`` fixture replaces ``registry.get_provider`` wholesale,
   so a test never names a vendor and never reaches the network, whatever
   routing.yaml resolves to.

2. The HTTP client is an in-process ASGI transport by default, but switches
   to a plain network client against ``GATEWAY_BASE_URL`` when that env var
   is set. That is what lets the contract test module be re-run against the
   real deployed endpoint (from inside the VNet) with no code change — only
   an environment variable.

Tests are written as plain synchronous functions that drive async calls
through the ``run()`` helper below, on one shared event loop. That keeps the
suite runnable with nothing but pytest installed, which matters because
several acceptance-criteria verify commands invoke ``pytest`` without
installing any async plugin first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx
import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import budget  # noqa: E402
import caching  # noqa: E402
import db  # noqa: E402
import routing  # noqa: E402
from main import app  # noqa: E402
from providers import registry  # noqa: E402
from providers.base import Provider, ProviderResult  # noqa: E402

REDACTION_RULES_PATH = REPO_ROOT / "contracts" / "model-gateway" / "redaction-rules.yaml"
OPENAPI_PATH = REPO_ROOT / "contracts" / "model-gateway" / "openapi.yaml"

AGENT_RUN_ID = "11111111-2222-3333-4444-555555555555"

_LOOP = asyncio.new_event_loop()


def run(awaitable):
    """Drive one awaitable to completion on the shared test event loop."""
    return _LOOP.run_until_complete(awaitable)


def completion_payload(model: str = "claude-sonnet", **overrides) -> dict:
    """A minimal, redaction-clean CompletionRequest body."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "write a short product tagline"}],
        "agent_run_id": AGENT_RUN_ID,
        "max_tokens": 64,
        "temperature": 0.2,
    }
    payload.update(overrides)
    return payload


class StubProvider(Provider):
    """In-memory provider: records calls, never touches the network."""

    def __init__(self, content: str = "stubbed completion text"):
        self.content = content
        self.call_count = 0
        self.calls: list[dict] = []
        self.last_call_kwargs: dict | None = None

    async def complete(self, **kwargs) -> ProviderResult:
        self.call_count += 1
        self.last_call_kwargs = kwargs
        self.calls.append(kwargs)
        return ProviderResult(content=self.content, input_tokens=12, output_tokens=8)


class GatewayClient:
    """Synchronous facade over an httpx.AsyncClient."""

    def __init__(self, client: httpx.AsyncClient):
        self.aclient = client

    def post(self, url: str, **kwargs):
        return run(self.aclient.post(url, **kwargs))

    def get(self, url: str, **kwargs):
        return run(self.aclient.get(url, **kwargs))


class GatewayLogCapture(logging.Handler):
    """Captures records from the 'model-gateway' logger itself.

    Deliberately NOT pytest's ``caplog``. main.py configures the
    'model-gateway' logger with its own handler and ``propagate = False`` so
    third-party libraries can never interleave plain-text lines into the JSON
    log stream (and so nothing attached to root can reformat or double-emit
    it) — which means records never reach caplog's root-mounted handler.

    Attaching here instead is also strictly the better test: it exercises the
    real logger the service configures, at the level the service configures
    it at, with no ``caplog.set_level`` forcing INFO on. If main.py ever
    stopped configuring the logger, these captures would go empty rather than
    silently keep passing.
    """

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)

    def json_lines(self) -> list[dict]:
        """Every captured message that parses as a JSON object."""
        parsed = []
        for record in self.records:
            try:
                parsed.append(json.loads(record.getMessage()))
            except (TypeError, ValueError):
                continue
        return parsed


@pytest.fixture
def gateway_log():
    """Capture what the 'model-gateway' logger actually emits."""
    logger = logging.getLogger("model-gateway")
    handler = GatewayLogCapture()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


@pytest.fixture
def stub_provider(monkeypatch) -> StubProvider:
    provider = StubProvider()
    monkeypatch.setattr(registry, "get_provider", lambda name: provider)
    return provider


@pytest.fixture
def fake_repo() -> db.FakeRepository:
    repo = db.FakeRepository()
    repo.set_agent_name(AGENT_RUN_ID, "test-agent")
    repo.set_daily_spend("test-agent", 0.0)
    return repo


@pytest.fixture
def app_client(fake_repo):
    base_url = os.environ.get("GATEWAY_BASE_URL")
    if base_url:
        # Real deployed endpoint (executed from inside the VNet). No ASGI
        # transport and no repository override — the deployed gateway owns
        # its own Postgres connection.
        client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
    else:
        app.dependency_overrides[db.get_repository] = lambda: fake_repo
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=30.0,
        )
    try:
        yield GatewayClient(client)
    finally:
        run(client.aclose())
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_gateway_state():
    """Keep registered stubs, added routes and cached responses per-test."""
    caching.clear()
    yield
    caching.clear()
    routing.reset_routes()
    budget.reset_policy()
    registry.reset_default_providers()
