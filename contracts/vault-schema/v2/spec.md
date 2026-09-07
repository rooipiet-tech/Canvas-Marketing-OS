# Vault schema contract — v2

**Status: contract defined, not yet migrated to or read/written by any
service.** This is stage 1 (schema/contract definitions) of TD-14's v2
contract window (`docs/architecture/09-technical-debt.md` TD-14). Stage 2
(consuming-service changes to Gatekeeper/Publisher, for the gate-token half
of this window) and stage 3 (the live `vault_internal` migration and
`services/vault` code changes that adopt this schema) are separate,
later PRs. `contracts/vault-schema/schema.sql` (v1) is byte-for-byte
unchanged and remains frozen exactly as published, guarded by
`contracts/.frozen-v1.sha256`.

## Why this exists

`docs/accepted-risks.md`'s "Vault taxonomy/consent/retention/rollup
bookkeeping lives in a separate `vault_internal` schema" entry names two
options for its "Production hardening path": fold `vault_internal`'s tables
into a version-bumped `vault-schema/schema.sql`, or freeze `vault_internal`
itself as a second frozen baseline. This file takes the first option,
because only it satisfies TD-14's stated goal of "removing the LEFT JOIN on
every read" — freezing `vault_internal` in place would make it official but
would not remove the join.

## What moved, and how

| v1 / `vault_internal` | v2 |
|---|---|
| `vault_internal.object_taxonomy` (7 tables' worth of taxonomy fields, keyed polymorphically by `(object_table, object_id)`, `LEFT JOIN`ed on every read in `services/vault/vault/routers/objects.py`) | **Retired.** `vertical`, `function_id`, `evidence_grade`, `consent_status`, `retention_class` are now real, nullable columns on all 9 object tables directly (`campaigns`, `signals`, `opportunity_cards`, `briefs`, `agent_runs`, `assets`, `gate_decisions`, `costs`, `consent_register`). `campaign_id` (the 6th `TaxonomyFields` member) already had a real FK column on 4 of those 9 in v1 (`opportunity_cards`, `briefs`, `assets`, `agent_runs`); v2 adds it as a real nullable FK to the other 5 too (`campaigns` needs none — it IS the campaign; `gate_decisions`/`costs` already reach `campaigns` via `agent_run_id`, but gain the direct column anyway for taxonomy-read symmetry with the other 7; `signals`/`consent_register` had no campaign anchor at all in v1 and gain a genuinely new one). |
| `vault_internal.consent_linkage` | Promoted to a first-class table, same shape, same polymorphic `(object_table, object_id)` key — this is not joined on every object read, so no restructuring, just no longer namespaced under a "sidecar" schema. |
| `vault_internal.audit_log` | Same — first-class, same shape. |
| `vault_internal.retention_policy` | Same — first-class, same shape. Note this table's own `retention_class` column (a sweep-scheduling value) is distinct from the new per-object `retention_class` column above (a taxonomy value); the two are meant to agree and stage 3's migration populates both from the same source, but they serve different reads (`retention.py`'s sweep vs. `objects.py`'s taxonomy read) and are not merged. |
| `vault_internal.retention_run` | Same — first-class, same shape. |
| `vault_internal.access_log` | Same — first-class, same shape. |
| `vault_internal.utilisation_daily` | Same — first-class, same shape. |

Every table this file defines matches `contracts/vault-api.yaml`'s existing
`TaxonomyFields` component exactly (`vertical`, `function_id`, `campaign`,
`evidence_grade`, `consent_status`, `retention_class`, already `required` on
every one of the 9 object `*Create` schemas). `docs/accepted-risks.md`'s
compensating control #2 ("the contract never leaks the split — no
`vault_internal` or `sidecar` reference anywhere in `vault-api.yaml`") holds
exactly as before: the API contract already presented taxonomy as
first-class object fields; this file brings the database schema in line
with what the API already promises, rather than changing the API at all.

## Why the 5 new taxonomy columns are nullable

Matches the precedent already set by `vault-schema/schema.sql` (v1) itself
for `opportunity_cards.pillar`/`so_what`/`source_url`/`confidence`: rows
written before a migration adopting this shape has no value to put there
without guessing. Unlike that precedent, though, a real backfill source
exists here — every existing row already has a matching
`vault_internal.object_taxonomy` row (or `vault_internal.consent_linkage`
row, for consent linkage) keyed by `(object_table, object_id)` — so stage
3's migration can genuinely backfill every column from real data, not
leave it permanently NULL. Whether to `ALTER COLUMN ... SET NOT NULL` after
a verified 100%-backfill is a stage-3 decision, not this file's.

## Enum types

Three new enum types (`taxonomy_evidence_grade`, `taxonomy_consent_status`,
`taxonomy_retention_class`) mirror `vault-api.yaml`'s `TaxonomyFields` enum
values exactly, following v1's own convention of a Postgres `ENUM` type per
constrained string field (`asset_approval_state`, `gate_decision_outcome`,
`agent_run_status` are carried forward from v1 unchanged in this file).

## Explicitly out of scope for this file

- **The live migration.** No `ALTER TABLE`, no backfill `UPDATE`, no
  `services/vault` code change. This file is a target-shape contract
  definition only, the same relationship v1's `schema.sql` has to the
  `caj-vault-migrate` job that actually applies it.
- **Gatekeeper/Publisher.** Nothing here touches gate-token issuance or
  verification — see `contracts/gate-token/v2/spec.md` for that half of
  this same window.
- **`tenant_id` / TD-05.** Per
  `docs/architecture/20-multi-tenancy-decision-memo.md` §2.4/§1.6, this
  file needs no tenant-related change at all: schema-per-tenant (the
  memo's recommended option) reapplies whichever vault-schema file is
  canonical, byte-for-byte, per tenant schema — v1 today, this v2 file
  once stage 3 lands. If a future change edits this file to add a
  `tenant_id` column, that is a sign it drifted from TD-05's Option 2 back
  toward Option 3 (row-level tenancy) — stop and re-check against the memo
  first. `campaign_id` newly appearing on `signals`/`consent_register`
  above is a byproduct of taxonomy consolidation, not a tenancy change —
  but it does incidentally move those two tables from TD-05 memo §1.3's
  "no anchor" bucket into the "derivable via campaigns" bucket, which
  whoever executes TD-05 Part 2 should reconcile that memo's table against.
