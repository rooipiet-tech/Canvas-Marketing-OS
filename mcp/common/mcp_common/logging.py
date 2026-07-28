"""mcp_common.logging — mcp_ops.tool_calls logging wrapper (AC-9).

Every real tool call made through a server's logging code path inserts one
row into mcp_ops.tool_calls (see mcp/mcp_ops/schema.sql) containing caller
identity, an arguments HASH (never raw arguments — no payload/PII ever
reaches the log table), latency, and a documented outcome enum.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

# Documented outcome enum — mirrors mcp_ops.tool_call_outcome in
# mcp/mcp_ops/schema.sql. Keep these two in sync.
OUTCOMES = ("success", "error", "rejected", "rate_limited")


def hash_arguments(arguments: dict[str, Any]) -> str:
    """SHA-256 hex digest (64 chars, fixed-length) of the call's canonical
    -JSON arguments. Never store raw arguments in the log table."""
    canonical = json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def get_connection(database_url_env: str = "DATABASE_URL"):
    """Return a new psycopg connection built from the given env var.

    Raises RuntimeError if the env var is unset — callers (tests, dispatch
    wrappers) decide whether that means "skip this check" or "hard fail".
    """
    import psycopg

    database_url = os.environ.get(database_url_env)
    if not database_url:
        raise RuntimeError(f"{database_url_env} is not set")
    return psycopg.connect(database_url)


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
