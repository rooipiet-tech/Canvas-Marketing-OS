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

All 6 GET routes (plus the one write action below) are documented here so
a plain HTTP client (no browser) can drive the console exactly as a human
would — send `Accept: application/json` on any GET route to get
structured data back instead of HTML; the same authentication requirement
(Easy Auth) applies to both.

| Method | Path | Purpose | Example |
|---|---|---|---|
| GET | `/tasks` | Task queue (agent runs) | `curl -H "Accept: application/json" https://<console-fqdn>/tasks` |
| GET | `/tasks/{task_ref}/trace` | Trace timeline for one task_ref | `curl -H "Accept: application/json" https://<console-fqdn>/tasks/<task_ref>/trace` |
| GET | `/approvals` | Approval inbox (read-only) | `curl -H "Accept: application/json" https://<console-fqdn>/approvals` |
| GET | `/vault-search` | Vault search, filtered by taxonomy dimension | `curl -H "Accept: application/json" "https://<console-fqdn>/vault-search?object_type=assets&vertical=mobility"` |
| GET | `/costs` | Cost ledger, grouped by function or day | `curl -H "Accept: application/json" "https://<console-fqdn>/costs?group_by=function"` |
| GET | `/kill-switch` | Current kill-switch state, including `state.last_audit_entry` (`GOAL-004`: operator, active, reason, decided_at for the most recent toggle) | `curl -H "Accept: application/json" https://<console-fqdn>/kill-switch` |
| POST | `/kill-switch/toggle` | **The only write-capable action in the console** (`CONSOLE-005`, `AGENT-002`) | `curl -X POST -H "Content-Type: application/json" -d '{"active":true,"reason":"incident"}' https://<console-fqdn>/kill-switch/toggle` |

`GET /` is a plain redirect to `/tasks` (the landing screen a browser user sees
after Easy Auth login) — not counted among the 6 agent-native JSON routes
above, since it carries no data of its own; an agent should go straight to
`/tasks` rather than following the redirect.

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
or via `az containerapp update --set-env-vars`). See `INTEG-001`.

**Rebase re-verification (2026-07-31, `contracts/vault-api.yaml` now
merged to main):** `console/app/clients/vault_api_mock.py`'s shapes were
compared field-by-field against the merged contract — no drift found.
`TaxonomyFields` (`vertical`, `function_id`, `campaign`, `evidence_grade`,
`consent_status`, `retention_class`), `AgentRun` (`agent_name`, `status`,
`input`, `output`, `started_at`, `completed_at`), and `Cost`
(`agent_run_id`, `provider`, `unit`, `amount`, `incurred_at`) all match the
mock exactly; `GET /assets` correctly returns `AssetSummary` (no
`content_base64`), matching the mock's own seed shape. `VaultApiHttpClient`
needs no code change — the cutover really is config-only as documented
above.

## Switching Gatekeeper from mock to real

The same `Protocol`/mock/real split exists for `GatekeeperClient`
(`console/app/clients/gatekeeper_base.py`): `GatekeeperMock` (default,
explicitly labeled non-authoritative) vs. `GatekeeperHttpClient` (real,
`console/app/clients/gatekeeper_real.py`).

**Rebase re-verification (2026-07-31) — IMPORTANT CORRECTION to the
config-only framing above:** session/s4-governance has now merged to
main, and its Gatekeeper (`services/gatekeeper/`) and Publisher are live
and smoke-proven — but merging did **not** make this cutover config-only.
The real, merged `services/gatekeeper/main.py` mounts only two routers,
`gate_check` and `decisions`; a separate app, `approval_main.py`
(`ca-gatekeeper-approval`), mounts one more, `approval_action` (the
single-use token-based approve/reject click handler). **None of these
expose a list-all-pending-approvals or read/toggle-kill-switch HTTP
route.** `governance.kill_switches` and `governance.approval_inbox` are
real, merged Postgres tables (`infra/modules/governance/migrations/
0001_governance_init.sql`), but `app/kill_switch.py`'s `is_blocked()` and
`app/approval_inbox.py`'s list/read helpers are called only internally by
`gate_check`/`approval_action` — there is no REST wrapper around them
anywhere in the repo yet, on any merged or unmerged branch.

Consequently `GatekeeperHttpClient`'s four calls (`GET /kill-switch`,
`POST /kill-switch/toggle`, `GET /kill-switch/audit/last`,
`GET /approval-inbox`) target routes that do not exist yet. **INTEG-002's
cutover precondition is therefore not just "session/s4-governance merges"
but "a REST API is built on top of `kill_switch.py`/`approval_inbox.py`
and merges"** — tracked as follow-up work, not assumed complete by this
session (`GATEKEEPER_API_MODE` stays `mock` in `console-app.bicep`).

Column-level good news: the now-real, merged table schemas confirm the
mock's shape is still accurate for what it models.
`GatekeeperMock.seed_approval_inbox`'s fields (`id`, `agent_run_id`,
`function_id`, `action_class`, `level`, `preview_title`,
`preview_reference`, `evidence_summary`, `status`, `decided_by`,
`decided_at`, `created_at`) are a strict subset of
`governance.approval_inbox`'s real columns (omitting only
`gate_decision_id`, `content_hash`, `link_token`, `link_consumed_at`,
`expires_at` — internal/security-sensitive fields the console's read-only
inbox view has no reason to surface). One real scope gap worth flagging
for whoever builds the S8 REST wrapper: `governance.kill_switches`
supports **per-function** switches (`scope='function'`, one row per
`function_id`) in addition to the global one, but
`GatekeeperMock.get_kill_switch_state` models only a single global
switch — the console's kill-switch screen currently has no way to show or
toggle a function-scoped switch. This matches the frozen spec's GOAL
wording ("kill-switch state") and is not a defect in this session's
scope, but the future real wrapper/console screen should decide whether
to expose function-scoped switches too.

Once that REST API exists and merges, the cutover is the same env-var
flip documented above:

```
GATEKEEPER_API_MODE=real
GATEKEEPER_API_BASE_URL=https://<gatekeeper Container App's internal FQDN>
```

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
