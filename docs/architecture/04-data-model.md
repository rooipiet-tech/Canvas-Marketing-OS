# 04 — Data Model

*Every entity, from DDL. Five schemas in one Postgres 16 instance, owned by
four different services.*

---

## 1. Entity-relationship overview

```mermaid
erDiagram
  campaigns ||--o{ opportunity_cards : "campaign_id"
  campaigns ||--o{ briefs : "campaign_id"
  campaigns ||--o{ assets : "campaign_id"
  campaigns ||--o{ agent_runs : "campaign_id (NOT NULL)"
  signals ||--o{ opportunity_cards : "signal_id"
  opportunity_cards ||--o{ briefs : "opportunity_card_id"
  briefs ||--o{ assets : "brief_id"
  agent_runs ||--o{ assets : "agent_run_id (NOT NULL)"
  agent_runs ||--o{ gate_decisions : "agent_run_id (NOT NULL)"
  agent_runs ||--o{ costs : "agent_run_id (NOT NULL)"
  assets ||--o{ assets : "predecessor_asset_id"
  consent_register ||--o{ consent_linkage : "consent_register_id"

  campaigns {
    uuid id PK
    text name
    text status
    timestamptz starts_at
    timestamptz ends_at
  }
  signals {
    uuid id PK
    text source
    text signal_type
    jsonb payload
    timestamptz received_at
  }
  opportunity_cards {
    uuid id PK
    uuid signal_id FK
    uuid campaign_id FK
    text title
    numeric score
    text status
  }
  briefs {
    uuid id PK
    uuid opportunity_card_id FK
    uuid campaign_id FK
    text title
    text body
  }
  agent_runs {
    uuid id PK
    uuid campaign_id FK
    text agent_name
    enum status
    jsonb input
    jsonb output
    timestamptz completed_at
  }
  assets {
    uuid id PK
    uuid brief_id FK
    uuid campaign_id FK
    text asset_type
    int version
    enum approval_state
    uuid agent_run_id FK
    uuid predecessor_asset_id FK
    text storage_uri
    text content_hash
  }
  gate_decisions {
    uuid id PK
    uuid agent_run_id FK
    text decided_by
    enum outcome
    text reason
    timestamptz decided_at
  }
  costs {
    uuid id PK
    uuid agent_run_id FK
    text provider
    text unit
    numeric amount
    timestamptz incurred_at
  }
  consent_register {
    uuid id PK
    text data_subject_ref
    text lawful_basis
    text channel
    text purpose
    timestamptz consented_at
    timestamptz revoked_at
  }
```

## 2. The nine core Vault entities (`public`, frozen)

Source: `contracts/vault-schema/schema.sql`, hash-pinned in
`contracts/.frozen-v1.sha256`.

### 2.1 `campaigns` — top of the roll-up chain
The aggregate root. Everything financial rolls up here. In practice campaigns
are auto-created by handlers as `run-{envelope.campaign_id}` where
`campaign_id = uuid5(heartbeat.event_id, f"campaign:{loop_id}")` — so **one
campaign per loop per heartbeat**, not a marketing campaign in the business
sense. **[INFERRED]** The entity is named for a future state it does not yet
serve.

### 2.2 `signals` — raw inbound market signal
`payload jsonb` holds function 09's whole structured output. Immutable in
practice (patchable but nothing patches it).

### 2.3 `opportunity_cards` — scored opportunities
Has `score numeric` and `status`. **Nothing in the platform writes this
table today.** No handler creates an opportunity card; the `score-signals`
task_type is a pass-through. This is a fully-specified, unused entity.

### 2.4 `briefs` — creative/marketing briefs
Written by `draft_brief_handler`, twice per run (a full brief and an
"Executive Edition"). `opportunity_card_id` is always NULL in practice
because §2.3 is unused — the brief is derived from a signal via lineage
instead.

### 2.5 `agent_runs` — **the central entity of the whole data model**
Every model invocation is an `agent_run`. It is the join point for:
- cost attribution (`costs.agent_run_id`, NOT NULL)
- governance (`gate_decisions.agent_run_id`, NOT NULL)
- authorship/approval of artefacts (`assets.agent_run_id`, NOT NULL)
- budget enforcement (`budget.py` resolves `agent_run_id → agent_name`)
- the proof-circuit dry-run forcing (`agent_name == "loop-proof-circuit"`)

`status` is an enum: `pending | running | succeeded | failed | cancelled`.
Handlers create it `running`, then update to `succeeded`/`failed` with the
output payload.

**If you want to understand this platform's data model in one sentence:
`agent_runs` is the ledger of AI labour, and everything else hangs off it.**

### 2.6 `assets` — versioned, approvable artefacts
Four things make this table unusually well-designed:
- `version integer` + `predecessor_asset_id` self-FK with a
  `CHECK (predecessor_asset_id IS DISTINCT FROM id)` — an asset chain is
  reconstructable
- `approval_state` enum: `draft | pending_review | approved | rejected | superseded`
- `content_hash` — the sha256 that binds into gate tokens
- `storage_uri` — points at the content-addressed blob

**How to answer "which gate decision approved this asset version?"** There is
no direct FK. The schema documents the join convention inline:
```sql
SELECT gd.* FROM gate_decisions gd
WHERE gd.agent_run_id = <assets.agent_run_id>
ORDER BY gd.decided_at DESC LIMIT 1;
```
Because `gate_decisions` is append-only, the latest `decided_at` for that
`agent_run_id` *is* the authoritative outcome. This is a deliberate design
note in the DDL, not an accident.

### 2.7 `gate_decisions` — append-only governance ledger
**Has no `updated_at` column, deliberately, "to discourage in-place mutation
of a recorded decision."** A re-decision is a new row.

Written by three distinct deciders, namespaced by convention:
| `decided_by` | Written by |
|---|---|
| `gatekeeper:policy` | Autonomy policy evaluation |
| `gatekeeper:kill-switch` | Kill switch block |
| `<name> (<oid>)` | A human via Easy Auth |
| `system:model-gateway:redaction-firewall` | PII block |
| `system:model-gateway:budget-gate` | Budget hard breach |

**This table is the platform's single most important audit artefact.** It is
the one place where "an AI wanted to do something and here is what was
decided, by whom, and why" is recorded — for both human and machine
deciders, in the same shape.

### 2.8 `costs` — three rows per completion
`unit` is free text, which is what lets one schema carry three signals
without a migration: `usd`, `tokens`, `ms`
(`services/model-gateway/metering.py`). The unbroken FK chain
`costs → agent_runs → campaigns` is called out explicitly in the DDL as the
per-campaign roll-up path.

### 2.9 `consent_register` — POPIA-shaped
Three design decisions are called out in the DDL comments and matter legally:
- `lawful_basis text NOT NULL` — **a real value, not a boolean.** POPIA s11
  recognises consent as *one* of several lawful bases; a boolean would model
  only consent.
- `channel` + `purpose` columns — **not a single global opt-in flag.**
  Consent is per-channel, per-purpose.
- `revoked_at` distinct from `consented_at` — revocation is its own event,
  not a mutation of the grant.
- `UNIQUE (data_subject_ref, channel, purpose, consented_at)` — repeated
  grants over time are all preserved.

---

## 3. `vault_internal` — the governance sidecar (7 tables)

Exists because the public schema is frozen. Every table is keyed by
`(object_table, object_id)` — a **polymorphic association**, uniformly
applied to all 9 object types.

| Table | Purpose | Key constraint |
|---|---|---|
| `object_taxonomy` | The 6 mandatory taxonomy fields for every object | `UNIQUE(object_table, object_id)`; `campaign_id` carries a real FK to `public.campaigns` |
| `consent_linkage` | Binds a client-derived object to the exact consent row that authorised it | `UNIQUE(object_table, object_id)` |
| `audit_log` | Every taxonomy rejection, consent rejection, retention deletion | indexed on event_type, object, correlation_id |
| `retention_policy` | `retention_class → expires_at`, with `deleted_at` | partial index `WHERE deleted_at IS NULL` |
| `retention_run` | Sweep bookkeeping | — |
| `access_log` | One row per object GET, keyed by `X-Caller-Service` | 90-day self-purge |
| `utilisation_daily` | Daily rollup of access_log | `UNIQUE(day, object_table, caller_service)` |

**The taxonomy is the governance metamodel.** Six fields, mandatory, immutable
after create:

| Field | Domain | Enforced by |
|---|---|---|
| `vertical` | free text | presence only |
| `function_id` | free text | presence only |
| `campaign` | uuid, FK to `campaigns` | uuid parse + FK |
| `evidence_grade` | `A \| B \| C \| D \| unverified` | closed set |
| `consent_status` | `granted \| revoked \| not_required \| pending` | closed set |
| `retention_class` | `ephemeral_30d \| standard_1y \| extended_3y \| legal_hold` | closed set |

`evidence_grade` is the most interesting of the six: it means **every object
in the system carries a claim about how well-evidenced it is.** That maps
directly to `positioning.md`'s "proof over platitude" rule. The data model
encodes the brand policy.

---

## 4. `governance` — the control plane (6 tables)

| Table | Owner | Lifecycle |
|---|---|---|
| `kill_switches` | Gatekeeper + Publisher (read) | mutable `active` flag; CHECK ensures `scope='global' ⇒ function_id IS NULL` and vice versa |
| `approval_inbox` | Gatekeeper | `pending → approved\|rejected\|expired`; `link_consumed_at` is the single-use latch |
| `approval_actions` | Gatekeeper | append-only, 4 CHECK-constrained outcomes |
| `publish_attempts` | Publisher | append-only, `published\|rejected` + reason |
| `jti_ledger` | Publisher | `jti` **is** the primary key — the PK is the enforcement mechanism |
| `schema_migrations` | migration job | — |

**Zero dollar signs in this migration file, deliberately** — constrained
domains are CHECK constraints over `text`, never `CREATE TYPE`, because
`CREATE TYPE` needs a `DO $$ ... $$` block and Container Apps collapses `$$`
to `$` in secret values.

---

## 5. Orchestrator tables (`public`, additive)

```
task_state(task_id PK, loop_id, task_type, state, retry_count,
           depends_on jsonb, vault_write_failed_count, result_ref jsonb,
           created_at, updated_at)

task_transitions(id PK, task_id FK, from_state, to_state,
                 reason CHECK IN (10 values), occurred_at)
```

**Two defence-in-depth mechanisms in one place:** `reason` is a closed
`TransitionReason` enum in Python *and* a CHECK constraint in Postgres. The
history of that constraint is instructive — migrations 0003 and 0004 both had
to be rewritten to `ADD CONSTRAINT ... NOT VALID` because the migration script
re-applies all four files on *every* deploy, so a later migration's wider
vocabulary broke an earlier migration's re-validation.

The ten reasons:
`created`, `dependency_satisfied`, `dispatched`, `completed`,
`failed_attempt_1`, `failed_attempt_2`, `dead_lettered`,
`dependency_dead_lettered`, `vault_write_failed`, `qa_blocked`.

Each of those last three encodes a distinct business meaning:
- `dead_lettered` — "we tried three times and gave up"
- `dependency_dead_lettered` — "we never tried; it was already impossible"
- `qa_blocked` — "a normal business verdict said no" (not a failure)

**`result_ref` is the platform's inter-task memory.** It is deliberately a
*small structured pointer*, never content: `{vault_signal_id, brief_id,
content_hash, agent_run_id, campaign_id, decision_id, ...}`. A downstream
handler resolves what to do by reading its predecessor's `result_ref` off the
lineage — never by re-deriving or guessing.

---

## 6. `analytics` — the measurement schema (11 tables)

4 raw fact tables (`buffer_post_metrics`, `ga4_metrics`,
`search_console_metrics`, `linkedin_metrics`), each
`UNIQUE(source, day, natural_row_id)`; `utm_campaign_map` +
`utm_quarantine`; `scheduled_posts` (the reliability denominator); and 4
`kpi_rollup_*` tables.

**analytics-ingest never connects to the Vault's database directly** — it
reads Vault exclusively over REST (`C-VAULT-TABLES`). That is a real
architectural boundary, not a convention.

---

## 7. Ownership and lifecycle

| Entity | Created by | Mutated by | Deleted by | Terminal states |
|---|---|---|---|---|
| `campaigns` | dispatch handlers (`get_or_create_campaign`) | Vault PATCH | retention sweep | — |
| `signals` | `ingest_signals_handler` | — | retention sweep | — |
| `opportunity_cards` | **nothing** | — | retention sweep | — |
| `briefs` | `draft_brief_handler` | — | retention sweep | — |
| `agent_runs` | every handler | handler on completion | retention sweep | succeeded / failed / cancelled |
| `assets` | `draft_content_handler` | Vault PATCH (approval_state) | retention sweep (+ blob) | approved / rejected / superseded |
| `gate_decisions` | Gatekeeper ×3 paths, model-gateway ×2 paths | **never** | never | append-only |
| `costs` | model-gateway metering | **never** | never | append-only |
| `consent_register` | manual / API | PATCH `revoked_at` only | never | revoked |
| `task_state` | `insert_task_batch` | `transition`, `set_result_ref` | never | completed / dead_lettered / failed |
| `approval_inbox` | `/gate-check` level 1–2 | `consume_link` once | never | approved / rejected / expired |
| `jti_ledger` | Publisher | **never** (PK insert only) | never | consumed |

## 8. How information flows through the system

```mermaid
flowchart LR
  EXT["public web<br/>(4 allowlisted URLs)"] -->|mcp-web fetch_url| RAW["raw article bodies<br/>truncated 2000 chars"]
  RAW -->|prompt 09 + Haiku| SIG["signals.payload<br/>jsonb"]
  SIG -->|deterministic _render_brief| BR["briefs ×2"]
  BR -->|prompt 02 + Sonnet| VER["QA verdict<br/>pass/violations"]
  VER -->|pass only| APR["gate_decisions<br/>+ approval_inbox"]
  POS["positioning.md"] -->|prompt 42 + Sonnet| AST["assets<br/>+ content-addressed blob"]
  AST --> APR
  APR -->|human click| TOK["gate token<br/>RS256, hash-bound"]
  TOK -->|Publisher verify| PUBA["publish_attempts"]
  PUBA -->|live mode only| BUF["Buffer draft"]
  BUF -->|next night| MET["analytics.*_metrics"]
  MET --> KPI["4 KPI rollups"]
  KPI --> FAB["Fabric export blob"]
  ALL["every step"] -.->|3 costs rows| COST["costs"]
  ALL -.->|OTel span| AI["App Insights"]
  ALL -.->|result_ref| TS["task_state"]
```

**Three information-flow invariants, each enforced structurally:**

1. **Content is never on the wire between components.** Queues carry ids
   (`contracts/service-bus/spec.md`). `result_ref` carries ids
   (`0002_task_result_ref.sql` header). Spans carry ≤200-char enum-keyed
   values (`telemetry_lib/attributes.py`). Content lives in the Vault and is
   fetched by id.

2. **PII cannot reach a provider un-scanned.** The redaction firewall is step
   3 of the gateway's own pipeline, before any adapter call, and it scans the
   `tools[]` passthrough too — because *"client-identifying data smuggled
   into a tool definition would bypass a messages-only scanner entirely."*

3. **Every rejection produces a durable audit row on an isolated
   connection.** `write_audit_isolated()` exists precisely because the
   rejection path raises and rolls back its own transaction — a shared
   connection would roll back the audit row along with it, "silently losing
   the very audit trail the rejection is supposed to produce."

## 9. Data-model gaps worth naming

| Gap | Impact |
|---|---|
| **No `tenant_id` / `organisation_id` anywhere** | Single-tenant by construction. Multi-tenancy is a schema-wide change, not a feature |
| `opportunity_cards` never written | The signal→opportunity scoring step of the value chain is modelled but not implemented |
| No FK `assets ↔ gate_decisions` | Resolvable only by the documented join convention; a direct FK would be safer |
| No `users` / `roles` / `permissions` tables | Identity is entirely delegated to Entra; there is no in-app authorisation model |
| `publish_attempts.agent_run_id` has no FK | It is in a different schema from `agent_runs`; referential integrity is by convention |
| Publisher's Vault "publish record" is a stub | The publication event is recorded in `publish_attempts` but never in the Vault |
| No campaign→brief→asset lineage query API | Reconstructable by hand; not exposed |
