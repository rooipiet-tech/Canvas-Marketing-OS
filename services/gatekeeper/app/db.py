"""Postgres connectivity for Gatekeeper.

Deliberately connection-per-request rather than a long-lived pool: every
kill-switch check must be an uncached read against the live database (see
app/kill_switch.py), and Container Apps replicas are short-lived.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import database_url


def connect() -> psycopg.Connection:
    """Open a new autocommit connection returning dict rows."""
    return psycopg.connect(database_url(), autocommit=True, row_factory=dict_row)


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_conn() -> Iterator[psycopg.Connection]:
    """FastAPI dependency yielding a per-request connection."""
    with connection() as conn:
        yield conn
