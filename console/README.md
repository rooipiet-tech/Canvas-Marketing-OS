# Canvas Marketing OS — operator console

Human-facing operator console: task queue, trace timeline, approval
inbox, Vault search, cost ledger, and a single kill-switch toggle. The
console is the ONLY externally-reachable read surface in the platform —
every other service stays internal-ingress-only — and sits behind Azure
Container Apps built-in authentication (Easy Auth) with a Microsoft Entra
ID identity provider, made fully secretless via a Federated Identity
Credential (see `.compound/learnings/security/L-0013.md` and
`infra/modules/console/console-app.bicep`).

The console never speaks SQL/Postgres directly (`SCOPE-005`). Reads route
through:

- the **vault-api** contract (mock by default; real once
  [session/s2-vault](#switching-vault-api-from-mock-to-real) merges),
- **Application Insights / Log Analytics** (direct KQL, managed-identity
  auth), and
- the **Gatekeeper** contract (mock by default; real once
  [session/s4-governance](#switching-gatekeeper-from-mock-to-real)
  merges).

## Routes (agent-native — `AGENT-001`)

All 6 routes are documented here so a plain HTTP client (no browser) can
drive the console exactly as a human would — send `Accept:
application/json` on any GET route to get structured data back instead of
HTML; the same authentication requirement (Easy Auth) applies to both.

| Method | Path | Purpose | Example |
|---|---|---|---|
| GET | `/tasks` | Task queue (agent runs) | `curl -H "Accept: application/json" https://<console-fqdn>/tasks` |
| GET | `/tasks/{task_ref}/trace` | Trace timeline for one task_ref | `curl -H "Accept: application/json" https://<console-fqdn>/tasks/<task_ref>/trace` |
| GET | `/approvals` | Approval inbox (read-only) | `curl -H "Accept: application/json" https://<console-fqdn>/approvals` |
| GET | `/vault-search` | Vault search, filtered by taxonomy dimension | `curl -H "Accept: application/json" "https://<console-fqdn>/vault-search?object_type=assets&vertical=mobility"` |
| GET | `/costs` | Cost ledger, grouped by function or day | `curl -H "Accept: application/json" "https://<console-fqdn>/costs?group_by=function"` |
| GET | `/kill-switch` | Current kill-switch state, including `state.last_audit_entry` (`GOAL-004`: operator, active, reason, decided_at for the most recent toggle) | `curl -H "Accept: application/json" https://<console-fqdn>/kill-switch` |
| POST | `/kill-switch/toggle` | **The only write-capable action in the console** (`CONSOLE-005`, `AGENT-002`) | `curl -X POST -H "Content-Type: application/json" -d '{"active":true,"reason":"incident"}' https://<console-fqdn>/kill-switch/toggle` |

Every request above requires a valid Easy-Auth session/token — an
unauthenticated request to any path (including `POST
/kill-switch/toggle`) returns `401` (`AUTH-002`, `AGENT-002`).

## Switching vault-api from mock to real

The console's `VaultApiClient` is a `Protocol`
(`console/app/clients/base.py`), implemented identically by
`VaultApiMock` (default) and `VaultApiHttpClient` (real, httpx-based). The
cutover to [session/s2-vault](https://github.com)'s real `vault-api`
service (contract: `contracts/vault-api.yaml`, once merged to main) is a
**single config change, no code change**:

```
VAULT_API_MODE=real
VAULT_API_BASE_URL=https://<vault-api Container App's internal FQDN>
```

(set as Container App env vars in `infra/modules/console/console-app.bicep`
or via `az containerapp update --set-env-vars`). See `INTEG-001`. At
rebase time, this session's mock shapes (`console/app/clients/
vault_api_mock.py`) must be re-verified against whatever actually merged
to main — merged contracts are authoritative over the branch snapshot
this session observed at `C:\Users\rooip\cmos-s2\contracts\vault-api.yaml`.

## Switching Gatekeeper from mock to real

The same pattern applies to `GatekeeperClient`
(`console/app/clients/gatekeeper_base.py`): `GatekeeperMock` (default,
explicitly labeled non-authoritative — see its module docstring) vs.
`GatekeeperHttpClient` (real). Cutover to
[session/s4-governance](https://github.com)'s real Gatekeeper flag-write
API, once it exists and merges to main, is again config-only:

```
GATEKEEPER_API_MODE=real
GATEKEEPER_API_BASE_URL=https://<gatekeeper Container App's internal FQDN>
```

See `INTEG-002`. As documented in `.loop/research.md`, no real
kill-switch write endpoint existed anywhere (even uncommitted) as of this
session — the mock's shape mirrors session/s4-governance's uncommitted
`governance.kill_switches` / `governance.approval_inbox` tables, observed
read-only, and must be re-verified at rebase time.

## Querying spans directly (agents) — `AGENT-003`

An agent can query Application Insights directly, bypassing the console's
own `/tasks/{task_ref}/trace` route entirely, via the exact KQL this
console uses internally (`console/app/clients/app_insights_client.py`'s
`build_trace_query` / `_QUERY_TEMPLATE` — the block below mirrors that
template's field ordering and `tostring()` usage exactly; if the two ever
drift, `_QUERY_TEMPLATE` is authoritative):

```bash
az monitor app-insights query -g cmos-dev -a <app-insights-name> \
  --analytics-query "union traces, dependencies, requests
    | where tostring(customDimensions.task_ref) == '<task_ref>'
    | project
        timestamp,
        name,
        function_id = tostring(customDimensions.function_id),
        task_ref = tostring(customDimensions.task_ref),
        model = tostring(customDimensions.model),
        registry_version = tostring(customDimensions.registry_version),
        cost = tostring(customDimensions.cost)"
```

`task_ref` must match `AppInsightsClient.TASK_REF_PATTERN`
(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`) — the console's own route rejects
anything else with `400` rather than building a query from it (`RISK-001`).

Note: `.github/workflows/deploy-console.yml`'s gated smoke job (its `(b)`
step) does NOT run this exact query — it runs a narrower
`operation_Id`-scoped count (`union traces,dependencies,requests | where
operation_Id == '<trace_id>' | count`) to confirm ingestion, not this
`task_ref`-scoped span projection. This `task_ref` query is instead
exercised by `console/tests/test_app_insights_client.py` and by the
console's own `/tasks/{task_ref}/trace` route in every request.

## Live verification happens in CI, not ad hoc

`INFRA-003`/`INFRA-004` (image pulled from the shared ACR; Application
Insights region/workspace linkage), `AUTH-001`/`AUTH-002` (secretless FIC
auth; unauthenticated rejection), and `GOAL-001` (synthetic trace
ingestion) are verified automatically inside
`.github/workflows/deploy-console.yml`'s gated `deploy` job on every push
to `console/**`, `services/telemetry-lib/**`, or
`infra/modules/console/**` that reaches `main`. A human approves the
`cmos-dev` GitHub Environment gate; everything downstream of that
approval is audited workflow-run output.

`GOAL-002` (cost-ledger reconciliation), `GOAL-004` (kill-switch
propagation + audit), and `AGENT-002` (programmatic kill-switch toggle
live half) are **NOT yet verified live in CI** — this is a documented,
not silently-assumed, residual gap: `deploy-console.yml`'s own header
comment and its `(c)`/`(d)` steps explain that these checks need an
authenticated request against the console's Entra-ID-gated Easy Auth
ingress, and no bearer-token mechanism for CI exists yet (it requires an
additional one-time manual Entra API-scope grant, not yet performed).
Until that exists, those specific checks are exercised by this repo's
unit/integration test suite (`console/tests/test_kill_switch_route.py`,
`console/tests/test_cost_ledger.py`) rather than against the live
environment.

## Local development

```bash
pip install -e ../services/telemetry-lib -e .
export VAULT_API_MODE=mock GATEKEEPER_API_MODE=mock
export CONSOLE_SEED_FIXTURES_JSON_B64=$(python tests/seed_fixtures.py)
uvicorn app.main:app --reload
```

## Testing

```bash
pytest tests
```

## Auth bootstrap (one-time, manual)

See `docs/console-auth-runbook.md` and `scripts/bootstrap-console-auth.sh`
for the one-time Entra App Registration + Federated Identity Credential
setup a human with directory admin rights must perform (`AUTH-003`).
