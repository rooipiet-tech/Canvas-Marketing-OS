"""mcp/conftest.py — shared pytest fixtures for the MCP tool-plane test suite.

Provides:
  - `server_app(server_name)`: imports (or re-imports, simulating a
    process restart) a given server's `app.main` module, isolated from
    the other two servers despite all three using an `app` package name
    internally on disk. Dual-mode gates are read at call time (or, for
    mcp-web's rate limiter, at import time) — a fresh import per test is
    how AC-4's "restart with zero file edits" mode switch is exercised
    without three same-named `app` packages colliding in sys.modules.
  - `mcp_client_factory`: server_name -> (http_client, tool_definitions).
    Defaults to an in-process FastAPI TestClient (AC-1's fixture-mode
    conformance run). If <SERVER>_BASE_URL env vars are set (e.g.
    MCP_WEB_BASE_URL), returns a real httpx.Client against that deployed
    FQDN instead — this is what lets the identical test file run as
    caj-mcp-smoke's in-VNet conformance check against real deployed
    Container Apps (AC-20), not just this repo's local pytest run.
  - `local_mock_server`: a minimal local HTTP server + request log, used
    by the mcp_smoke tests as the "local mock server standing in for the
    live provider" AC-6 explicitly asks for.
  - `database_url` / `pg_conn`: Postgres fixtures for mcp_logging /
    mcp_agent_e2e. Skip (not fail) when unreachable, for casual local
    runs — mcp/README.md documents that a real local Postgres with
    mcp/mcp_ops/schema.sql applied is REQUIRED for an official
    mcp_logging/mcp_agent_e2e verification pass; a skip there is
    explicitly not a substitute for that (mcp/scripts/run_required_checks.sh
    enforces this by failing on any skip).
  - An autouse fixture that starts every test from a clean slate for the
    3 secret/flag env vars this build's dual-mode gates key off, so
    accidental environment leakage from one test never causes another to
    silently run in the wrong mode.
"""

from __future__ import annotations

import http.server
import importlib
import os
import sys
import threading
from pathlib import Path

import pytest
from mcp_common.protocol import FIXTURE_MODE_HEADER

MCP_ROOT = Path(__file__).resolve().parent
SERVER_DIRS = {
    "mcp-web": MCP_ROOT / "mcp-web",
    "mcp-buffer": MCP_ROOT / "mcp-buffer",
    "mcp-canva": MCP_ROOT / "mcp-canva",
}

DUAL_MODE_ENV_VARS = (
    "BUFFER_API_KEY",
    "CANVA_CLIENT_ID",
    "CANVA_CLIENT_SECRET",
    "MCP_WEB_LIVE_MODE",
    "KEY_VAULT_URI",
)


@pytest.fixture(autouse=True)
def _clean_dual_mode_env(monkeypatch):
    for var in DUAL_MODE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _purge_app_modules() -> None:
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]


def load_server_app(server_name: str):
    """(Re-)import <server_name>'s app.main module fresh, isolated from
    the other two servers' same-named `app` package on disk."""
    server_dir = SERVER_DIRS[server_name]
    _purge_app_modules()
    sys.path.insert(0, str(server_dir))
    try:
        module = importlib.import_module("app.main")
    finally:
        sys.path.remove(str(server_dir))
    return module


@pytest.fixture
def server_app():
    """Factory fixture: server_name -> freshly-(re)imported app.main module."""

    def _factory(server_name: str):
        return load_server_app(server_name)

    return _factory


def _base_url_env_name(server_name: str) -> str:
    return f"{server_name.upper().replace('-', '_')}_BASE_URL"


@pytest.fixture
def mcp_client_factory():
    """Factory fixture: server_name -> (client, tool_definitions_or_None).

    `client` exposes a `.post(path, json=...)` method compatible between
    httpx.Client and FastAPI's TestClient. `tool_definitions` is the
    server's list[ToolDefinition] when running in-process, or None when
    hitting a deployed FQDN (caller can still use tools/list over the
    wire to discover them).
    """
    from fastapi.testclient import TestClient

    created_clients = []

    def _factory(server_name: str):
        # INCIDENT (2026-08-02, live — caj-mcp-smoke): AC-1's own contract
        # (this module's docstring, and test_conformance.py's) is fixture
        # mode with no live secrets present — but the deployed servers'
        # fixture-vs-live switch is credential-presence-based (AC-4/AC-6/
        # AC-7), so once real Buffer/Canva credentials landed in Key Vault
        # (an unrelated same-day human errand), the conformance smoke
        # started making real vendor API calls with synthetic tool
        # arguments never meant to reach a real vendor. mcp_common.
        # protocol.FIXTURE_MODE_HEADER opts a request out of that
        # credential-based switch regardless of what's configured
        # server-side — see credentials.py's force_fixture_mode() for the
        # full incident and design rationale. Sent on both branches below
        # for consistency with this module's stated fixture-mode
        # invariant, even though the in-process TestClient branch is
        # already fixture mode in practice (no real creds in local/CI env).
        fixture_headers = {FIXTURE_MODE_HEADER: "1"}

        base_url = os.environ.get(_base_url_env_name(server_name))
        if base_url:
            import httpx

            client = httpx.Client(base_url=base_url, timeout=15.0, headers=fixture_headers)
            created_clients.append(client)
            return client, None

        module = load_server_app(server_name)
        client = TestClient(module.app, headers=fixture_headers)
        created_clients.append(client)
        return client, module.mcp_server.tools

    yield _factory

    for client in created_clients:
        try:
            client.close()
        except Exception:
            pass


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    request_log: list[str] = []
    response_body: bytes = b'{"data": {}}'

    def _handle(self) -> None:
        type(self).request_log.append(f"{self.command} {self.path}")
        body = type(self).response_body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def log_message(self, *args, **kwargs) -> None:  # silence default access logging
        pass


@pytest.fixture
def local_mock_server():
    """Starts a minimal local HTTP server on 127.0.0.1:<ephemeral port>
    that answers every GET/POST with a canned JSON body and records every
    inbound request. Yields (base_url, request_log)."""
    _RecordingHandler.request_log = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", _RecordingHandler.request_log
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("DATABASE_URL") or os.environ.get("PG_URL")
    if not url:
        pytest.skip(
            "DATABASE_URL/PG_URL not set - see mcp/README.md: a local Postgres with "
            "mcp/mcp_ops/schema.sql applied is required for an official "
            "mcp_logging/mcp_agent_e2e verification run."
        )
    return url


@pytest.fixture(scope="session")
def pg_conn(database_url):
    try:
        import psycopg

        conn = psycopg.connect(database_url)
    except Exception as exc:
        pytest.skip(f"Postgres unreachable ({exc}) - see mcp/README.md.")
        return

    schema_sql = (MCP_ROOT / "mcp_ops" / "schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()

    yield conn
    conn.close()
