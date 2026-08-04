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


def _split_system_prompt(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """F-GATEWAY-SYSTEM-ROLE (4 Aug 2026, heartbeat round 16): Anthropic's
    Messages API takes the system prompt as a separate top-level ``system``
    string field, never as a ``role: "system"`` entry inside ``messages`` --
    a message with that role is REJECTED outright with an HTTP 400
    (``messages: Input should be 'user' or 'assistant'``), not silently
    accepted or reinterpreted.

    Every caller in this codebase builds its messages list as
    ``[{"role": "system", ...}, {"role": "user", ...}]`` -- see
    orchestrator/clients/gateway_client.py's ``OrchestratorGatewayClient.
    complete()`` -- which is the provider-agnostic shape completion.py's own
    contract expects (routing/redaction/budget all treat ``messages`` as an
    opaque list; nothing upstream of this adapter knows or cares which
    vendor eventually serves the request). This adapter is therefore the
    one place responsible for translating that shape into Anthropic's own
    wire format, exactly as its module docstring already promises ("adding
    a provider never edits [completion.py]").

    Discovered live (not in any local/mocked test) on the FIRST real
    completion.handle_completion request whose ``messages`` list ever
    actually reached this adapter with more than a stub/faked provider
    behind it -- every earlier heartbeat attempt failed upstream of this
    point (see redaction.py's INCIDENT 2 / F-INGEST-PUBLIC-SOURCE note), and
    every existing unit test in this package exercises a stub ``Provider``
    (test_provider_extensibility.py) rather than this real adapter's HTTP
    body shape, so nothing had ever asserted it end-to-end before.

    Concatenates (newline-joined, in list order) every ``role == "system"``
    entry's content into one string and strips those entries out of the
    conversation; every other role passes through unchanged and in order.
    Returns ``(None, messages)`` unchanged when there is no system-role
    entry at all, so a caller that never sends one behaves exactly as
    before this function existed.
    """
    system_parts = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
    conversation = [m for m in messages if m.get("role") != "system"]
    system_prompt = "\n\n".join(part for part in system_parts if part) or None
    return system_prompt, conversation


class AnthropicProvider(Provider):
    """Adapter for Anthropic's hosted Claude models."""

    # Exposed on the class so provider-agnostic callers (live_smoke.py) can
    # ask an adapter which environment variable carries its credential
    # without naming the vendor.
    API_KEY_ENV = API_KEY_ENV

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self._timeout = timeout
        # Test-only hook (mirrors OrchestratorGatewayClient's own
        # `transport` constructor param exactly) -- lets a test substitute
        # an httpx.MockTransport instead of the real network, without
        # patching httpx.AsyncClient globally or making a real HTTP call.
        # None (the default) in every real caller, which behaves exactly
        # as before this parameter existed.
        self._transport = transport

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
        system_prompt, conversation = _split_system_prompt(messages)
        body: dict = {
            "model": provider_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": conversation,
        }
        if system_prompt is not None:
            body["system"] = system_prompt
        if tools:
            body["tools"] = tools

        headers = {
            "x-api-key": self._key(),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
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
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(MODELS_URL, headers=headers)
            response.raise_for_status()
            data = response.json()
        return {
            item["id"]
            for item in data.get("data", [])
            if isinstance(item, dict) and "id" in item
        }
