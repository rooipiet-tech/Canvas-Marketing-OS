"""Tests for providers/anthropic.py's real HTTP request shape
(F-GATEWAY-SYSTEM-ROLE, 4 Aug 2026, heartbeat round 16).

Every other test in this package (test_provider_extensibility.py included)
exercises a stub ``Provider`` rather than the real ``AnthropicProvider`` --
none of them ever asserted what actually goes out over the wire. These use
``httpx.MockTransport`` (injected via the constructor's `transport` hook,
mirroring OrchestratorGatewayClient's own test-only `transport` param
exactly) so no real network call is ever made, while still exercising the
adapter's real request-building code.

The core bug this proves fixed: Anthropic's Messages API rejects a
``role: "system"`` entry inside ``messages`` outright (HTTP 400) -- the
system prompt must be a separate top-level ``system`` string. Every caller
in this codebase (OrchestratorGatewayClient.complete()) builds its messages
list as ``[{"role": "system", ...}, {"role": "user", ...}]``, so this
adapter is the one place responsible for translating that into Anthropic's
own wire shape.
"""

from __future__ import annotations

import json

import httpx
from conftest import run
from providers.anthropic import AnthropicProvider, _split_system_prompt


def _stub_response_body() -> dict:
    return {
        "content": [{"type": "text", "text": "hello from anthropic"}],
        "usage": {"input_tokens": 11, "output_tokens": 3},
    }


def test_complete_moves_system_role_message_into_top_level_system_field():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_stub_response_body())

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    run(
        provider.complete(
            provider_model="claude-haiku-4-5-20251001",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Summarize this article."},
            ],
            max_tokens=256,
            temperature=0.7,
            tools=None,
        )
    )

    body = captured["body"]
    assert body["system"] == "You are a helpful assistant."
    # The system-role entry must be gone -- Anthropic rejects it outright if
    # left in `messages`, it is never just "also" accepted there.
    assert body["messages"] == [{"role": "user", "content": "Summarize this article."}]
    assert all(m["role"] != "system" for m in body["messages"])


def test_complete_omits_system_field_when_no_system_role_message_present():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_stub_response_body())

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    run(
        provider.complete(
            provider_model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "no system prompt here"}],
            max_tokens=256,
            temperature=0.7,
            tools=None,
        )
    )

    body = captured["body"]
    assert "system" not in body
    assert body["messages"] == [{"role": "user", "content": "no system prompt here"}]


def test_complete_concatenates_multiple_system_role_messages():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_stub_response_body())

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    run(
        provider.complete(
            provider_model="claude-haiku-4-5-20251001",
            messages=[
                {"role": "system", "content": "First system instruction."},
                {"role": "system", "content": "Second system instruction."},
                {"role": "user", "content": "go"},
            ],
            max_tokens=256,
            temperature=0.7,
            tools=None,
        )
    )

    body = captured["body"]
    assert body["system"] == "First system instruction.\n\nSecond system instruction."
    assert body["messages"] == [{"role": "user", "content": "go"}]


def test_complete_still_forwards_model_max_tokens_temperature_and_tools():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_stub_response_body())

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))
    tools = [{"name": "lookup", "description": "d", "input_schema": {"type": "object"}}]

    run(
        provider.complete(
            provider_model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
            max_tokens=512,
            temperature=0.2,
            tools=tools,
        )
    )

    body = captured["body"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["max_tokens"] == 512
    assert body["temperature"] == 0.2
    assert body["tools"] == tools


def test_complete_returns_provider_result_extracted_from_response():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_stub_response_body())

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    result = run(
        provider.complete(
            provider_model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=256,
            temperature=0.7,
            tools=None,
        )
    )

    assert result.content == "hello from anthropic"
    assert result.input_tokens == 11
    assert result.output_tokens == 3


# ---------------------------------------------------------------------
# stop_reason (F-EMPTY-COMPLETION-VISIBILITY, 7 Aug 2026, round 24) --
# the real production incident this closes: a 200 response whose `content`
# array carries zero `type: "text"` blocks previously produced
# ProviderResult(content="", ...) with nothing anywhere in this stack
# saying why. Anthropic's own top-level `stop_reason` is captured
# regardless of content shape and threaded straight through.
# ---------------------------------------------------------------------


def test_complete_captures_stop_reason_alongside_normal_content():
    def handler(_request: httpx.Request) -> httpx.Response:
        body = _stub_response_body()
        body["stop_reason"] = "end_turn"
        return httpx.Response(200, json=body)

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    result = run(
        provider.complete(
            provider_model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=256,
            temperature=0.7,
            tools=None,
        )
    )

    assert result.stop_reason == "end_turn"


def test_complete_extracts_empty_content_and_captures_stop_reason_when_no_text_blocks():
    """The actual shape of the incident this guards against: a 200 whose
    content array has no type: "text" block at all (e.g. only a tool_use
    block, or an empty array) -- content must come back as "" (not raise),
    with stop_reason still captured so the caller isn't left guessing why."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "x", "input": {}}],
                "usage": {"input_tokens": 20, "output_tokens": 5},
                "stop_reason": "tool_use",
            },
        )

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    result = run(
        provider.complete(
            provider_model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=256,
            temperature=0.7,
            tools=None,
        )
    )

    assert result.content == ""
    assert result.stop_reason == "tool_use"
    assert result.output_tokens == 5


def test_complete_stop_reason_is_none_when_absent_from_response():
    """A response with no stop_reason field at all (older fixture shape,
    or a future vendor adapter) must not raise -- ProviderResult.stop_reason
    defaults to None."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_stub_response_body())

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    result = run(
        provider.complete(
            provider_model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=256,
            temperature=0.7,
            tools=None,
        )
    )

    assert result.stop_reason is None


def test_complete_raises_for_a_400_response_exactly_as_before_this_fix():
    """Proves the fix doesn't swallow a REAL 400 (e.g. a genuinely malformed
    request) -- httpx's raise_for_status() still fires, unchanged."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"type": "invalid_request_error", "message": "bad request"}},
        )

    provider = AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))

    try:
        run(
            provider.complete(
                provider_model="claude-haiku-4-5-20251001",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=256,
                temperature=0.7,
                tools=None,
            )
        )
        raise AssertionError("expected httpx.HTTPStatusError")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 400


# ---------------------------------------------------------------------
# _split_system_prompt unit-level coverage (no HTTP involved at all)
# ---------------------------------------------------------------------


def test_split_system_prompt_extracts_single_system_message():
    system, conversation = _split_system_prompt(
        [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert system == "be helpful"
    assert conversation == [{"role": "user", "content": "hi"}]


def test_split_system_prompt_returns_none_when_absent():
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    system, conversation = _split_system_prompt(messages)
    assert system is None
    assert conversation == messages


def test_split_system_prompt_preserves_non_system_order():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]
    _system, conversation = _split_system_prompt(messages)
    assert conversation == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]
