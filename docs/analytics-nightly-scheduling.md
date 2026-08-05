# Analytics nightly scheduling

How `caj-analytics-nightly-ingest` actually runs every night, how to
confirm its next scheduled run, the post-create identity/registry CLI
contract its image depends on, and how an agent (not just a human) can
operate it.

## Schedule-trigger rationale

The domain-expert-recommended default mechanism for a new nightly trigger
in this repo is an additive Logic App recurrence trigger under
`infra/modules/scheduling/` (the established platform pattern — see the
existing `dailySignalLoopTrigger`/`weeklyContentLoopTrigger` recurrence
triggers in `infra/modules/scheduling/logic-apps.bicep`). That mechanism
was considered and rejected for this session, not out of preference but
because it is structurally unreachable: `AC-01`'s frozen touch-scope regex
for this session's diff is

```
^(services/analytics-ingest/|analytics/|infra/modules/analytics/|infra/main\.bicep$|services/orchestrator/loops/[^/]+\.ya?ml$|docs/credentials-runbook\.md$|docs/.*analytics.*\.md$|\.github/workflows/analytics-image\.yml$)
```

— `infra/modules/scheduling/` is not in that list, and is therefore
completely off-limits for this session. A Schedule-triggered Container
Apps Job wholly inside `infra/modules/analytics/` (this session's own,
fully-owned directory) is the only mechanism available that is both (a)
capable of producing a REAL nightly execution and (b) spec-compliant with
the frozen touch-scope. This is genuinely unprecedented in this repo — as
of this session, every other Container Apps Job uses `triggerType:
'Manual'` — so it carries real risk of an undocumented Azure platform
quirk on first live deploy; this deviation was surfaced to, and explicitly
accepted by, the plan-reviewer at the plan-review gate (user amendment 3),
per `AC-26`'s verify logic (`infra/modules/analytics/nightly-ingest-job.bicep`
declares `triggerType: 'Schedule'`, which requires this rationale doc to
exist alongside it).

The orchestrator loop file
(`services/orchestrator/loops/nightly-analytics-ingest-loop.yaml`) is
therefore documentary/registry metadata describing the logical task graph
— it is not, today, the mechanism that actually fires the nightly run.

## Confirming the next scheduled run

`caj-analytics-nightly-ingest`'s cron expression is `0 1 * * *` (01:00
UTC / 03:00 SAST, daily). To confirm the schedule Azure has actually
persisted:

```
az containerapp job show \
  -g cmos-dev -n caj-analytics-nightly-ingest \
  --query "properties.configuration.scheduleTriggerConfig"
```

To see recent (and upcoming, via the most recent past) executions:

```
az containerapp job execution list \
  -g cmos-dev -n caj-analytics-nightly-ingest \
  --query "[].{name:name, status:properties.status, startTime:properties.startTime}"
```

## Post-create identity/registry CLI contract (L-0060/L-0061)

Neither `caj-analytics-nightly-ingest` nor `caj-analytics-buffer-smoke`
declares an `identity` or `registries[]` block in Bicep at all — a
`Microsoft.App/jobs` resource cannot have any user-assigned identity newly
attached in its own initial create call (confirmed live for
`caj-mcp-smoke`, see `L-0061`). `.github/workflows/analytics-image.yml`'s
gated `deploy` job is the sole, exclusive attacher, every deploy, via the
documented 2-step Microsoft workaround, in this order, for each job:

```
az containerapp job identity assign \
  -n <job-name> -g cmos-dev --user-assigned <id-analytics resource id>

az containerapp job registry set \
  -n <job-name> -g cmos-dev \
  --server <shared ACR login server> --identity <id-analytics resource id>
```

Both commands are idempotent and safe to repeat every deploy. Only after
both have run does `az containerapp job update --image ...:<sha>` (also
owned exclusively by that same gated deploy job) actually succeed against
the shared ACR.

## Agent-native CLI operability (AC-28)

An agent never needs a human-only UI step to trigger or inspect a run.

Local dry-run (no live DB/Azure Blob needed for the `run` subcommand;
`nightly --dry-run` still needs a reachable — throwaway/test is fine —
Postgres via `DATABASE_URL`, since it genuinely exercises every
DB-touching pipeline stage):

```
cd services/analytics-ingest
python -m analytics_ingest.cli run --source buffer --day 2026-07-31 --dry-run
python -m analytics_ingest.cli nightly --day 2026-07-31 --dry-run
```

`caj-analytics-buffer-smoke`'s own entrypoint
(`analytics_ingest.buffer_introspect`, see the "Buffer smoke job" section
below for the deployed path) can also be invoked locally the same way —
`assert_expected_fields()` fails loudly (never a silent pass) if the live
Buffer GraphQL schema is missing any field `buffer_client.py` assumes:

```
cd services/analytics-ingest
export BUFFER_API_KEY=<real Buffer API token>   # OR export KEY_VAULT_URI=<key vault URI> to resolve the
                                                 # buffer-api-key secret via DefaultAzureCredential instead
export BUFFER_API_URL=https://api.buffer.com/graphql   # optional — this is the default
python -m analytics_ingest.buffer_introspect
```

Required env vars: either `BUFFER_API_KEY` directly, or `KEY_VAULT_URI`
(resolved via `analytics_ingest.credentials.resolve_secret`'s dual-mode
fallback, reading the `buffer-api-key` Key Vault secret) — `run_introspection()`
raises `RuntimeError` immediately, uncaught, if neither resolves to a real
token (no fixture-mode fallback here by design — this smoke test only has
value against the live Buffer API).

Exit codes: `0` with `{"status": "ok", ...}` on stdout if every field
`buffer_client.ASSUMED_METRIC_FIELDS` assumes is present in Buffer's live
GraphQL schema; `1` with `{"status": "failed", "reason": ...}` on stdout
if any assumed field is missing (`assert_expected_fields()` failing
loudly, by design); an unhandled traceback (still exit `1`, but no JSON on
stdout) if credential resolution or the network call itself fails first.

Deployed path — start the job, then read its row-count output via logs:

```
az containerapp job start -g cmos-dev -n caj-analytics-nightly-ingest

az containerapp job execution list \
  -g cmos-dev -n caj-analytics-nightly-ingest \
  --query "[0].{name:name, status:properties.status}"

az containerapp job logs show \
  -g cmos-dev -n caj-analytics-nightly-ingest \
  --execution <execution-name> --container analytics-nightly-ingest
```

`--container` is mandatory even for this single-container job (`L-0024`)
— omitting it fails the command outright.

### Migration job (`caj-analytics-migrate`)

An agent asked to re-trigger the schema migration off-cycle (e.g. after
editing `services/analytics-ingest/migrations/0001_analytics_init.sql`
and redeploying) has the same start/list/logs path — the container name
is `analytics-migrate` (see
`infra/modules/analytics/migration-job.bicep`'s `template.containers[0].name`):

```
az containerapp job start -g cmos-dev -n caj-analytics-migrate

az containerapp job execution list \
  -g cmos-dev -n caj-analytics-migrate \
  --query "[0].{name:name, status:properties.status}"

az containerapp job logs show \
  -g cmos-dev -n caj-analytics-migrate \
  --execution <execution-name> --container analytics-migrate
```

This job runs `postgres:16` (never the shared `analytics-ingest:<sha>`
image) and applies the migration via `psql -v ON_ERROR_STOP=1`, so a
`Failed` execution's logs will show the exact SQL error. Safe to
re-trigger any time — the migration is idempotent (`IF NOT EXISTS`
throughout).

### Buffer smoke job (`caj-analytics-buffer-smoke`)

An agent asked to re-verify Buffer's live GraphQL schema off-cycle (e.g.
after Buffer changes their API, or before trusting a stale
`caj-analytics-buffer-smoke` deploy-time run) has the same start/list/logs
path — the container name is `analytics-buffer-smoke` (see
`infra/modules/analytics/buffer-smoke-job.bicep`'s
`template.containers[0].name`):

```
az containerapp job start -g cmos-dev -n caj-analytics-buffer-smoke

az containerapp job execution list \
  -g cmos-dev -n caj-analytics-buffer-smoke \
  --query "[0].{name:name, status:properties.status}"

az containerapp job logs show \
  -g cmos-dev -n caj-analytics-buffer-smoke \
  --execution <execution-name> --container analytics-buffer-smoke
```

This job runs `python -m analytics_ingest.buffer_introspect` (see the
local-invocation example above for what its stdout/exit codes mean) —
its `Failed` executions never gate CI/deploy (AC-34); this is purely an
independent, ad-hoc verification an agent can run any time.

## Polling caveat — 'Running' status lags real completion/crash (L-0056)

A Container Apps Job execution's `status` field lags real completion or
crash: an execution can show `Running` for a period after the container
has actually exited (successfully or not). Do not treat `Running` as
ground truth on its own. To get an accurate picture:

- Respect `replicaTimeout` (1800s for `caj-analytics-nightly-ingest`, 300s
  for `caj-analytics-buffer-smoke`) — a run that exceeds this is killed
  and marked `Failed`, but that transition is not always immediate in the
  execution list.
- Cross-check the job's `ContainerAppSystemLogs_CL` table in the
  associated Log Analytics workspace for the authoritative container exit
  event, e.g.:

  ```
  az monitor log-analytics query \
    --workspace <log analytics workspace id> \
    --analytics-query "ContainerAppSystemLogs_CL | where ContainerAppName_s == 'caj-analytics-nightly-ingest' | order by TimeGenerated desc | take 20"
  ```

- Only treat a run as genuinely done once `az containerapp job execution
  list`'s `properties.status` has settled to `Succeeded` or `Failed` AND
  the corresponding `ContainerAppSystemLogs_CL` exit event confirms it —
  never poll once and stop.

## Verifying via SQL

If job logs have expired (Log Analytics retention) or an agent just wants
an independent fallback that doesn't depend on log parsing at all, query
the `analytics` schema directly — every raw/rollup table carries a `day`
column:

```
psql "$DATABASE_URL" -c "SELECT day, count(*) FROM analytics.buffer_post_metrics WHERE day = '2026-07-31' GROUP BY day"

psql "$DATABASE_URL" -c "SELECT day, count(*) FROM analytics.utm_quarantine WHERE day = '2026-07-31' GROUP BY day"

psql "$DATABASE_URL" -c "SELECT day, kpi_name, source, post_archetype, engagement_rate FROM analytics.kpi_rollup_engagement_by_archetype WHERE day = '2026-07-31'"
```

Swap `analytics.buffer_post_metrics` for any of `ga4_metrics`,
`search_console_metrics`, `linkedin_metrics`, `scheduled_posts`,
`kpi_rollup_publishing_reliability`, `kpi_rollup_cost_per_accepted_asset`,
or `kpi_rollup_vault_utilisation` (see
`services/analytics-ingest/migrations/0001_analytics_init.sql` for the
full table list) to check any other stage's output. `$DATABASE_URL` is
the same connection string the Container Apps Jobs themselves use (Vault
admin credential on `cae-cmos-dev`'s Postgres server); a human with
network access to that server can run these directly, or an agent with a
reachable throwaway/test Postgres can compare row shapes without touching
the live instance at all.
