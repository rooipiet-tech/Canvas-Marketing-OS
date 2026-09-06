# 20 — Multi-Tenancy Decision Memo

**Status: decision required. No schema, migration, or code work has started.**
This document is the costing `09-technical-debt.md` TD-05 and
`07-operating-model.md` §D.3 both point to and defer. It does not choose an
option — that is a commercial decision about ops budget, target customer
count, and time-to-market, not an engineering one. Part 1 scores the three
options against this platform's *actual* current schema, re-verified against
the real files rather than against TD-05's original estimate. Part 2 sketches
the migration plan for the option the evidence favours, detailed enough for a
future session to execute — **but explicitly not authorised to start** until
a business owner signs off on Part 1.

---

## Part 1 — Decision memo

### 1.1 Re-verifying TD-05's numbers

TD-05 and `07-operating-model.md` §D.3 both cite **27 tables**. That figure is
stale. `docs/architecture/04-data-model.md` already carries the corrected,
actively-maintained count — **39 tables across 5 Postgres schemas** — and this
memo re-derived it independently from the migration files themselves to
confirm it:

| Schema | Tables | Source file(s) |
|---|---:|---|
| `public` — frozen v1 | 9 | `contracts/vault-schema/schema.sql` |
| `public` — orchestrator, additive | 2 | `services/orchestrator/migrations/0001_orchestrator_init.sql` |
| `public` — options inbox, additive v2 | 3 | `services/vault/migrations/0002_options_inbox_init.sql` |
| `vault_internal` | 7 | `services/vault/migrations/0001_vault_internal_init.sql` |
| `governance` | 6 | `infra/modules/governance/migrations/0001_governance_init.sql` |
| `analytics` | 11 | `services/analytics-ingest/migrations/0001_analytics_init.sql` |
| `mcp_ops` | 1 | `mcp/mcp_ops/schema.sql` |
| **Total** | **39** | |

The gap between 27 and 39 is not a counting error in TD-05 so much as a
timing one: TD-05 predates the options-inbox v2 addition (Appendix D PR 1),
the orchestrator's own `task_state`/`task_transitions` tables, and `mcp_ops`
— all three landed as *additive* schemas specifically designed to never touch
the frozen file (each migration's own header says so explicitly). **TD-05
should be updated to cite 39, not 27** — recommended as a one-line follow-up
alongside whatever this memo's decision turns out to be.

One more table exists outside this count on purpose:
`services/model-gateway/migrations/0001_completions_init.sql` creates
`completions` (TD-06's idempotency-cache table, keyed on `task_ref`), which
`04-data-model.md` itself never catalogues — it is a pure response-replay
cache with no business-object shape, not part of the "5 schemas" data model.
It is addressed separately in §1.4 because it raises its own tenancy question.

Confirmed independently: `grep -rn "tenant_id\|org_id\|workspace_id"` across
every `.sql` file in the repo returns zero matches. TD-05's core claim holds
exactly as stated.

### 1.2 Only one of these files is actually frozen

`scripts/validate_contracts.py`'s `FROZEN_FILES` list — the thing
`contracts/.frozen-v1.sha256` actually guards — names exactly one DDL file:
`vault-schema/schema.sql` (9 tables). The other 30 tables were built, table
by table, specifically to avoid ever touching it: `vault_internal`,
`governance`, `analytics`, the orchestrator tables, `mcp_ops`, and the
options-inbox tables all carry a near-identical comment header explaining
that they land in a new schema, or in `public` as a new table, *because* the
frozen file cannot be edited. That pattern is exactly right for schema
evolution and it is also the reason the tenancy question is has structural
teeth: three-quarters of the schema was already built around a
freeze-avoidance discipline, and any tenancy design has to keep respecting
it, not just for the original 9 tables.

Two more frozen files matter more than the DDL does, because they are JSON
Schema documents with `"additionalProperties": false`:

- `contracts/gate-token/schema.json` — the JWT gate-token claim set.
- `contracts/service-bus/task-envelope.schema.json` — the async task
  envelope every Service Bus message carries.

`additionalProperties: false` is a stricter guarantee than the SQL file's:
a nullable SQL column can be added additively (the precedent already exists
— `opportunity_cards.pillar`/`so_what`/`source_url`/`confidence` were added
this way, baseline refreshed via `--write-baseline`, still v1). A JSON Schema
with `additionalProperties: false` cannot gain a new top-level property
without a schema-file edit at all — there is no additive path. This is not
new debt this memo is inventing: `contracts/gate-token/spec.md`'s own
"Forward plan" section already states that `function_id` and `content_hash`
"graduate to first-class top-level claims" only "at the first v2 contract
window," and that doing so before then is "explicitly out of scope for v1."
TD-14 costs that exact window at "~2 weeks + migration." A `tenant_id` claim
is the same shape of change, riding the same forward plan.

### 1.3 Table-by-table: what actually needs a new column

The naive assumption — "add `tenant_id` to `campaigns`, everything else
joins through `campaign_id`" — does not hold. Walking the real FK graph in
each migration file:

| Bucket | Count | Tables |
|---|---:|---|
| **Root anchor** (gets `tenant_id` directly, by design) | 1 | `campaigns` |
| **Derivable** — reaches `campaigns` via an existing, enforced FK | 12 | `opportunity_cards`, `briefs`, `agent_runs`, `assets`, `gate_decisions`, `costs` (all via `campaign_id` or `agent_run_id → campaigns`); `option_cards`, `approval_decisions` (via `agent_run_id`/`card_id`); `task_transitions` (via `task_id → task_state`, once `task_state` has one); `object_taxonomy` (already carries `campaign_id`); `approval_actions` (via `approval_inbox_id`, once that table has one); `consent_linkage` (via `consent_register_id`, once that table has one) |
| **No anchor — needs a direct new column, no derivation exists** | 22 | `signals`, `consent_register` (frozen public — neither has a `campaign_id` at all); `standing_permissions`, `task_state` (public v2/orchestrator); `audit_log`, `retention_policy`, `access_log`, `utilisation_daily` (`vault_internal` — all keyed by polymorphic `(object_table, object_id)`, not a real FK); `approval_inbox`, `publish_attempts` (`governance` — `agent_run_id`/`gate_decision_id` are bare `uuid` columns, **not** `REFERENCES` — so even "derive it" isn't reliable here); all 11 `analytics.*` tables (keyed by `(source, day, natural_row_id)` or `(day, kpi_name, …)` — the only campaign linkage anywhere in this schema is the free-text `utm_campaign` column, reconciled through `utm_campaign_map.vault_campaign_id`, itself a bare `uuid` with no FK — and `utm_quarantine` exists precisely because that reconciliation already fails some rows today); `mcp_ops.tool_calls` |
| **Global / platform-level — arguably should stay tenant-free** | 4 | `schema_migrations`, `jti_ledger` (replay prevention should almost certainly stay a single global namespace — a tenant-scoped jti ledger buys nothing and risks a jti colliding across tenants going unnoticed), `retention_run` (a sweep-run record, not a per-object one), `kill_switches` (a real product decision either way — see §2.5) |

**23 of 39 tables (59%) need a literal new column** — `campaigns` itself plus
the 22 with no derivation path — not a join. This is the single most
load-bearing number in this memo: it is roughly 2.5× the "just touch the top
of the chain" intuition, and it is why Option 3's engineering estimate below
is what it is.

### 1.4 The `completions` cache deserves one sentence

`services/model-gateway/migrations/0001_completions_init.sql` keys
`completions` on `task_ref` alone, with no tenant dimension. `task_ref` is
derived via `uuid5` decomposition (per TD-07's note on the same mechanism).
Whatever tenancy option is chosen, confirm `task_ref`'s derivation is already
tenant-unique before assuming this table is a non-issue — a `uuid5` seed that
does not include a tenant identifier could let one tenant's cached completion
be replayed to another. This is a one-line check, not effort, and belongs in
whichever follow-up spec gets executed regardless of which option wins.

### 1.5 Scoring the three options

| | **1. Single-tenant per deployment** | **2. Schema-per-tenant** | **3. Row-level tenancy + RLS** |
|---|---|---|---|
| **Code change** | None. `main.bicep` is already a fully parameterised, self-contained deployable unit — every service, migration job, and secret is already environment-scoped. | Moderate: a tenant-resolution layer (schema/search_path selection per request) in each of the 6 services with a `db.py`; migration jobs re-run per tenant schema instead of once. | Extensive: 23 tables gain a real column (not just DDL — every INSERT/SELECT across every service needs a tenant filter or an RLS policy to rely on); the gate-token and task-envelope frozen contracts need a v2 window (§1.2); TD-08's byte-identical `parse_resource_claim`/`build_resource_claim` duplication (gatekeeper + publisher) needs synchronized updates if `tenant_id` is packed into the `resource` claim as a v1-compatible interim step. |
| **Tables touched** | 0 | 0 (same DDL re-applied under N schema names, unchanged) | 23 direct + 12 derivable-but-still-touched (an RLS policy has to reference *something* on every row) = effectively all 39 |
| **Frozen-contract impact** | None. | None — `contracts/vault-schema/schema.sql`'s bytes never change; it is applied verbatim per tenant. The sha256 baseline in `contracts/.frozen-v1.sha256` never moves. | Direct. Even a nullable, "additive" `tenant_id` column changes `vault-schema/schema.sql`'s bytes, forcing a baseline re-generation (survivable, same mechanism as the `pillar`/`so_what` precedent) — but propagating tenant context through the gate-token and task-envelope contracts is not survivable under v1 at all (§1.2), which is exactly what `07-operating-model.md` §D.3 already flags as "breaks the frozen v1 contract." |
| **Ops cost** | Highest, and compounding: N full copies of every resource in `main.bicep` — Postgres Flexible Server, 8 Container Apps, 2 Container Apps Jobs categories, Key Vault, Service Bus. TD-12 already flags the *single* existing Postgres as a `Standard_B1ms`/50-connection scaling wall with no HA; N of those is N times the fragility, not just N times the bill. | Low-moderate: one Postgres server, N schema sets. Directly compatible with TD-12's fix (General Purpose tier + HA) landing once, benefiting every tenant. | Lowest at scale — the reason it is listed as "cheapest to run" in §D.3 — but only once built; nothing here reduces build cost. |
| **Rough effort** | ~1–2 days per new customer (a `main.bicep` deploy to a fresh resource group); no cross-customer engineering cost. | ~3–4 weeks: tenant-resolution middleware in 6 services (`analytics-ingest`, `gatekeeper`, `model-gateway`, `orchestrator`, `publisher`, `vault` each have their own `db.py`), a `platform.tenants` registry, N-schema-aware migration jobs (8 existing `caj-*-migrate` jobs), console stays untouched (reads Vault only via REST, per `analytics-ingest`'s own "never a direct Postgres connection" convention — the same is true of console). No contract work. | ~8–10 weeks, grounded in comparable entries already in the TD register: TD-14's "v2 contract window" alone is costed at "~2 weeks + migration" for *one* claim-graduation; this needs the same window for two contracts (gate-token, task-envelope) plus 23 tables' worth of column/query/RLS-policy work plus a synchronized TD-08 fix across gatekeeper+publisher plus full-suite re-verification (400+ existing tests, most of which assume single-tenant data and would need a tenant fixture added). |
| **Fastest to first paying multi-tenant customer** | Yes — no engineering lead time, but each new customer is a new deploy, not a signup. | Second — one build, then genuinely fast per-tenant onboarding (a schema apply, not a deploy). | Slowest to first customer; cheapest per marginal customer after that investment. |

### 1.6 Recommendation

**Option 2 — schema-per-tenant — is the option this memo recommends**, for
reasons specific to this codebase rather than to multi-tenancy in the
abstract:

1. It is the only option that both changes something (unlike Option 1) and
   leaves `contracts/.frozen-v1.sha256`'s baseline completely untouched
   (unlike Option 3) — the DDL in `vault-schema/schema.sql` is reapplied
   byte-for-byte per tenant schema, so the frozen-contract guarantee survives
   in full, not just in the "additive nullable column" sense Option 3 relies
   on for half its own claim.
2. It converges with, rather than fights, TD-12. Option 1 multiplies the
   already-flagged Postgres fragility by the customer count; Option 2
   consolidates onto the one server TD-12 already recommends upgrading
   (General Purpose + HA), and that upgrade pays for every tenant at once.
3. Its engineering cost (~3–4 weeks) is in the same range as several already
   -accepted Priority-2 items in the TD register (TD-08 governance-lib
   extraction, ~1 week; TD-11 real analytics sources, ~1 week/source) rather
   than in TD-14/TD-01's multi-week-to-multi-month range that Option 3 would
   actually cost once the gate-token and task-envelope contracts are
   honestly priced in.

This recommendation carries real open questions that are commercial, not
technical, and could flip it:

- **Expected tenant count.** Option 2's per-tenant cost is a schema apply,
  not a deploy — cheap at 10s of tenants. At hundreds, N schema namespaces on
  one Postgres server becomes its own operational surface (migration jobs
  looping N times, connection-pool accounting per schema). If the real
  target is "hundreds of small customers," this changes the comparison.
- **Cross-tenant reporting.** Nothing in the current product reads across
  campaigns today, so nothing reads across tenants either — but if a future
  requirement needs it (e.g., a Canvas Intelligence house-wide rollup across
  managed customers), schema-per-tenant makes that a fan-out query across N
  schemas, where row-level tenancy makes it one `WHERE tenant_id IN (...)`.
- **Authorization, not just storage.** Vault's API auth (TD-03, resolved) is
  a single shared bearer token with no tenant dimension at all. Schema-per-
  tenant db isolation does nothing on its own to stop an authenticated
  caller from querying a different tenant's schema — the follow-up spec
  in Part 2 has to add tenant-scoped authorization as a first-class
  requirement, not assume storage isolation implies access isolation.

**No schema, migration, or infrastructure work should begin against any of
the three options until a business owner (the same authority TD-35 and
TD-36 already route commercial-policy decisions to) confirms the target
customer count and cross-tenant reporting requirement above, and signs off
on Option 2 specifically or overrides it.** Part 2 is written so that sign-
off is the only gate between this memo and execution — not a further design
phase.

---

## Part 2 — Follow-up spec: schema-per-tenant (Option 2)

**Not authorised to start. This is a spec for whoever executes the decision
in Part 1, once made — it is not itself that decision, and no code in this
PR implements any part of it.**

### 2.1 Schema diff

No change to any existing `CREATE TABLE` statement in any of the 7 migration
files listed in §1.1. The diff is entirely additive infrastructure around
them:

- **New tiny registry schema**, `platform` (mirrors the `governance` schema's
  own "why a separate schema" rationale — additive, never touches the frozen
  file):
  ```sql
  CREATE SCHEMA IF NOT EXISTS platform;

  CREATE TABLE IF NOT EXISTS platform.tenants (
      tenant_id       text PRIMARY KEY,      -- short slug, becomes the schema name prefix
      display_name    text NOT NULL,
      schema_prefix   text NOT NULL UNIQUE,  -- e.g. "t_acme" -> t_acme.campaigns, t_acme_analytics.*, etc.
      status          text NOT NULL DEFAULT 'active',
      created_at      timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT platform_tenants_status_allowed CHECK (status IN ('active', 'suspended', 'deprovisioned'))
  );
  ```
- Every one of the 7 migration files gets applied **once per tenant**, each
  time against a tenant-specific schema name (e.g. `t_acme` for the
  public-equivalent tables, `t_acme_vault_internal`, `t_acme_governance`,
  `t_acme_analytics`; `mcp_ops` is a defensible candidate to stay a single
  shared schema across tenants — tool-call logging is platform-operational,
  not tenant business data, matching the "global" bucket reasoning in §1.3).
  This requires each migration file's hardcoded schema name
  (`vault_internal`, `governance`, `analytics`, `mcp_ops`) to become a
  parameter, and `public`-schema tables (the frozen 9, plus orchestrator's 2
  and options-inbox's 3) to be created under the tenant's own schema instead
  of literal `public` — i.e., every migration job's SQL needs `search_path`
  set to the tenant schema before running, not a rewrite of the DDL itself.

### 2.2 Services that touch the new boundary

| Service | What changes |
|---|---|
| `services/vault` (`vault/db.py`) | Connection/session must resolve and set the request's tenant schema before any query. This is the one place most other services' tenancy resolution ultimately depends on, since `vault_lookup.py`/`vault_adapter.py` in publisher and orchestrator all go through Vault's REST API, not direct Postgres. |
| `services/orchestrator` (`orchestrator/db.py`, `orchestrator/servicebus/producer.py`) | `TaskEnvelope` needs a tenant identifier threaded end-to-end (see §2.4 on why this is a v1-compatible field, not a v2 one) so a dequeued task resolves to the right tenant's `task_state`/`task_transitions` schema. |
| `services/gatekeeper` (`app/db.py`, `app/tokens.py`) | Gate-token issuance needs to know which tenant's `governance.kill_switches`/`approval_inbox` to check; `parse_resource_claim`/`build_resource_claim` (TD-08's byte-identical duplicate, also in `services/publisher/app/verifier.py`) need the same tenant-carrying change applied to **both** copies in the same PR, per CLAUDE.md hard rule 10 — this is exactly the "shared-mechanism fix is a bug-class fix" pattern the register already warns about. |
| `services/publisher` (`app/db.py`, `app/verifier.py`, `app/vault_adapter.py`) | Same `verifier.py` duplication as above; `record_publish`'s Vault round-trip needs the tenant context to reach the right Vault schema. |
| `services/model-gateway` (`db.py`, `caching.py`) | `PostgresCache`'s advisory-lock key (`hashtextextended(task_ref, 0)`) should incorporate the tenant identifier per §1.4's finding, independent of whether `completions` itself gets a `tenant_id` column. |
| `services/analytics-ingest` (`analytics_ingest/db.py`) | Nightly ingest loops per tenant; `utm_campaign_map`/`utm_quarantine` reconciliation must not cross tenant schema boundaries even by accident, since today's reconciliation already has a documented failure path (quarantine) that a tenant mix-up could silently feed. |
| `console` | **No direct DB change** — it already reads Vault exclusively through the REST API (the same convention `analytics-ingest`'s migration header cites for its own schema). Console needs a tenant selector in its own auth/session layer (interacts with TD-04's `require_principal`), not a new DB dependency. |
| `mcp/mcp_ops` | Recommended to stay a single shared schema (see §2.1) — flag as an open item if tool-call logs must be tenant-partitioned for compliance reasons. |

### 2.3 The connection-pooling risk this spec must design against

`SET search_path` on a connection returned to a shared pool without
resetting it first is the same shape of bug TD-06 already found and fixed
once (a completed-but-still-borrowed advisory-locked connection returned to
the pool). Schema-per-tenant reintroduces that exact risk class at a much
larger blast radius: a connection that keeps tenant A's `search_path` after
being returned to the pool, then handed to a request for tenant B, is a
cross-tenant data leak, not a double-billing bug. The spec for whoever
executes this must pick one of:

- **Per-tenant connection pools** (bounded by TD-12's connection ceiling —
  cannot be adopted independently of TD-12's General Purpose tier + HA fix,
  since N tenant pools on a 50-max-connection B1ms is not viable), or
- A **request-scoped set-and-verified-reset** pattern with a regression test
  mirroring `test_postgres_cache.py::test_a_cache_hit_releases_its_advisory_lock`'s
  shape: assert directly that a connection's `search_path` is back to its
  pre-request value before it is released, not just that the request itself
  succeeded.

This decision point must be resolved before writing the migration, not
discovered during it.

### 2.4 What a v2 contract window covers, and what it doesn't need to

Per TD-14's existing plan, `contracts/gate-token/spec.md`'s "Forward plan"
already commits to a v2 window graduating `function_id`/`content_hash` to
first-class claims. Folding tenant context into the *same* window is the
efficient sequencing:

- **`gate-token/schema.json` v2**: add `tenant_id` as a first-class,
  required top-level claim (not packed into `resource` — the whole point of
  the v2 window is to stop stuffing structured data into a string). New
  `/v2/`-namespaced `$id`, fresh frozen baseline in
  `contracts/.frozen-v1.sha256`'s companion v2 guard (or a new
  `.frozen-v2.sha256`, mirroring the same mechanism).
- **`service-bus/task-envelope.schema.json` v2**: add `tenant_id` alongside
  `campaign_id` (already a top-level, required field on the envelope today
  — `tenant_id` is the natural sibling, not a nested addition).
- **What does *not* need a v2 window**: `vault-schema/schema.sql` itself.
  Schema-per-tenant's whole advantage (§1.5, §1.6) is that the DDL is
  reapplied verbatim per tenant schema — v1 stays exactly as published, no
  new namespace, no baseline change. If a future session finds itself
  editing `vault-schema/schema.sql` to implement this spec, that is a sign
  the spec drifted from Option 2 back toward Option 3, and should stop and
  re-check against Part 1.
- **Interim, pre-v2 option**: if tenant context is needed before the v2
  window lands, `tenant_id` can ride inside the existing `resource` claim
  string the same way `function_id`/`content_hash` do today — genuinely
  additive, no schema-file edit — but it inherits the same "byte-identical
  parity between two services" fragility TD-08 already flags, so treat it as
  a bridge, not a destination.

### 2.5 Explicitly out of scope for the first cut

- Self-service tenant provisioning (a `POST /tenants` flow). First tenants
  are provisioned by running the (parameterised) migration set by hand or by
  script, the same way the platform is deployed today.
- Cross-tenant reporting/rollups (flagged as an open question in §1.6 — build
  only if the business requirement in that bullet is confirmed).
- Per-tenant billing/metering beyond what `costs`/the KPI rollups already
  compute per campaign — extending that to a per-tenant roll-up is additive
  work on top of this spec, not a blocker to it.
- A decision on `governance.kill_switches`'s scope (global-only today; could
  become tenant-scoped). Ship the first cut with kill switches staying
  global across tenants (fail-safe: one incident stops everything) and
  revisit only if a customer-specific kill switch is actually requested.
