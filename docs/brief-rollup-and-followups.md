# Brief rollup and follow-ups

How the 12 new function-definition packages built in session/s10-intelligence
feed the daily-signal loop's brief chain, and the three follow-ups this
build explicitly did not resolve.

## How the cards flow into a brief

`services/orchestrator/loops/daily-signal-loop.yaml` fans the pre-existing
`ingest` node out into 11 new scanning tasks, all `depends_on: [ingest]` so
they run in parallel off the same raw ingest:

```
ingest
  +-- competitor-discovery-scan             (functions/10-competitor-discovery-scanner)
  +-- competitor-change-monitor             (functions/11-competitor-change-monitor)
  +-- competitive-positioning-analysis      (functions/12-competitive-positioning-analyst)
  +-- competitor-content-performance-scout  (functions/13-competitor-content-performance-scout)
  +-- fabric-ecosystem-scout                (functions/16-microsoft-fabric-ecosystem-scout)
  +-- vertical-scan-logistics-fleet         (functions/18-01-vertical-intel-logistics-fleet)
  +-- vertical-scan-mining-industrial       (functions/18-02-vertical-intel-mining-industrial)
  +-- vertical-scan-manufacturing           (functions/18-03-vertical-intel-manufacturing)
  +-- vertical-scan-construction-buildsmart (functions/18-04-vertical-intel-construction-buildsmart)
  +-- vertical-scan-fmcg-beverage           (functions/18-05-vertical-intel-fmcg-beverage)
  +-- vertical-scan-financial-services      (functions/18-06-vertical-intel-financial-services)
```

Each of those 11 packages emits a JSON object with a `cards` array (each card
carrying `headline`, `so_what`, `source_url`, `card_type`, `taxonomy`,
`evidence_grade`, `confidence`, and - for the six verticals - a `vertical`
const). All 11 feed `dedupe-signal-cards`, which depends on all of them and
removes duplicate cards (the same competitor move surfacing through both the
discovery scanner and a vertical scan, for example) before anything downstream
sees them.

`dedupe-signal-cards`'s deduplicated output feeds two places:

1. **`competitive-response-strategize`** (`functions/25-competitive-response-strategist`),
   which consumes the deduped cards and ranks them into a `response_plan`:
   every item gets a `severity` (critical/high/medium/low) and a
   `playbook_template` - the two named templates, `RIB BI+ move` and
   `BuildSmart-native-BI move`, cover the construction vertical's most common
   competitor-BI-product pattern; everything else gets a general
   pillar-led response.
2. **`morning-brief-rollup`**, which depends on both `dedupe-signal-cards`
   and `competitive-response-strategize`, so the morning brief carries both
   the deduped raw cards and the ranked response plan.

`executive-brief-rollup` depends on `morning-brief-rollup` and produces the
shorter executive-facing cut of the same material.

This build only shapes the declarative loop graph and the per-function
eval-fixture output shape (out_of_scope: real orchestrator dispatch has no
`worker.py` dispatch logic wired up yet, and is not part of this touch-scope).

## Follow-ups

Three follow-ups are named explicitly rather than silently left uncovered:

### (a) Vault-persistence gap

No code in this build inserts a card into the Vault's `opportunity_cards` or
`threat_cards` tables. The frozen Vault schema
(`contracts/vault-schema/schema.sql`) has no such tables or taxonomy columns
today, and adding them is a schema change this build's touch-scope
(`/functions`, `/services/orchestrator/loops`, `/docs`) forbids. Every one of
the 12 new packages' `cards`/`response_plan` shape is designed to be a
*future* Vault card (see AC-22/AC-23 in `.loop/spec.json`) without changing
the Vault contract itself now. Persisting these cards into
`opportunity_cards`/`threat_cards` is a follow-up requiring a contract
amendment to the Vault schema before it can ship - out of scope for this
build, not silently dropped.

### (b) Cross-brief citation consistency

Every one of the 12 packages' `tool_check.py` enforces per-function citation
rules (`every_card_has_https_source`, `distinct_source_domains`) - but no
per-function `tool_check.py` catches an uncited claim that *survives into an
assembled, multi-function brief*. Once `morning-brief-rollup` and
`executive-brief-rollup` combine cards from 11 upstream scanners plus the
response plan into prose, a claim could in principle lose its citation in
the rollup step even though every individual card that fed it was cited.
This is named explicitly as a follow-up for a future **measurement wave**
that adds cross-brief citation checking at the rollup level, once the rollup
step itself is implemented - it is out of scope for a per-function build.

### (c) registry.yml's hardcoded package paths

`.github/workflows/registry.yml`'s CI steps call
`services/registry/validate_package.py` and
`services/registry/lint_rubrics.py` against exactly the 3 pre-existing
package paths (02, 09, 42). Those steps do not automatically discover or gate
the 12 new packages this build adds, even though
`services/registry/eval_harness.py --all` and `common.py`'s
`discover_function_packages()` already auto-discover every package under
`functions/` with the right shape (confirmed in this build - see
`docs/function-register-coverage.md`). Fixing the CI gate needs an edit to
`.github/workflows/registry.yml`, which is outside this build's touch-scope
(`/functions`, `/services/orchestrator/loops`, `/docs` only). Named here as a
follow-up, not silently left uncovered: until `registry.yml` is updated,
`validate_package.py --all` and `lint_rubrics.py --all` (run manually) are
the only CI-equivalent gates that see the 12 new packages; the workflow
itself does not yet.
