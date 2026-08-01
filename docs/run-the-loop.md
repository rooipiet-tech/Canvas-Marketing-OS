# Running the daily signal loop — operator runbook

This is a numbered runbook for a new, unaided operator with `az` CLI
access to the `cmos-dev` resource group (and, for the approval-click step,
an Entra ID account with directory access to this platform's tenant —
see `docs/console-auth-runbook.md`). Follow the steps in order. Every
command below is copy-pasteable as written; nowhere does it hardcode a
Container App FQDN — every address is resolved live, every time, exactly
because Container Apps FQDNs are environment-specific and can change on
redeploy (AC-19).

If any step's numbered outcome doesn't match, stop and re-read the
preceding step before continuing — later steps assume every prior step's
outcome held.

## 0. Prerequisites

- `az login` against the subscription hosting `cmos-dev`.
- `az extension add --name containerapp --yes` (idempotent if already
  installed).
- `jq` and `curl` (or `httpx`/`curl` equivalents) on your PATH.
- For any local test command against orchestrator, telemetry-lib, vault,
  or console code: use `py -3.12`, **never** bare `python`/`pytest`. This
  machine's bare `python` may resolve to an incompatible interpreter
  version; these four services pin `>=3.12,<3.13` in their
  `pyproject.toml`.

Set the resource group once for the session:

```
RESOURCE_GROUP=cmos-dev
```

## 1. Resolve every live service FQDN — never hardcode one

Container App FQDNs are assigned at deploy time and are not stable
literals to copy into scripts or docs. Resolve each one live, immediately
before you use it:

```
ORCH_FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n ca-orchestrator \
  --query properties.configuration.ingress.fqdn -o tsv)
GATEKEEPER_FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n ca-gatekeeper \
  --query properties.configuration.ingress.fqdn -o tsv)
GATEKEEPER_APPROVAL_FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n ca-gatekeeper-approval \
  --query properties.configuration.ingress.fqdn -o tsv)
PUBLISHER_FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n ca-publisher \
  --query properties.configuration.ingress.fqdn -o tsv)
CONSOLE_FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n ca-console \
  --query properties.configuration.ingress.fqdn -o tsv)
VAULT_FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n ca-vault \
  --query properties.configuration.ingress.fqdn -o tsv)

echo "orchestrator:        https://$ORCH_FQDN"
echo "gatekeeper (internal): https://$GATEKEEPER_FQDN"
echo "gatekeeper-approval:  https://$GATEKEEPER_APPROVAL_FQDN"
echo "publisher:            https://$PUBLISHER_FQDN"
echo "console:              https://$CONSOLE_FQDN"
echo "vault:                https://$VAULT_FQDN"
```

`ca-gatekeeper` is internal-only (no human-facing UI); `ca-gatekeeper-approval`
is the physically separate, Entra-protected app that hosts the actual
approve/reject click surface. Re-run this block whenever you start a new
session — do not cache these values across days.

## 2. Trigger a run

The daily signal loop (`services/orchestrator/loops/daily-signal-loop.yaml`)
runs automatically every day at **06:00 South Africa Standard Time**, fired
by the `la-daily-signal-loop-trigger` Logic App
(`infra/modules/scheduling/daily-signal-loop-trigger.bicep`), which POSTs a
heartbeat-event-shaped message onto the Service Bus `event` queue using its
own managed identity. You can either wait for that recurrence, or trigger
a run on demand right now with either of the two options below.

### Option A — run the Logic App trigger on demand

```
az logic workflow trigger run \
  -g "$RESOURCE_GROUP" \
  --workflow-name la-daily-signal-loop-trigger \
  --name Recurrence
```

This sends the exact same heartbeat message the 06:00 recurrence sends.

### Option B — run the caj-loop-e2e-smoke Container Apps Job (recommended for verification)

`caj-loop-e2e-smoke` (`infra/modules/orchestrator/loop-e2e-smoke-job.bicep`,
deployed by `.github/workflows/deploy-loop-e2e-smoke.yml`) publishes a
synthetic heartbeat and then polls `GET /runs/{task_ref}` (step 5 below)
until the S8 proof circuit reaches a terminal state — it is the fastest
way to both trigger and verify a run in one step. Start it manually with
the same `az rest` bare `StartJobExecutionTemplate` pattern the CI
workflow uses (never `az containerapp job start --yaml` — see
`docs/accepted-risks.md`'s Container Apps Job notes and `.compound/`
learnings L-0022/L-0023):

```
az containerapp job show -g "$RESOURCE_GROUP" -n caj-loop-e2e-smoke \
  --query "properties.template.containers" -o json > /tmp/loop-e2e-smoke-containers.json
jq -n --slurpfile containers /tmp/loop-e2e-smoke-containers.json \
  '{containers: $containers[0]}' > /tmp/loop-e2e-smoke-start.json

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
START_RESPONSE=$(az rest --method post \
  --url "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.App/jobs/caj-loop-e2e-smoke/start?api-version=2024-03-01" \
  --body @/tmp/loop-e2e-smoke-start.json)
EXEC_NAME=$(echo "$START_RESPONSE" | jq -r '.name // empty')
echo "started execution: $EXEC_NAME"
```

Poll it to completion:

```
az containerapp job execution list -g "$RESOURCE_GROUP" -n caj-loop-e2e-smoke \
  --query "[?name=='$EXEC_NAME'].properties.status" -o tsv
```

Once it reports `Succeeded` or `Failed`, dump its logs (an explicit
`--container` is required even for this single-container job — L-0024):

```
az containerapp job logs show -g "$RESOURCE_GROUP" -n caj-loop-e2e-smoke \
  --execution "$EXEC_NAME" --container loop-e2e-smoke > /tmp/loop-e2e-smoke.log
cat /tmp/loop-e2e-smoke.log
jq -R 'fromjson? | .Log // empty' /tmp/loop-e2e-smoke.log 2>/dev/null
```

The job's own console output includes the `request-linkedin-approval`
task_id it derived — you'll need that `task_ref` for step 5.

## 3. Locate the approval card

Open `https://$CONSOLE_FQDN/approvals` in a browser (Entra sign-in
required — see `docs/console-auth-runbook.md` if this is your first time).
This renders the same six columns `console/app/templates/approvals.html`
always renders: **Function**, **Action class**, **Level**, **Preview**,
**Status**, **Decided by**.

**Important — how to tell the S8 proof circuit's card apart from a real
production approval request.** By design (`.loop/spec.json` AC-30/AC-31),
the proof circuit's `request-approval` task issues its gate-token request
against the *real* production `function_id` (`publish.social_post`) and
the *real* `action_class` (`publish`) — it deliberately does not invent a
fake function_id, so it exercises the actual production autonomy policy
path. That means **Function**, **Action class**, and **Level** are
byte-identical to a genuine production publish request; they will not
help you tell them apart.

The **only** field that distinguishes a proof-circuit card is the
**Preview** column (`preview_title`): the proof circuit's request-approval
handler (`services/orchestrator/orchestrator/dispatch.py`) prefixes it
with a literal `[LOOP-PROOF] ` marker, e.g.:

```
[LOOP-PROOF] publish.social_post (publish)
```

A genuine production request's Preview column will never start with that
literal string. If you are triaging the approvals table and are not
certain whether a row is the proof circuit or a real request, check this
one field before approving anything.

(A second, non-visual tag — `preview_reference`, formatted as
`loop-proof://<task_ref>` — is also set on the same row, but console's
`approvals.html` does not render `preview_reference` in the UI; it exists
for programmatic/API consumers, e.g. step 5's `GET /runs/{task_ref}` and
the costs-ledger rows, not for a human scanning this page.)

## 4. Approve the card

Click **Approve** on the `[LOOP-PROOF]`-marked row. This calls the
Entra-protected `ca-gatekeeper-approval` app's action-link endpoint, which
mutates the `governance.approval_inbox` row's status to `approved` in
place (`services/gatekeeper/app/approval_inbox.py`'s `consume_link`).

Note what this step does *not* do: it does not itself mint a gate token.
The proof circuit's `request-approval` orchestrator task already
completed (as `COMPLETED`) the instant its own `/gate-check` call
returned `escalated` — long before you clicked Approve. Approving here
only changes the human decision status; a **second** `/gate-check` call
is required to actually mint the now-approved token (next step).

## 5. Re-call `/gate-check` to mint the approved gate token

`POST /gate-check` on `ca-gatekeeper` for the **same**
`(agent_run_id, function_id, content_hash)` triple the proof circuit's
`request-approval` task originally used. Gatekeeper's
`services/gatekeeper/app/routers/gate_check.py` looks up
`latest_approved(...)` for that exact triple; since step 4's approval is
now on record, this second call returns `outcome: "approved"` with a real
`gate_token` (its first call, before you approved, only ever returned
`outcome: "escalated"` with no token — a gate token is issued only on an
`approved` outcome, never on `escalated`).

You can read the original triple's values back from
`GET /runs/{task_ref}` (step 6) — the `request-linkedin-approval` stage's
`result_ref` carries `agent_run_id`, `function_id`, and `content_hash`.
Then:

```
curl -s -X POST "https://$GATEKEEPER_FQDN/gate-check" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_run_id": "<agent_run_id from result_ref>",
    "function_id": "publish.social_post",
    "action_class": "publish",
    "content_hash": "<content_hash from result_ref>"
  }' | jq .
```

Confirm the response's `outcome` is `approved` and `gate_token` is
non-null. Save `gate_token` — you need it for the next step.

You can independently confirm the human decision status at any time,
without a browser, via `ca-gatekeeper`'s `GET /approval-status`:

```
curl -s "https://$GATEKEEPER_FQDN/approval-status?agent_run_id=<agent_run_id>&function_id=publish.social_post" | jq .
```

## 6. POST the Publisher's `/publish` in dry-run mode — with `asset_id` set

This is the step that actually demonstrates the proof circuit's isolation
mechanism, so do not skip the `asset_id` field.

`services/publisher/app/vault_lookup.py` forces `dry_run=True`
**regardless of the global `PUBLISHER_DRY_RUN` flag's value** whenever
the published asset's Vault-recorded `agent_run.agent_name` equals
`loop-proof-circuit` (`AGENT_NAME_LOOP_PROOF` in both
`services/orchestrator/orchestrator/dispatch.py` and
`services/publisher/app/config.py`) — but it can only do that lookup if
you supply `asset_id` on the request. `asset_id` is `PublishRequest`'s
existing-but-optional field (`services/publisher/app/models.py`); if you
omit it, Publisher never attempts the Vault cross-check at all (fully
backward-compatible with every existing caller, e.g. `caj-governance-smoke`,
which never sets it) and falls through to ordinary
`PUBLISHER_DRY_RUN`-flag-driven behavior instead — which would **not**
exercise the proof circuit's isolation guarantee.

Read `vault_asset_id` and `content_hash` off the proof circuit's
`draft-linkedin-post` (`draft-content`) stage's `result_ref` — again
visible via `GET /runs/{task_ref}` (step 6/step 7 below share the same
call). Then:

```
ASSET_BYTES_B64=$(curl -s "https://$VAULT_FQDN/assets/<vault_asset_id from draft-content result_ref>" \
  | jq -r '.content_base64')

curl -s -X POST "https://$PUBLISHER_FQDN/publish" \
  -H 'Content-Type: application/json' \
  -d "{
    \"agent_run_id\": \"<agent_run_id from request-approval result_ref>\",
    \"function_id\": \"publish.social_post\",
    \"asset_bytes_b64\": \"$ASSET_BYTES_B64\",
    \"gate_token\": \"<gate_token from step 5>\",
    \"asset_id\": \"<vault_asset_id from draft-content result_ref>\"
  }" | jq .
```

Expect `outcome: "published"`, `reason: "published_dry_run"`. No HTTP
call ever reaches mcp-buffer's `create_draft` on this path — a distinct
`published_dry_run` `governance.publish_attempts` row is written instead.
This holds true even if an operator has separately set
`PUBLISHER_DRY_RUN=false` on `ca-publisher` (AC-08(e)/AC-30) — the
proof circuit's Buffer stage is structurally, permanently dry-run.

## 7. Verify via `GET /runs/{task_ref}`

From a plain script — no browser, no interactive Entra session required —
confirm the whole chain traced under one trace id and inspect the real
approval decision status:

```
curl -s "https://$ORCH_FQDN/runs/<request-linkedin-approval task_id>" | jq .
```

The response lists every stage in the proof circuit's lineage (walking
`depends_on` backward from the given `task_ref`: `request-linkedin-approval`
-> `content-qa-review` -> `draft-linkedin-post` -> `qa` -> ... -> the
daily loop's `ingest-signals`/`draft-brief` stages), each with its task
`state` and `result_ref`, a best-effort `span_presence` field (`present`
/ `absent` / `not_checked`, depending on whether App Insights is
configured), and — this is the field that actually matters for AC-15 —
an `approval_decision_status` object sourced from Gatekeeper's REAL
`governance.approval_inbox` row, distinct from the `request-linkedin-approval`
task's own (always-`COMPLETED`-once-issued) task state:

```json
{
  "task_ref": "request-linkedin-approval",
  "stage_count": 7,
  "stages": [ ... ],
  "span_presence": "present",
  "approval_decision_status": {
    "status": "approved",
    "decided_by": "<the Entra principal who clicked Approve>",
    "decided_at": "2026-08-01T06:14:02Z"
  }
}
```

Before step 4, `approval_decision_status.status` reads `pending`; after
step 4, `approved` (or `rejected`, had you rejected instead). This is the
one place in the whole chain where "the task finished" and "a human
actually approved" are visibly, deliberately different things — do not
confuse `request-linkedin-approval`'s task `state` (`COMPLETED` the
instant `/gate-check` responded, long before any human acted) with
`approval_decision_status.status` (the real decision).

## Cost budget

Every model call this loop makes is metered into the costs table. The
budget this run is checked against is the named constant
**`DAILY_LOOP_BUDGET_USD`**, defined in
`services/orchestrator/orchestrator/config.py` (env-var-overridable via
`CMOS_DAILY_LOOP_BUDGET_USD`, documented default `5.00`). If you are
building alerting or a dashboard on top of this runbook, cite
`DAILY_LOOP_BUDGET_USD` by name as the single source of truth for that
threshold — do not hardcode `5.00` (or any other number) anywhere else.

## Local test commands

Run these from the repo root. All local-test commands for orchestrator,
telemetry-lib, vault, and console code use `py -3.12` — never bare
`python`/`pytest`:

```
py -3.12 -m pytest tests/e2e -v
py -3.12 -m pytest services/orchestrator/tests -v
py -3.12 -m pytest services/publisher/tests -v
py -3.12 -m pytest services/gatekeeper/tests -v
py -3.12 -m pytest services/vault/tests -v
py -3.12 -m pytest console/tests -v
```

`tests/e2e` is designed to **skip cleanly** (exit code 0, zero
failures/errors, every test reported `skipped`) when no live Azure
credentials / `DATABASE_URL` are configured — see `tests/e2e/conftest.py`.
It only runs for real, against the live `cmos-dev` environment, in CI
with credentials present.

## Proof Circuit Lifecycle

The three tasks this session appended to the END of
`services/orchestrator/loops/daily-signal-loop.yaml` —
`draft-linkedin-post` (`draft-content`), `content-qa-review`
(`qa-review`), and `request-linkedin-approval` (`request-approval`) — are
a deliberately **throwaway, permanently-dry-run proof circuit**. They
exist solely to exercise the real
signal -> brief -> draft -> QA -> approval-card path against the live
platform end to end, once, as evidence the dispatch mechanism this
session built actually works outside a unit test. They are **not** a
second production publishing path, and they must never be treated as
one — S11's `services/orchestrator/loops/weekly-content-loop.yaml` is,
and remains, the production content pipeline. This session's build never
modifies that file (`.loop/spec.json` AC-26/AC-32).

**Removal trigger**: once `weekly-content-loop.yaml` has run live, end to
end, successfully, at least once — i.e. once S11's real production
content pipeline has proven itself in production — an operator should
delete the proof circuit. The exact block to remove is clearly labeled in
`daily-signal-loop.yaml`:

```
# --- S8 PROOF CIRCUIT (function-42, permanently dry-run; remove once
# weekly-content-loop.yaml has run live once -- see docs/run-the-loop.md
# "Proof Circuit Lifecycle") ---
  ...
# --- END S8 PROOF CIRCUIT ---
```

Delete everything between (and including) those two marker comments —
the three `task_id` entries (`draft-linkedin-post`, `content-qa-review`,
`request-linkedin-approval`) and their surrounding explanatory comment
block — and nothing else. This reverts `daily-signal-loop.yaml` to
signals + brief only, its pre-proof-circuit shape. Do not delete this
runbook's steps 3-7 at the same time — they remain valid for verifying
any future `request-approval` task, proof-circuit or not; only the
"how to tell it apart" callout in step 3 becomes moot once the circuit
itself is gone.

Do not leave the proof circuit in place indefinitely "just in case" — its
whole purpose is to be thrown away once it has done its job; leaving it
running forever would mean two loops effectively drafting content, with
this one existing only to be silently ignored, which is worse than not
having it.
