"""Diagnostic probe: can asyncpg log in through ca-pgbouncer from inside the VNet?

Run by caj-vault-smoke-test AFTER the pytest suite (smoke-test-job.bicep loads
this file with loadTextContent and passes it in the PGBOUNCER_PROBE env var; the
job's exit code is still pytest's). Its output lands at the end of the smoke
job log, which deploy-infra.yml's failure branch already dumps.

Why it exists: deploy-pipeline runs 88 and 89 failed with ca-vault's
`asyncpg ProtocolViolationError: SASL authentication failed` although the
password the vault secret-writer stored equals ca-pgbouncer's (run 89: both 64
characters, match=true) and `psql` through the same PgBouncer works. This tells
the remaining explanations apart:

* every PgBouncer attempt fails, direct Postgres attempts succeed
    -> asyncpg and PgBouncer disagree about the SCRAM exchange
* PgBouncer attempts are mixed (some OK, some FAIL)
    -> more than one PgBouncer answering, one holding a different password
* both fail
    -> the password itself is not what Postgres holds

It prints error types, messages and timings only. The password is never printed:
no print() argument may expand it, and exception text is passed through
_redact() first. A test (test_pgbouncer_probe_never_prints_the_password.py)
enforces that.
"""
import asyncio
import os
import socket
import sys
import time
from urllib.parse import unquote, urlsplit

import asyncpg

ATTEMPTS = 8
CONNECT_TIMEOUT = 8
PGBOUNCER_HOST = "ca-pgbouncer"
PGBOUNCER_PORT = 6432


def _redact(text, secret):
    return text.replace(secret, "***") if secret else text


def _resolve(host):
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return sorted({i[4][0] for i in infos})
    except Exception as exc:  # name resolution failure is itself a finding
        return [f"unresolvable: {type(exc).__name__}"]


async def _attempts(label, host, port, user, password, database):
    ok = 0
    for i in range(1, ATTEMPTS + 1):
        started = time.monotonic()
        try:
            conn = await asyncpg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                ssl="require",
                timeout=CONNECT_TIMEOUT,
            )
            try:
                value = await conn.fetchval("select 1")
            finally:
                await conn.close()
            ok += 1
            print(f"probe {label} attempt {i}/{ATTEMPTS}: OK select 1 -> {value} "
                  f"({time.monotonic() - started:.2f}s)")
        except Exception as exc:
            detail = _redact(str(exc), password)
            print(f"probe {label} attempt {i}/{ATTEMPTS}: FAIL "
                  f"{type(exc).__module__}.{type(exc).__name__}: {detail} "
                  f"({time.monotonic() - started:.2f}s)")
    print(f"probe {label} summary: {ok}/{ATTEMPTS} succeeded")


async def main():
    parts = urlsplit(os.environ["DATABASE_URL"])
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    database = (parts.path or "/postgres").lstrip("/") or "postgres"
    direct_host = parts.hostname
    direct_port = parts.port or 5432

    print("=== pgbouncer probe (asyncpg, sslmode=require equivalent) ===")
    print(f"asyncpg {asyncpg.__version__}, python {sys.version.split()[0]}")
    print(f"user={user} database={database} password length={len(password)}")
    print(f"{PGBOUNCER_HOST} resolves to {_resolve(PGBOUNCER_HOST)}")
    print(f"{direct_host} resolves to {_resolve(direct_host)}")

    await _attempts("pgbouncer", PGBOUNCER_HOST, PGBOUNCER_PORT, user, password, database)
    await _attempts("direct-postgres", direct_host, direct_port, user, password, database)
    print("=== end of pgbouncer probe ===")


try:
    asyncio.run(main())
except Exception as exc:  # the probe must never change the job's exit code
    print(f"probe aborted: {type(exc).__name__}")
