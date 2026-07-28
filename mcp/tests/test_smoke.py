"""AC-6: when a live secret (or, for mcp-web, its non-secret
MCP_WEB_LIVE_MODE flag-equivalent) is present, its per-server smoke test
performs exactly one real read-only call and exits 0; when absent, it
exits 0 (skipped) without attempting any network call.

Runs each server's standalone smoke_test.py as a subprocess (matching how
it's actually invoked operationally), with a real local mock HTTP server
standing in for the live provider (conftest.py's local_mock_server
fixture) so "exactly one inbound request" is independently verifiable,
not just asserted from inside the process under test.

Note on mcp-buffer: Buffer's API is GraphQL, so its one read-only call is
an HTTP POST carrying a `query` operation (not a mutation) rather than a
literal HTTP GET — "GET-shaped" per AC-6's verify text is interpreted here
as "semantically read-only", since GraphQL has no GET-based read
convention. mcp-web and mcp-canva's smoke calls are literal HTTP GETs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.mcp_smoke

MCP_ROOT = Path(__file__).resolve().parent.parent

DUAL_MODE_ENV_VARS = (
    "BUFFER_API_KEY",
    "CANVA_CLIENT_ID",
    "CANVA_CLIENT_SECRET",
    "MCP_WEB_LIVE_MODE",
)


def _run_smoke(server_dir: str, env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for var in DUAL_MODE_ENV_VARS:
        env.pop(var, None)
    env.update(env_overrides)
    script = MCP_ROOT / server_dir / "smoke_test.py"
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=str(MCP_ROOT / server_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_mcp_web_smoke_absent_secret_skips_cleanly_without_network():
    result = _run_smoke("mcp-web", {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not set" in result.stdout.lower()


def test_mcp_web_smoke_present_flag_makes_exactly_one_read_only_call(local_mock_server):
    base_url, request_log = local_mock_server
    result = _run_smoke(
        "mcp-web",
        {
            "MCP_WEB_LIVE_MODE": "true",
            "MCP_WEB_ALLOWLIST": "127.0.0.1",
            "MCP_WEB_SMOKE_URL": f"{base_url}/synthetic",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(request_log) == 1
    assert request_log[0].startswith("GET")


def test_mcp_buffer_smoke_absent_secret_skips_cleanly_without_network():
    result = _run_smoke("mcp-buffer", {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not set" in result.stdout.lower()


def test_mcp_buffer_smoke_present_secret_makes_exactly_one_read_only_call(local_mock_server):
    base_url, request_log = local_mock_server
    result = _run_smoke(
        "mcp-buffer",
        {
            "BUFFER_API_KEY": "dummy-buffer-key",
            "BUFFER_API_URL": f"{base_url}/graphql",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(request_log) == 1


def test_mcp_canva_smoke_absent_secret_skips_cleanly_without_network():
    result = _run_smoke("mcp-canva", {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not set" in result.stdout.lower()


def test_mcp_canva_smoke_present_secret_makes_exactly_one_read_only_call(local_mock_server):
    base_url, request_log = local_mock_server
    result = _run_smoke(
        "mcp-canva",
        {
            "CANVA_CLIENT_ID": "dummy-client-id",
            "CANVA_CLIENT_SECRET": "dummy-client-secret",
            "CANVA_API_URL": base_url,
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(request_log) == 1
    assert request_log[0].startswith("GET")
