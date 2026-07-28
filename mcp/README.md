# Canvas Marketing OS — MCP tool plane

Three MCP servers — `mcp-web`, `mcp-buffer`, `mcp-canva` — plus shared
libraries (`mcp/common`), a session-owned Postgres schema for tool-call
logging (`mcp/mcp_ops`), and the pytest suite that verifies all of it
(`mcp/tests`).

All three servers implement the same minimal MCP-over-HTTP surface (no
external MCP SDK dependency — see `mcp/common/mcp_common/protocol.py`):
`POST /mcp` with a JSON-RPC 2.0 body, methods `initialize`, `tools/list`,
`tools/call`; plus `GET /health`.

## Build and run

Each server (`mcp-web`, `mcp-buffer`, `mcp-canva`) is a small FastAPI app,
mirroring `services/model-gateway`'s shape:

```
mcp/<server>/
  pyproject.toml
  requirements.txt      # includes `-e ../common` (mcp_common)
  Dockerfile             # build context MUST be mcp/, e.g.:
                         #   docker build -f mcp/mcp-web/Dockerfile mcp/
  app/main.py            # FastAPI app + MCPServer wiring
  app/dispatch.py|tools.py  # tool implementations
  tools.yaml              # manifest (validates against
                           # contracts/function-definition/tools.schema.json)
  fixtures/*.json          # synthetic-only (every file carries "_synthetic": true)
  smoke_test.py            # standalone AC-6 smoke script
```

Local dev (from repo root):

```bash
pip install -e mcp/common
pip install -r mcp/mcp-web/requirements.txt
uvicorn app.main:app --app-dir mcp/mcp-web --port 8080
```

## Dual-mode: fixture vs. live

Fixture mode is the default and requires nothing — no secret, no flag, no
config edit. Every server switches to live mode purely by an env var's
presence (AC-4); no code or config file edit is ever needed.

| Server      | Live-mode env var(s)                          | Real Key Vault secret name(s)         |
|-------------|------------------------------------------------|----------------------------------------|
| mcp-web     | `MCP_WEB_LIVE_MODE` (non-secret flag, truthy)   | none — mcp-web has no vendor credential (orchestrator-approved waiver, `.loop/spec.json` amendments v2, addressing plan-reviewer finding F3) |
| mcp-buffer  | `BUFFER_API_KEY`                                | `buffer-api-key`                        |
| mcp-canva   | `CANVA_CLIENT_ID` **and** `CANVA_CLIENT_SECRET` | `canva-client-id`, `canva-client-secret` |

Note: the real Key Vault secret names above are the ones actually loaded
into `kv-cmos-dev-*` per environment facts — `docs/credentials-runbook.md`
documents stale/incomplete names for Buffer/Canva (`buffer-access-token`,
`canva-client-secret`-only) and is known to be out of date; fixing it is
out of this build's touch-scope (see `.loop/spec.json`'s `out_of_scope`).

Other tunables (env vars, never hardcoded — CO-1):

- `MCP_WEB_ALLOWLIST` — comma-separated egress allow-list for mcp-web's
  `fetch_url` tool (default `example.com,api.example.com`).
- `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW_SEC` — mcp-web's sliding-window
  rate limiter ceiling/window (defaults `5` / `1.0`).
- `DATABASE_URL` — Postgres connection string for `mcp_ops.tool_calls`
  logging (best-effort: if unset or unreachable, logging is silently
  skipped and the tool call still succeeds — a Postgres outage must never
  take down a tool call).
- `KEY_VAULT_URI` — optional; enables `mcp_common.credentials.resolve_secret`'s
  direct Key Vault SDK fallback lookup when the secret's own env var is
  unset (the deployed Container Apps instead receive secrets via a native
  Key-Vault-backed Container Apps `secretRef`, wired in
  `infra/modules/mcp/container-app.bicep` — this fallback is a convenience
  for anything that wants direct SDK resolution instead).

## Canva OAuth consent helper

`canva-refresh-token` does not exist as a Key Vault secret yet. To obtain
one:

```bash
python mcp/mcp-canva/scripts/oauth_consent.py --help
python mcp/mcp-canva/scripts/oauth_consent.py \
  --client-id <canva-client-id value> \
  --client-secret <canva-client-secret value>
```

This runs a local OAuth2+PKCE consent flow (redirect
`http://127.0.0.1:8484/oauth/redirect`), and prints the resulting
`refresh_token` for the operator to load into Key Vault themselves via
the existing gated in-VNet secret-loading path (see `.compound` learning
L-0012) — the script never touches Key Vault directly. Its absence never
breaks anything else: mcp-canva runs happily in fixture mode with no
`CANVA_ACCESS_TOKEN`/refresh token present at all.

## Running the test suite

```bash
pip install -e mcp/common
pip install -r mcp/requirements-test.txt
cd mcp
pytest -m mcp_conformance -v      # AC-1
pytest -m mcp_buffer_surface -v   # AC-2/AC-3
pytest -m mcp_mode_switch -v      # AC-4
pytest -m mcp_rate_limit -v       # AC-5
pytest -m mcp_smoke -v            # AC-6
pytest -m mcp_logging -v          # AC-9 (Postgres required — see below)
pytest -m mcp_manifest_schema -v  # AC-14
pytest -m mcp_agent_e2e -v        # AC-15 (Postgres required — see below)
pytest -m mcp_canva_surface -v    # AC-16
pytest -m mcp_web_allowlist -v    # AC-17
```

### Required local Postgres prerequisite (mcp_logging, mcp_agent_e2e)

`mcp_logging` and `mcp_agent_e2e` need a real, reachable Postgres with
`mcp/mcp_ops/schema.sql` applied. `conftest.py`'s `pg_conn`/`database_url`
fixtures **skip** (not fail) these tests when Postgres is unreachable —
that graceful-skip behavior exists only so casual/partial local runs
don't hard-fail on an unrelated missing dependency. **A skipped run is
NOT a valid verification of AC-9/AC-15** — it must not be reported as a
pass.

Stand up Postgres exactly like `ci.yml`'s `migration-test` job does:

```bash
docker run -d --name mcp-test-pg -e POSTGRES_USER=cmos \
  -e POSTGRES_PASSWORD=cmos_ci_password -e POSTGRES_DB=cmos \
  -p 5432:5432 postgres:16

export DATABASE_URL=postgresql://cmos:cmos_ci_password@localhost:5432/cmos
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f contracts/vault-schema/schema.sql
# mcp/mcp_ops/schema.sql is also applied automatically, idempotently, by
# conftest.py's pg_conn fixture on first use — applying it here yourself
# first is optional, not required.

cd mcp && pytest -m mcp_logging -rs -v
cd mcp && pytest -m mcp_agent_e2e -rs -v
```

To self-enforce the zero-skipped rule mechanically instead of eyeballing
the summary line, run:

```bash
bash mcp/scripts/run_required_checks.sh
```

This runs every marker above with `-rs` and fails (non-zero exit) if
**any** test anywhere is reported skipped — including deliberately, if
`DATABASE_URL` is left unset (that failure is the self-enforcement
working as intended, not a bug in the script).

### AC-15 end-to-end recipe (documented, purely programmatic)

`mcp/tests/test_agent_e2e.py` is the runnable recipe: for each of the 3
servers it (1) opens an MCP client (in-process `TestClient`, or a real
`httpx.Client` against a deployed FQDN if `<SERVER>_BASE_URL` is set —
see `conftest.py`'s `mcp_client_factory`), (2) calls `tools/list`, (3)
calls one fixture-backed tool via `tools/call`, (4) queries
`mcp_ops.tool_calls` for the row matching that call's arguments hash, (5)
asserts the row's `outcome` is `'success'`. No GUI/human-only step
anywhere in that path.

## In-VNet deployed smoke (AC-20)

The identical `mcp_conformance` test file also runs as `caj-mcp-smoke`'s
one-shot Container Apps Job payload (`mcp/Dockerfile.smoke`), pointed at
the deployed Container Apps' internal FQDNs via `MCP_WEB_BASE_URL` /
`MCP_BUFFER_BASE_URL` / `MCP_CANVA_BASE_URL` — see
`infra/modules/mcp/mcp-smoke-job.bicep` and
`.github/workflows/deploy-mcp.yml`.

## Cross-session coordination record

Per plan step 1/16's recon check, at the time this build ran:

- `git log origin/main -- infra/modules/container-registry.bicep` was
  empty -> **ACR_MODE=author**. This build authored
  `infra/modules/container-registry.bicep` (Basic SKU, admin disabled,
  managed-identity pull only).
- `git log origin/main -- contracts/function-definition/tools.schema.json`
  was empty -> **TOOLS_SCHEMA_MODE=author**. This build authored
  `contracts/function-definition/tools.schema.json` plus its
  `contracts/.frozen-v1.sha256` hash line and the minimal
  `scripts/validate_contracts.py` companion edit.

Both are re-checked at the pre-PR rebase step; if either file landed on
`main` first in the meantime, this build's copy is dropped in favor of
main's per the ownership rule in `.loop/spec.json`'s ruling R2/AC-21/AC-22.

## Known operational gap

None of this suite's 10 pytest markers are wired into `.github/workflows/ci.yml`
today — `ci.yml`'s existing jobs (`lint`, `validate-contracts`,
`migration-test`) do not touch `/mcp` at all, and extending `ci.yml` is
outside this build's touch-scope (`.loop/spec.json`'s `touch_scope` names
`.github/workflows/deploy-mcp.yml` as the only new workflow file
authorized; all other workflow files, including `ci.yml`, remain
untouched). The only automated coverage this suite gets today is:

1. Whatever a human/agent runs locally via `pytest -m <marker>` or
   `mcp/scripts/run_required_checks.sh`.
2. `caj-mcp-smoke`'s `mcp_conformance`-only subset, run once per
   `deploy-mcp.yml` deploy (AC-20) — not the other 9 markers.

**Recommended follow-up for a future wave** (requires touch-scope
authorization for `.github/workflows/ci.yml`): add an `mcp-tests` job to
`ci.yml` mirroring `migration-test`'s Postgres-service-container pattern,
running `mcp/scripts/run_required_checks.sh` (or the individual marker
commands) on every push to `main`, so this gap closes without relying on
manual/local verification.
