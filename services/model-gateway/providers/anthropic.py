"""Anthropic Messages API adapter.

Uses httpx directly rather than the vendor SDK: httpx is already a required
dependency (the ASGI/contract tests need it) and exactly one endpoint is
called, so pulling in an extra SDK would not earn its keep in this build.

The API key is read from the process environment. In Azure it is projected
there by the Container App's native Key Vault secret reference (see
infra/modules/gateway.bicep) — the gateway never calls the vault data plane
itself, which is what keeps the vault's public network access disabled.
"""

from __future__ import annotations

import os

import httpx

from providers.base import Provider, ProviderResult

API_KEY_ENV = "ANTHROPIC_API_KEY"
API_URL = "https://api.anthropic.com/v1/messages"
MODELS_URL = "https://api.anthropic.com/v1/models"
API_VERSION = "2023-06-01"
DEFAULT_TIMEOUT_SECONDS = 60.0


class AnthropicProvider(Provider):
    """Adapter for Anthropic's hosted Claude models."""

    # Exposed on the class so provider-agnostic callers (live_smoke.py) can
    # ask an adapter which environment variable carries its credential
    # without naming the vendor.
    API_KEY_ENV = API_KEY_ENV

    def __init__(self, api_key: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._api_key = api_key
        self._timeout = timeout

    def _key(self) -> str:
        key = self._api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise RuntimeError(f"{API_KEY_ENV} is not set — no upstream credential available")
        return key

    async def complete(
        self,
        *,
        provider_model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict] | None,
    ) -> ProviderResult:
        body: dict = {
            "model": provider_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "x-api-key": self._key(),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(API_URL, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        content = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return ProviderResult(
            content=content,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )

    async def list_model_ids(self) -> set[str]:
        """Every model id this account currently has access to (L-0026).

        Not part of the Provider Protocol (providers/base.py's Protocol is
        deliberately one method — `complete` — since not every future vendor
        adapter will have an equivalent listing endpoint); this is an extra
        method on the concrete Anthropic adapter only. main.py's startup
        routing check imports this class directly to call it, the same way
        live_smoke.py already reaches for AnthropicProvider.API_KEY_ENV
        without going through the Protocol.
        """
        headers = {
            "x-api-key": self._key(),
            "anthropic-version": API_VERSION,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(MODELS_URL, headers=headers)
            response.raise_for_status()
            data = response.json()
        return {
            item["id"]
            for item in data.get("data", [])
            if isinstance(item, dict) and "id" in item
        }
