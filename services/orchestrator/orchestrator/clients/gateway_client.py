"""model-gateway client for the orchestrator's dispatch handlers (AC-01,
AC-19, AC-20).

Mirrors services/registry/gateway_client.py's request/response validation
against the frozen contracts/model-gateway/openapi.yaml contract
(CompletionRequest requires model/messages/agent_run_id; CompletionResponse
requires id/model/content/usage/agent_run_id) — deliberately a separate,
independent implementation (registry's client is scoped to the eval
harness's mock/live split and importing across service boundaries here
would be an unwanted coupling), but the same contract shape and the same
"never guess the FQDN" discipline.

Logical tiers ONLY (AC-20, L-0026): the literal `model` values used here
are exactly routing.yaml's logical ids — `claude-haiku` / `claude-sonnet` /
`claude-opus` — the same convention services/registry/gateway_client.py's
DEFAULT_MODEL already establishes (`claude-sonnet`, not a bare `sonnet` nor
any physical provider_model id like `claude-sonnet-4-6`). No physical
model id literal appears anywhere in this module.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from typing import Any

import httpx

from orchestrator.clients.azure_fqdn import resolve_live_fqdn
from orchestrator.telemetry_wiring import inject_traceparent

AZURE_CONTAINER_APP = "ca-model-gateway"

COMPLETIONS_PATH = "/v1/completions"

# The only 3 logical tiers routing.yaml defines. Never a physical model id.
TIER_HAIKU = "claude-haiku"
TIER_SONNET = "claude-sonnet"
TIER_OPUS = "claude-opus"
LOGICAL_TIERS = frozenset({TIER_HAIKU, TIER_SONNET, TIER_OPUS})

COMPLETION_REQUEST_REQUIRED = ("model", "messages", "agent_run_id")
COMPLETION_RESPONSE_REQUIRED = ("id", "model", "content", "usage", "agent_run_id")


class GatewayClientError(RuntimeError):
    """Raised when the gateway cannot be reached/resolved, or returns a
    response that doesn't conform to the frozen v1 contract.

    F-INGEST-REDACTION (4 Aug 2026): `status_code` and `error_code` are
    populated (best-effort) whenever the error originates from a non-200
    HTTP response whose body is JSON shaped like
    ``{"error": {"code": ..., ...}}`` -- e.g. the redaction firewall's
    ``REDACTION_BLOCKED`` responses (see model-gateway's redaction.py) --
    so a caller can distinguish a specific, potentially-recoverable error
    code from every other gateway failure without re-parsing response
    text itself. Both stay `None` for any other `GatewayClientError`
    (unreachable gateway, malformed response shape, etc.) -- never guess
    a code that wasn't actually present in the response body.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@lru_cache(maxsize=1)
def resolve_gateway_base_url() -> str | None:
    """CMOS_GATEWAY_BASE_URL env override wins (tests / non-Azure runs);
    otherwise resolve ca-model-gateway's real live FQDN. Never a hardcoded
    hostname (AC-19, L-0025).

    PERF-04 defense-in-depth: memoized for the lifetime of the process —
    see vault_client_ext.resolve_vault_base_url's identical note. Call
    resolve_gateway_base_url.cache_clear() to force re-resolution (tests
    only)."""
    override = os.environ.get("CMOS_GATEWAY_BASE_URL")
    if override:
        return override
    return resolve_live_fqdn(AZURE_CONTAINER_APP)


def _validate_response(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise GatewayClientError("CompletionResponse must be a JSON object")
    missing = [name for name in COMPLETION_RESPONSE_REQUIRED if name not in payload]
    if missing:
        raise GatewayClientError(
            f"CompletionResponse is missing required field(s): {', '.join(missing)}"
        )


class OrchestratorGatewayClient:
    """POST /v1/completions client used by the 5 new dispatch handlers."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise GatewayClientError(
                "OrchestratorGatewayClient requires a resolved base_url — never a guessed "
                "hostname (L-0025); call resolve_gateway_base_url() first"
            )
        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_content: str,
        agent_run_id: str,
        max_tokens: int = 1536,
        content_class: str | None = None,
    ) -> dict[str, Any]:
        if model not in LOGICAL_TIERS:
            raise GatewayClientError(
                f"model {model!r} is not one of the logical tiers {sorted(LOGICAL_TIERS)} "
                "(AC-20) — never pass a physical provider model id here"
            )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "agent_run_id": agent_run_id,
            "max_tokens": max_tokens,
        }
        if content_class is not None:
            # Additive field, not in the frozen v1 contract — see
            # model-gateway/completion.py's CONTENT_CLASS_PATTERN_EXEMPTIONS
            # and redaction.py's INCIDENT 2 note (F-INGEST-PUBLIC-SOURCE, 4
            # Aug 2026). Omitted entirely when None so every existing caller
            # sends byte-identical payloads to before this parameter existed.
            payload["content_class"] = content_class
        # W3C traceparent (DE-5): carries the current ambient trace context
        # (see telemetry_wiring.emit_task_span's run_id-derived parent) so
        # model-gateway's own adopted span (steps 15-16) joins the SAME
        # trace as this task's stage span.
        response = self._client.post(COMPLETIONS_PATH, json=payload, headers=inject_traceparent())
        if response.status_code != 200:
            error_code = None
            try:
                error_code = (response.json().get("error") or {}).get("code")
            except Exception:  # noqa: BLE001 - error_code stays None if the body isn't {"error": {"code": ...}}
                pass
            raise GatewayClientError(
                f"gateway returned HTTP {response.status_code} for {COMPLETIONS_PATH}: "
                f"{response.text[:500]}",
                status_code=response.status_code,
                error_code=error_code,
            )
        body = response.json()
        _validate_response(body)
        return body

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorGatewayClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


def new_agent_run_id() -> str:
    return str(uuid.uuid4())
