"""Model-gateway client conforming to contracts/model-gateway/openapi.yaml.

Two clearly separated construction paths — never one ambiguous factory:

* ``build_mock_client(responder)`` — the **default** for every eval run. It
  installs an in-process transport, so there is no socket, no DNS lookup and
  no way to reach the real gateway host even by accident. Both `tool_check`
  and `llm_judged` rubric scoring go through this client.
* ``build_live_client(base_url, api_key)`` — used **only** by
  ``eval_harness.py --live``. It is the only path that can open a real
  connection, and the harness refuses to construct it without an API key.

Request/response shapes are validated against the frozen v1 contract
(`CompletionRequest` requires `model`, `messages`, `agent_run_id`;
`CompletionResponse` requires `id`, `model`, `content`, `usage.input_tokens`,
`usage.output_tokens`, `agent_run_id`). Validation happens *before* the
transport is invoked, so a malformed request never leaves the process.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

DEFAULT_MODEL = "claude-sonnet"
DEFAULT_BASE_URL = "https://model-gateway.internal.cmos.dev"
COMPLETIONS_PATH = "/v1/completions"

MOCK_BASE_URL = "http://mock-gateway.invalid"

COMPLETION_REQUEST_REQUIRED = ("model", "messages", "agent_run_id")
COMPLETION_RESPONSE_REQUIRED = ("id", "model", "content", "usage", "agent_run_id")
USAGE_REQUIRED = ("input_tokens", "output_tokens")

VALID_ROLES = {"system", "user", "assistant", "tool"}


class GatewayContractError(ValueError):
    """A request or response violates the frozen model-gateway v1 contract."""


@dataclass
class CallCounters:
    """Per-run call accounting, used to assert zero real network calls."""

    mock_calls: int = 0
    live_calls: int = 0
    by_purpose: dict[str, int] = field(default_factory=dict)

    def record(self, *, live: bool, purpose: str) -> None:
        if live:
            self.live_calls += 1
        else:
            self.mock_calls += 1
        self.by_purpose[purpose] = self.by_purpose.get(purpose, 0) + 1


def build_completion_request(
    *,
    system_prompt: str,
    user_content: str,
    agent_run_id: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Build a contract-conformant CompletionRequest payload."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "agent_run_id": agent_run_id or str(uuid.uuid4()),
        "max_tokens": max_tokens,
    }


def validate_completion_request(payload: Any) -> None:
    """Raise GatewayContractError unless `payload` is a valid CompletionRequest."""
    if not isinstance(payload, dict):
        raise GatewayContractError("CompletionRequest must be a JSON object")

    missing = [name for name in COMPLETION_REQUEST_REQUIRED if name not in payload]
    if missing:
        raise GatewayContractError(
            f"CompletionRequest is missing required field(s): {', '.join(missing)} "
            "(contracts/model-gateway/openapi.yaml requires model, messages, agent_run_id)"
        )

    if not isinstance(payload["model"], str) or not payload["model"]:
        raise GatewayContractError("CompletionRequest.model must be a non-empty string")

    messages = payload["messages"]
    if not isinstance(messages, list) or len(messages) < 1:
        raise GatewayContractError("CompletionRequest.messages must have minItems 1")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise GatewayContractError(f"CompletionRequest.messages[{index}] must be an object")
        if "role" not in message or "content" not in message:
            raise GatewayContractError(
                f"CompletionRequest.messages[{index}] requires both role and content"
            )
        if message["role"] not in VALID_ROLES:
            raise GatewayContractError(
                f"CompletionRequest.messages[{index}].role {message['role']!r} is not one of "
                f"{sorted(VALID_ROLES)}"
            )
        if not isinstance(message["content"], str):
            raise GatewayContractError(
                f"CompletionRequest.messages[{index}].content must be a string"
            )

    agent_run_id = payload["agent_run_id"]
    if not isinstance(agent_run_id, str) or not agent_run_id:
        raise GatewayContractError(
            "CompletionRequest.agent_run_id must be a non-empty uuid string — it is the "
            "FK to Vault agent_runs.id used for cost attribution"
        )
    try:
        uuid.UUID(agent_run_id)
    except ValueError as exc:
        raise GatewayContractError(
            f"CompletionRequest.agent_run_id {agent_run_id!r} is not a uuid: {exc}"
        ) from exc


def validate_completion_response(payload: Any) -> None:
    """Raise GatewayContractError unless `payload` is a valid CompletionResponse."""
    if not isinstance(payload, dict):
        raise GatewayContractError("CompletionResponse must be a JSON object")

    missing = [name for name in COMPLETION_RESPONSE_REQUIRED if name not in payload]
    if missing:
        raise GatewayContractError(
            f"CompletionResponse is missing required field(s): {', '.join(missing)}"
        )
    if not isinstance(payload["content"], str):
        raise GatewayContractError("CompletionResponse.content must be a string")

    usage = payload["usage"]
    if not isinstance(usage, dict):
        raise GatewayContractError("CompletionResponse.usage must be an object")
    usage_missing = [name for name in USAGE_REQUIRED if name not in usage]
    if usage_missing:
        raise GatewayContractError(
            f"CompletionResponse.usage is missing required field(s): {', '.join(usage_missing)}"
        )
    for name in USAGE_REQUIRED:
        if not isinstance(usage[name], int) or isinstance(usage[name], bool):
            raise GatewayContractError(f"CompletionResponse.usage.{name} must be an integer")


def build_completion_response(request_payload: dict[str, Any], content: str) -> dict[str, Any]:
    """Build a contract-conformant CompletionResponse for a given request.

    Token counts are word counts, not a real tokeniser — deliberately so.
    A mocked run must never imply it measured real usage.
    """
    prompt_text = " ".join(str(message["content"]) for message in request_payload["messages"])
    return {
        "id": f"mock-{uuid.uuid5(uuid.NAMESPACE_OID, prompt_text)}",
        "model": request_payload["model"],
        "content": content,
        "usage": {
            "input_tokens": len(prompt_text.split()),
            "output_tokens": len(content.split()),
        },
        "agent_run_id": request_payload["agent_run_id"],
    }


class GatewayClient:
    """Thin POST /v1/completions client with contract validation on both legs."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport,
        base_url: str,
        is_live: bool,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.is_live = is_live
        self.counters = CallCounters()
        self.transport = transport
        self._client = httpx.Client(
            transport=transport,
            base_url=self.base_url,
            headers=headers or {},
            timeout=timeout,
        )

    @property
    def mode(self) -> str:
        return "live" if self.is_live else "mock"

    def complete(self, payload: dict[str, Any], *, purpose: str = "generate") -> dict[str, Any]:
        """POST a CompletionRequest and return a validated CompletionResponse.

        The request is validated *before* the transport is touched, so an
        invalid request (e.g. one missing agent_run_id) is rejected
        pre-network and never counted as a call.
        """
        validate_completion_request(payload)
        response = self._client.post(COMPLETIONS_PATH, json=payload)
        self.counters.record(live=self.is_live, purpose=purpose)
        if response.status_code != 200:
            raise GatewayContractError(
                f"gateway returned HTTP {response.status_code} for {COMPLETIONS_PATH}"
            )
        body = response.json()
        validate_completion_response(body)
        return body

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


Responder = Callable[[dict[str, Any]], str]


def build_mock_client(responder: Responder) -> GatewayClient:
    """Default-mode client. Always in-process — cannot reach a real host.

    `responder` receives the parsed CompletionRequest and returns the string
    the simulated model would have produced. Used by BOTH `tool_check` and
    `llm_judged` rubric scoring, so no rubric check type has a path to the
    network.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content.decode("utf-8"))
        validate_completion_request(request_payload)
        content = responder(request_payload)
        return httpx.Response(200, json=build_completion_response(request_payload, content))

    return GatewayClient(
        transport=httpx.MockTransport(handler),
        base_url=MOCK_BASE_URL,
        is_live=False,
    )


def build_live_client(base_url: str, api_key: str, *, timeout: float = 30.0) -> GatewayClient:
    """Live-mode client. The only construction path that can open a socket.

    Refuses to build without an API key, so `--live` can never silently
    degrade into an unauthenticated real call.
    """
    if not api_key:
        raise GatewayContractError(
            "build_live_client requires an API key — a live run without a credential "
            "must be reported as SKIPPED, never attempted"
        )
    return GatewayClient(
        transport=httpx.HTTPTransport(retries=0),
        base_url=base_url or DEFAULT_BASE_URL,
        is_live=True,
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        timeout=timeout,
    )
