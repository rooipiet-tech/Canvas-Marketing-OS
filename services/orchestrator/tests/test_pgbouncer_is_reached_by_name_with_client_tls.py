"""ca-pgbouncer must be reached by NAME, and must offer client-side TLS.

deploy-pipeline run 85 failed at the Vault smoke test: every database call from
ca-vault hung. deploy-pgbouncer run 3's probe found two separate causes:

1. Wrong address. Every caller reached ca-pgbouncer at its
   `.internal.<env>.azurecontainerapps.io` FQDN. For a TCP-transport app that
   FQDN resolves to the environment's proxy address (100.100.0.208), where
   nothing answers on 6432; TCP connects, pg_isready and psql all timed out.
   The bare app name resolved to the app's own service and connected. The
   Container Apps docs say a TCP app "is accessible to other container apps in
   the same environment via its name ... and exposed port number" (L-0091).
2. No client TLS. entrypoint.sh set server_tls_sslmode (PgBouncer -> Postgres)
   but nothing for clients, while all 20 callers use sslmode=require, so once
   reachable they would fail with "server does not support SSL, but SSL was
   required".

Both are guarded below, as pure functions over file text so each can be shown
to fail on the old shape as well as pass on the current one.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MAIN_BICEP = REPO / "infra" / "main.bicep"
ENTRYPOINT = REPO / "pgbouncer" / "entrypoint.sh"
DOCKERFILE = REPO / "pgbouncer" / "Dockerfile"


def pgbouncer_host_violations(main_bicep: str) -> list[str]:
    """Problems with how main.bicep derives the host callers connect to."""
    problems: list[str] = []
    m = re.search(r"^var\s+pgbouncerHost\s*=\s*(.+?)\s*$", main_bicep, re.M)
    if not m:
        problems.append("main.bicep declares no `var pgbouncerHost`")
    elif m.group(1) != "pgbouncer.outputs.appName":
        problems.append(
            f"`var pgbouncerHost = {m.group(1)}` — must be "
            "`pgbouncer.outputs.appName` (the module output keeps the real "
            "dependency, L-0017; a literal would drop it)"
        )
    for lineno, line in enumerate(main_bicep.splitlines(), 1):
        stripped = line.lstrip()
        if "pgbouncer.outputs.internalFqdn" in line and not stripped.startswith(
            ("output ", "//")
        ):
            problems.append(
                f"main.bicep:{lineno} uses pgbouncer.outputs.internalFqdn as a "
                "connection host — that FQDN does not answer on 6432 (L-0091)"
            )
    if re.search(r"\bpgbouncerFqdn\b", main_bicep):
        problems.append("main.bicep still references the old `pgbouncerFqdn` name")
    return problems


def client_tls_violations(entrypoint: str, dockerfile: str) -> list[str]:
    """Problems that would leave PgBouncer unable to serve sslmode=require."""
    problems: list[str] = []
    ini = entrypoint.split("cat > \"$INI\"", 1)[-1]
    if not re.search(r"^client_tls_sslmode\s*=\s*require\s*$", ini, re.M):
        problems.append("pgbouncer.ini has no `client_tls_sslmode = require`")
    for key in ("client_tls_key_file", "client_tls_cert_file"):
        if not re.search(rf"^{key}\s*=\s*\S+", ini, re.M):
            problems.append(f"pgbouncer.ini has no `{key}`")
    if "openssl req -x509" not in entrypoint:
        problems.append("entrypoint.sh never generates the client TLS certificate")
    if not re.search(r"apt-get install[^\n]*(\\\n[^\n]*)*\bopenssl\b", dockerfile):
        problems.append("Dockerfile does not install openssl")
    return problems


# ---------------------------------------------------------------- real files


def test_main_bicep_connects_callers_by_app_name():
    assert pgbouncer_host_violations(MAIN_BICEP.read_text(encoding="utf-8")) == []


def test_pgbouncer_offers_client_tls():
    assert (
        client_tls_violations(
            ENTRYPOINT.read_text(encoding="utf-8"),
            DOCKERFILE.read_text(encoding="utf-8"),
        )
        == []
    )


# ---------------------------------------------------- non-vacuity: can it fail


def test_host_check_flags_the_old_fqdn_wiring():
    old = (
        "var pgbouncerHost = pgbouncer.outputs.internalFqdn\n"
        "postgresFqdn: pgbouncerHost\n"
    )
    assert any("must be" in p for p in pgbouncer_host_violations(old))


def test_host_check_flags_a_literal_host_and_a_direct_fqdn_consumer():
    bad = (
        "var pgbouncerHost = 'ca-pgbouncer'\n"
        "    postgresFqdn: pgbouncer.outputs.internalFqdn\n"
    )
    problems = pgbouncer_host_violations(bad)
    assert any("must be" in p for p in problems)
    assert any("does not answer on 6432" in p for p in problems)


def test_host_check_allows_the_informational_output():
    ok = (
        "var pgbouncerHost = pgbouncer.outputs.appName\n"
        "output pgbouncerContainerAppInternalFqdn string = "
        "pgbouncer.outputs.internalFqdn\n"
    )
    assert pgbouncer_host_violations(ok) == []


def test_tls_check_flags_the_old_entrypoint_shape():
    old_entrypoint = (
        'cat > "$INI" <<EOF\n'
        "[pgbouncer]\n"
        "server_tls_sslmode = require\n"
        "admin_users = x\n"
        "EOF\n"
    )
    old_dockerfile = (
        "RUN apt-get update \\\n"
        "    && apt-get install -y --no-install-recommends pgbouncer "
        "ca-certificates \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
    )
    problems = client_tls_violations(old_entrypoint, old_dockerfile)
    assert len(problems) == 5, problems
