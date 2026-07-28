"""AC-9: a real tool call made through each server's logging code path
inserts a row into mcp_ops.tool_calls containing caller identity, an
arguments hash, latency, and outcome.

Requires a reachable Postgres (DATABASE_URL or PG_URL) with
mcp/mcp_ops/schema.sql applied — see mcp/README.md. Skips cleanly (does
not fail) when unreachable, for casual local runs; mcp/scripts/
run_required_checks.sh fails loudly on any skip for an official
verification run.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mcp_logging

SAMPLE_CALLS = [
    ("mcp-web", "fetch_url", {"url": "https://example.com/synthetic"}),
    ("mcp-buffer", "list_queue", {}),
    ("mcp-canva", "export_design", {"design_id": "SYNTH-DESIGN-0001"}),
]


def test_real_tool_calls_insert_log_rows(monkeypatch, server_app, pg_conn, database_url):
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mcp_ops.tool_calls "
            "WHERE server_name IN ('mcp-web', 'mcp-buffer', 'mcp-canva')"
        )
    pg_conn.commit()

    for server_name, tool_name, arguments in SAMPLE_CALLS:
        module = server_app(server_name)
        module.dispatch(tool_name, arguments)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT caller_identity, arguments_hash, latency_ms, outcome
            FROM mcp_ops.tool_calls
            WHERE server_name IN ('mcp-web', 'mcp-buffer', 'mcp-canva')
            ORDER BY called_at
            """
        )
        rows = cur.fetchall()

    assert len(rows) == 3
    for caller_identity, arguments_hash, latency_ms, outcome in rows:
        assert caller_identity
        assert len(arguments_hash) == 64
        assert latency_ms is not None and float(latency_ms) >= 0
        assert outcome in ("success", "error", "rejected", "rate_limited")
