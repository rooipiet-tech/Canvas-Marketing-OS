"""TD-03 fix — vault/auth.py's `require_service_token` dependency.

Unit-tests the dependency function directly (no live Postgres/Key Vault
needed — the same reason test_telemetry_wiring.py avoids a live backend)
plus one ASGI-level check that a real router actually enforces it (via
httpx's ASGITransport, never touching the DB pool since the auth
dependency runs before the route body).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import httpx  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from vault import auth  # noqa: E402
from vault.config import get_settings  # noqa: E402
from vault.main import app  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VAULT_API_TOKEN", raising=False)
    monkeypatch.delenv("KEY_VAULT_NAME", raising=False)
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    get_settings.cache_clear()
    auth._reset_for_tests()
    yield
    get_settings.cache_clear()
    auth._reset_for_tests()


async def test_missing_authorization_header_rejected():
    with pytest.raises(HTTPException) as exc_info:
        await auth.require_service_token(authorization=None)
    assert exc_info.value.status_code == 401


async def test_malformed_scheme_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VAULT_API_TOKEN", "correct-token")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc_info:
        await auth.require_service_token(authorization="Token correct-token")
    assert exc_info.value.status_code == 401


async def test_wrong_token_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VAULT_API_TOKEN", "correct-token")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc_info:
        await auth.require_service_token(authorization="Bearer wrong-token")
    assert exc_info.value.status_code == 401


async def test_correct_token_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VAULT_API_TOKEN", "correct-token")
    get_settings.cache_clear()
    # Raises nothing == request allowed through.
    await auth.require_service_token(authorization="Bearer correct-token")


async def test_dev_default_fallback_used_and_logs_warning(caplog: pytest.LogCaptureFixture):
    # Nothing configured (env cleared by the autouse fixture, no Key Vault
    # name set) — falls back to the committed dev-only default, per
    # L-0041, with a runtime WARNING every time it's the resolved token.
    with caplog.at_level(logging.WARNING, logger="vault.auth"):
        await auth.require_service_token(authorization=f"Bearer {auth._DEV_DEFAULT_TOKEN}")
    assert any("dev-only default token" in record.message for record in caplog.records)


async def test_unauthenticated_request_to_a_real_router_gets_401():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://vault.test") as client:
        response = await client.get("/campaigns")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_health_never_requires_authentication():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://vault.test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
