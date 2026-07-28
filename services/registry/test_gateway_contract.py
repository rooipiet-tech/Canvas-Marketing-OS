#!/usr/bin/env python3
"""Conformance tests for gateway_client against the frozen v1 contract.

Run standalone (from repo root):

    python services/registry/test_gateway_contract.py

Asserts:

  1. `CompletionRequest` required fields (model, messages, agent_run_id) are
     enforced, and a request missing `agent_run_id` is rejected **before**
     the transport is touched — proved by a counting transport that records
     zero calls after the rejection.
  2. `CompletionResponse` required fields (id, model, content,
     usage.input_tokens, usage.output_tokens, agent_run_id) are enforced.
  3. The default-mode client cannot reach a real host: its base URL is not
     the real gateway host, its transport is an in-process
     `httpx.MockTransport`, and a full mocked eval run records zero live
     calls for BOTH rubric check types.
  4. `build_live_client` refuses to be constructed without an API key.

Exits 0 with PASS, or 1 with `FAIL: <reason>`.
"""

from __future__ import annotations

import json
import uuid

import httpx
from common import discover_function_packages, fail
from eval_harness import CHECK_TYPES, load_tasks, load_tool_check_module, run_task_mocked
from gateway_client import (
    DEFAULT_BASE_URL,
    GatewayClient,
    GatewayContractError,
    build_completion_request,
    build_live_client,
    build_mock_client,
    validate_completion_request,
    validate_completion_response,
)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def expect_rejected(payload, validator, field: str) -> None:
    try:
        validator(payload)
    except GatewayContractError as exc:
        expect(
            field in str(exc),
            f"rejection message for missing {field!r} must name the field; got: {exc}",
        )
        return
    fail(f"a payload missing {field!r} was accepted — contract validation is not enforced")


def main() -> None:
    valid_request = build_completion_request(
        system_prompt="system", user_content="user", agent_run_id=str(uuid.uuid4())
    )
    validate_completion_request(valid_request)

    # --- 1. CompletionRequest required fields.
    for field in ("model", "messages", "agent_run_id"):
        broken = dict(valid_request)
        broken.pop(field)
        expect_rejected(broken, validate_completion_request, field)

    empty_messages = dict(valid_request, messages=[])
    expect_rejected(empty_messages, validate_completion_request, "messages")

    bad_uuid = dict(valid_request, agent_run_id="not-a-uuid")
    expect_rejected(bad_uuid, validate_completion_request, "agent_run_id")

    # --- 1b. Rejection happens PRE-NETWORK.
    calls = {"count": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={})

    missing_agent_run_id = dict(valid_request)
    missing_agent_run_id.pop("agent_run_id")
    with GatewayClient(
        transport=httpx.MockTransport(counting_handler),
        base_url="http://counting.invalid",
        is_live=False,
    ) as client:
        try:
            client.complete(missing_agent_run_id)
        except GatewayContractError:
            pass
        else:
            fail("a request missing agent_run_id was sent to the transport")
        expect(
            calls["count"] == 0,
            f"transport was invoked {calls['count']} time(s) for an invalid request — "
            "validation must run before the network leg",
        )
        expect(
            client.counters.mock_calls == 0 and client.counters.live_calls == 0,
            "a pre-network rejection must not be counted as a call",
        )

    # --- 2. CompletionResponse required fields.
    valid_response = {
        "id": "resp-1",
        "model": "claude-sonnet",
        "content": "hello",
        "usage": {"input_tokens": 2, "output_tokens": 1},
        "agent_run_id": valid_request["agent_run_id"],
    }
    validate_completion_response(valid_response)
    for field in ("id", "model", "content", "usage", "agent_run_id"):
        broken = dict(valid_response)
        broken.pop(field)
        expect_rejected(broken, validate_completion_response, field)
    for field in ("input_tokens", "output_tokens"):
        usage = dict(valid_response["usage"])
        usage.pop(field)
        expect_rejected(dict(valid_response, usage=usage), validate_completion_response, field)

    # --- 3. Default mode cannot reach a real host.
    with build_mock_client(lambda payload: json.dumps({"echo": payload["model"]})) as mock_client:
        expect(mock_client.is_live is False, "build_mock_client must produce a non-live client")
        expect(
            DEFAULT_BASE_URL not in mock_client.base_url,
            f"mock client base_url {mock_client.base_url!r} must not be the real gateway host",
        )
        expect(
            isinstance(mock_client.transport, httpx.MockTransport),
            "mock client must use an in-process httpx.MockTransport",
        )
        response = mock_client.complete(valid_request)
        validate_completion_response(response)
        expect(
            mock_client.counters.live_calls == 0 and mock_client.counters.mock_calls == 1,
            f"expected 1 mock call and 0 live calls, got {mock_client.counters}",
        )

    # --- 3b. A real mocked eval run makes zero live calls per check type.
    packages = discover_function_packages()
    expect(bool(packages), "no function packages discovered")
    live_by_check_type = {check_type: 0 for check_type in CHECK_TYPES}
    graded = {check_type: 0 for check_type in CHECK_TYPES}
    for package_dir in packages:
        prompt_text = (package_dir / "prompt.md").read_text(encoding="utf-8")
        module = load_tool_check_module(package_dir)
        for task in load_tasks(package_dir):
            run_task_mocked(task, prompt_text, module, live_by_check_type)
            for entry in task.get("rubric", []):
                graded[str(entry.get("check_type", "tool_check"))] += 1
    for check_type in CHECK_TYPES:
        expect(
            graded[check_type] > 0,
            f"no {check_type} rubric entries were exercised — the zero-live-call "
            f"assertion for {check_type} would be vacuous",
        )
        expect(
            live_by_check_type[check_type] == 0,
            f"{live_by_check_type[check_type]} real network call(s) made while scoring "
            f"{check_type} entries in default mode",
        )

    # --- 4. build_live_client refuses to build without a key.
    try:
        build_live_client(DEFAULT_BASE_URL, "")
    except GatewayContractError:
        pass
    else:
        fail("build_live_client built a client with no API key")

    print("CompletionRequest  required fields enforced (model, messages, agent_run_id)")
    print("CompletionResponse required fields enforced (id, model, content, usage.*, agent_run_id)")
    print("pre-network        invalid request rejected with 0 transport invocations")
    print(
        "default mode       in-process MockTransport; 0 live calls across "
        + ", ".join(f"{ct}={graded[ct]} entries" for ct in CHECK_TYPES)
    )
    print("live client        refuses to build without an API key")
    print("PASS")


if __name__ == "__main__":
    main()
