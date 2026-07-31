"""Unit tests for app/buffer_client.py (plan step 14, AC-08, AC-09) — no
live mcp-buffer needed, exercised against httpx.MockTransport doubles."""

from __future__ import annotations

import json

import httpx
import pytest
from app.buffer_client import BufferClient, BufferClientError


def _with_mock(client: BufferClient, handler) -> BufferClient:
    client._client = httpx.Client(
        base_url="http://mock-buffer.invalid", transport=httpx.MockTransport(handler)
    )
    return client


def test_requires_base_url():
    with pytest.raises(BufferClientError):
        BufferClient(base_url="")


def test_list_queue_count_fixture_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["params"]["name"] == "list_queue"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "structuredContent": {
                        "source": "fixture",
                        "posts": [{"id": "p1"}, {"id": "p2"}],
                    }
                },
            },
        )

    client = _with_mock(BufferClient(base_url="http://mock-buffer.invalid"), handler)
    assert client.list_queue_count("68e73facca3a4e6b746d17b4") == 2


def test_list_queue_count_live_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "structuredContent": {
                        "source": "live",
                        "result": {"data": {"posts": [{"id": "p1"}]}},
                    }
                },
            },
        )

    client = _with_mock(BufferClient(base_url="http://mock-buffer.invalid"), handler)
    assert client.list_queue_count("68e73facca3a4e6b746d17b4") == 1


def test_create_draft_sends_only_channel_id_and_text_never_a_status_argument():
    """AC-09: no caller-supplied status/mode/state argument is ever
    threaded through create_draft."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.update(body["params"]["arguments"])
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"structuredContent": {"source": "fixture", "post": {"status": "draft"}}},
            },
        )

    client = _with_mock(BufferClient(base_url="http://mock-buffer.invalid"), handler)
    client.create_draft(channel_id="68e73facca3a4e6b746d17b4", text="hello world")

    assert set(captured.keys()) == {"channel_id", "text"}
    assert captured["channel_id"] == "68e73facca3a4e6b746d17b4"


def test_raises_on_jsonrpc_error():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -1, "message": "x"}}
        )

    client = _with_mock(BufferClient(base_url="http://mock-buffer.invalid"), handler)
    with pytest.raises(BufferClientError):
        client.create_draft(channel_id="c1", text="t")
