"""AC-1: MCP protocol conformance (initialize, tools/list, tools/call) in
fixture mode, for all 3 servers, with no live secrets present.

Also serves as the smoke-check pytest -m mcp_conformance runs inside
caj-mcp-smoke against deployed FQDNs (AC-20), via mcp_client_factory's
<SERVER>_BASE_URL env-var switch — see conftest.py.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mcp_conformance

SERVERS = ["mcp-web", "mcp-buffer", "mcp-canva"]

SAMPLE_ARGS = {
    "mcp-web": ("fetch_url", {"url": "https://example.com/synthetic"}),
    "mcp-buffer": ("list_queue", {}),
    "mcp-canva": ("create_design_from_template", {"template_id": "SYNTH-TEMPLATE-0001"}),
}


@pytest.mark.parametrize("server_name", SERVERS)
def test_mcp_conformance(server_name, mcp_client_factory):
    client, tools = mcp_client_factory(server_name)

    # initialize
    resp = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["serverInfo"]["name"] == server_name

    # tools/list
    resp = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert resp.status_code == 200
    listed_tools = resp.json()["result"]["tools"]
    assert len(listed_tools) > 0
    listed_names = {t["name"] for t in listed_tools}
    if tools is not None:
        assert listed_names == {t.name for t in tools}

    # tools/call — one fixture-backed tool
    tool_name, arguments = SAMPLE_ARGS[server_name]
    assert tool_name in listed_names
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error") is None, f"unexpected error: {body.get('error')}"
    result = body["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["source"] == "fixture"
