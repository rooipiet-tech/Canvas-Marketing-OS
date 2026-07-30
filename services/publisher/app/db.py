"""Postgres connectivity for Publisher.

Connection-per-request, deliberately: the kill-switch re-check on every
publish attempt must be an uncached read (app/kill_switch.py), and the jti
replay ledger must be shared durable state rather than per-process memory.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import database_url


def connect() -> psycopg.Connection:
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
