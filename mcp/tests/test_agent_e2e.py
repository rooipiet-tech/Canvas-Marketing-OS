"""AC-15: an agent using any MCP client can, in fixture mode, list a
server's tools, call a tool, and read back the resulting log row's
outcome via a documented query, end-to-end and purely programmatically
(no human-only/GUI step), for all three servers.

Requires a reachable Postgres (DATABASE_URL or PG_URL) with
mcp/mcp_ops/schema.sql applied — see mcp/README.md. Skips cleanly (does
not fail) when unreachable, for casual local runs; mcp/scripts/
run_required_checks.sh fails loudly on any skip for an official
verification run.
"""

from __future__ import annotations

import pytest
from mcp_common import hash_arguments

pytestmark = pytest.mark.mcp_agent_e2e

SERVERS_AND_CALLS = [
    ("mcp-web", "fetch_url", {"url": "https://example.com/synthetic"}),
    ("mcp-buffer", "list_queue", {}),
    ("mcp-canva", "export_design", {"design_id": "SYNTH-DESIGN-0001"}),
]


@pytest.mark.parametrize("server_name,tool_name,arguments", SERVERS_AND_CALLS)
def test_agent_e2e_list_call_log_readback(
    server_name, tool_name, arguments, monkeypatch, mcp_client_factory, pg_conn, database_url
):
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mcp_ops.tool_calls WHERE server_name = %s AND tool_name = %s",
            (server_name, tool_name),
        )
    pg_conn.commit()

    # 1. connect an MCP client over the server's transport (in-process
    #    TestClient here; a real httpx.Client against a deployed FQDN when
    #    <SERVER>_BASE_URL is set — see conftest.py's mcp_client_factory).
    client, _ = mcp_client_factory(server_name)

    # 2. tools/list
    resp = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert resp.status_code == 200
    listed_names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert tool_name in listed_names

    # 3. tools/call — one fixture-backed tool
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error") is None
    assert body["result"]["isError"] is False

    # 4. query mcp_ops.tool_calls (per AC-9) for the row matching this
    #    call's arguments hash — purely programmatic, no GUI step.
    expected_hash = hash_arguments(arguments)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT outcome FROM mcp_ops.tool_calls
            WHERE server_name = %s AND tool_name = %s AND arguments_hash = %s
            ORDER BY called_at DESC LIMIT 1
            """,
            (server_name, tool_name, expected_hash),
        )
        row = cur.fetchone()

    # 5. assert the row's outcome reflects success.
    assert row is not None, "no matching mcp_ops.tool_calls row found for this call"
    assert row[0] == "success"
