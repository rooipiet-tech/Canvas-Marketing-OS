"""mcp_common.logging — mcp_ops.tool_calls logging wrapper (AC-9).

Every real tool call made through a server's logging code path inserts one
row into mcp_ops.tool_calls (see mcp/mcp_ops/schema.sql) containing caller
identity, an arguments HASH (never raw arguments — no payload/PII ever
reaches the log table), latency, and a documented outcome enum.

Connection handling: a small, bounded, shared connection pool
(psycopg_pool.ConnectionPool) per DATABASE_URL, NOT a brand-new
psycopg.connect() per call. Every tool call — including ones rejected by
a rate limiter or that error out — used to open a fresh TCP+auth
handshake against Postgres; on the SHARED Postgres server that also
hosts the frozen Vault schema (contracts/vault-schema's 9 tables), a
caller flooding any of the 3 MCP endpoints could exhaust the server's
connection limit, a platform-wide availability risk, not just an
MCP-tool-plane one (post-build risk-security review, RISK-1/PERF-2). A
bounded pool (default max_size 5) caps the worst-case connection count
against Postgres regardless of request volume, and a short
connection-acquisition timeout makes pool exhaustion fail fast (raises,
caught by each server's best-effort logging wrapper) rather than piling
up new connections.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

# Documented outcome enum — mirrors mcp_ops.tool_call_outcome in
# mcp/mcp_ops/schema.sql. Keep these two in sync.
OUTCOMES = ("success", "error", "rejected", "rate_limited")

# Pool sizing/timeout are env-configurable (not hardcoded), same
# convention as mcp-web's rate limiter (CO-1).
_POOL_MAX_SIZE_ENV = "MCP_OPS_DB_POOL_MAX_SIZE"
_POOL_TIMEOUT_SECONDS_ENV = "MCP_OPS_DB_POOL_TIMEOUT_SECONDS"
_DEFAULT_POOL_MAX_SIZE = 5
_DEFAULT_POOL_TIMEOUT_SECONDS = 5.0

# One shared, bounded pool per distinct DATABASE_URL value, reused across
# every tool call in this process — never one new connection per call.
# Guarded by a lock since FastAPI/uvicorn can serve requests from
# multiple threads.
_pools_lock = threading.Lock()
_pools: dict[str, "ConnectionPool"] = {}


def hash_arguments(arguments: dict[str, Any]) -> str:
    """SHA-256 hex digest (64 chars, fixed-length) of the call's canonical
    -JSON arguments. Never store raw arguments in the log table."""
    canonical = json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _pool_settings() -> tuple[int, float]:
    max_size = int(os.environ.get(_POOL_MAX_SIZE_ENV, _DEFAULT_POOL_MAX_SIZE))
    timeout = float(os.environ.get(_POOL_TIMEOUT_SECONDS_ENV, _DEFAULT_POOL_TIMEOUT_SECONDS))
    return max_size, timeout


def _get_pool(database_url: str) -> "ConnectionPool":
    """Return the shared, bounded pool for this DATABASE_URL, creating it
    lazily (once) on first use."""
    with _pools_lock:
        pool = _pools.get(database_url)
        if pool is None:
            from psycopg_pool import ConnectionPool

            max_size, timeout = _pool_settings()
            pool = ConnectionPool(
                conninfo=database_url,
                min_size=0,
                max_size=max_size,
                timeout=timeout,
                open=True,
            )
            _pools[database_url] = pool
        return pool


def get_connection(database_url_env: str = "DATABASE_URL"):
    """Return a context manager that checks a connection OUT of the shared
    bounded pool for this env var's DATABASE_URL, and checks it back IN
    (never truly closes the socket) when the `with` block exits.

    Usage:
        with get_connection() as conn:
            log_tool_call(conn, ...)

    Raises RuntimeError if the env var is unset — callers (tests, dispatch
    wrappers) decide whether that means "skip this check" or "hard fail".
    If the pool is exhausted (max_size callers already checked out) and a
    connection isn't returned within the timeout, psycopg_pool raises
    PoolTimeout — callers should treat that the same as any other logging
    failure (best-effort: never let it fail the underlying tool call).
    """
    database_url = os.environ.get(database_url_env)
    if not database_url:
        raise RuntimeError(f"{database_url_env} is not set")
    pool = _get_pool(database_url)
    return pool.connection()


def log_tool_call(
    conn,
    *,
    server_name: str,
    tool_name: str,
    caller_identity: str,
    arguments: dict[str, Any],
    latency_ms: float,
    outcome: str,
) -> None:
    """Insert one row into mcp_ops.tool_calls."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    arguments_hash = hash_arguments(arguments)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mcp_ops.tool_calls
                (server_name, tool_name, caller_identity, arguments_hash, latency_ms, outcome)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (server_name, tool_name, caller_identity, arguments_hash, latency_ms, outcome),
        )
    conn.commit()


@contextmanager
def logged_tool_call(
    conn,
    *,
    server_name: str,
    tool_name: str,
    caller_identity: str,
    arguments: dict[str, Any],
) -> Iterator[None]:
    """Context manager: times the wrapped call and logs success/error
    automatically. For a 'rejected' or 'rate_limited' outcome, call
    log_tool_call directly instead — this wrapper only distinguishes
    success vs. error.
    """
    start = time.monotonic()
    try:
        yield
    except Exception:
        latency_ms = (time.monotonic() - start) * 1000
        log_tool_call(
            conn,
            server_name=server_name,
            tool_name=tool_name,
            caller_identity=caller_identity,
            arguments=arguments,
            latency_ms=latency_ms,
            outcome="error",
        )
        raise
    else:
        latency_ms = (time.monotonic() - start) * 1000
        log_tool_call(
            conn,
            server_name=server_name,
            tool_name=tool_name,
            caller_identity=caller_identity,
            arguments=arguments,
            latency_ms=latency_ms,
            outcome="success",
        )
