# Audit v2 — the recount, in full

*Companion arithmetic to `canvas-marketing-os-audit-v2.html` and
`road-to-N-v2.html`. This file is the working paper; the two HTML documents
are the readable artefacts. Every number here traces to a cited source —
where none exists, the line says **unverified**.*

**Baseline commit:** `main` @ `df21490` (2026-09-04 10:16:33 +0200) — the tip
of `main` when this audit started. PR #148 (`docs: blueprint v2 + register`,
Job 1 of this request) is open off this commit and is **not** included in the
baseline: it is scaffolding not yet merged, and grading it against itself
would be circular. Where PR #148's content is relevant it is named explicitly
as "landed in PR #148, unmerged."

**Design of record:** `docs/blueprint/agentic-marketing-engine-v2.md` (as
landed by PR #148), not the July `.docx`. Every row below is re-derived
against v2.

---

## 1. The denominator: 178 → 219

The August/4-September audit counted 178 designed items across nine classes
drawn from the v1 `.docx`. v2 changes two of those nine classes and adds six
new ones native to v2 itself. Reconciliation, line by line:

| # | Class | v1 (4 Sep) | v2 | Δ | Why |
|---|---|---:|---:|---:|---|
| 1 | Function register | 112 | **127** | +15 | v2 §10 adds functions 113–127 (assigned in PR #148). Fn 40 (SME Interviewer) is marked **superseded** by Fn 113, not removed — it stays a counted row (v2 Appendix A: "Row retained for numbering stability"). |
| 2 | Daily core agents | 15 | 15 | 0 | v2 does not change the 15-agent daily core; Fn 116/117 change how gates route, not the daily-core count. |
| 3 | Operating loops | 7 | **10** | +3 | v2 ships three new loop DAGs: `options-approval-loop`, `expertise-harvest-loop`, `foundation-bootstrap-loop` (landed in PR #148 as top-level `loops/*.yaml`, per `scripts/validate.py`'s hardcoded path — see PR #148's own body for why). |
| 4 | Autonomy levels (0–4) | 5 | 5 | 0 | Same five levels; Level 1 is renamed "Options" in v2, not resized. |
| 5 | KPI hierarchy (families) | 8 | 8 | 0 | v2 does not touch the KPI family tree. |
| 6 | Agent-score components | 7 | 7 | 0 | v2 changes *how* the Acceptance component is measured (edit-distance → Recommendation Hit Rate / Rejection-All Rate, Appendix A), not the count of components. The two new metric names are counted separately at row 15 below rather than folded in here — see judgement call, §1a. |
| 7 | Four-lens scorecard | 4 | 4 | 0 | Unchanged. |
| 8 | Technology-stack integrations | 13 | 13 | 0 | Unchanged; v2 adds no new stack row (Canva refresh-token remains unresolved — see §3). |
| 9 | Non-negotiable approval gates | 7 | 7 | 0 | v2 states these are "unchanged in substance; now typed card kinds" — same seven, reshaped delivery mechanism. |
| — | **Subtotal, v1-derived classes** | **178** | **196** | **+18** | |
| 10 | Contracts (Appendix C1–C4) | — | **4** | +4 | OptionCard, ApprovalDecision, EvidenceResolution (C3, a rule rather than a schema file, counted as a contract per the doc's own "C1–C4" framing), StandingPermission. |
| 11 | Earn-in rules (§G2 table) | — | **8** | +8 | The §G2 table's own row labels: action classes; starting level; promote 1→2; promote 2→3; promote 3→4; demote; timeout defaults; regain. |
| 12 | Standing-permission seeds | — | **4** | +4 | SP-001 (Fireflies transcripts), SP-002 (LinkedIn scheduling), SP-003 (personal-profile consent), SP-004 (legal GREEN auto-pass) — v2 Appendix C4. |
| 13 | `options_inbox` components | — | **4** | +4 | Per the extension's own README table: card builder/validator (`cards.py`), routing policy (`policy.py`), Teams digest renderer (`teams_render.py`), decision store + DDL (`store.py`). |
| 14 | Approval budget | — | **1** | +1 | The single governance parameter: 6 cards/working day, one 07:30 digest. |
| 15 | Ratification metrics | — | **2** | +2 | Recommendation Hit Rate, Rejection-All Rate (Appendix A: "Agent score — Acceptance"). |
| — | **Subtotal, v2-native classes** | **0** | **23** | **+23** | |
| | **Total** | **178** | **219** | **+41** | |

**N = 219.**

### 1a. Judgement calls made, stated plainly

- **Row 6 vs. row 15 overlap risk.** The "Acceptance" agent-score component
  and the two ratification metrics describe the same underlying change (edit
  distance → Hit Rate/Rejection-All). Counted them as two separate classes
  rather than one, because Appendix A lists them as two separate change-log
  rows ("Agent score — Acceptance" and "Governance — trust-weighting" are
  distinct rows) and the v1 scorecard's "Agent-score components" class was
  always a fixed set of seven *categories* (Reach, Accuracy, etc.), not a
  metric-name count — resizing it would break comparability with the 4
  September table for no gain. Flagged here so a reader who disagrees can
  subtract 2 from N.
- **Row 10, C3.** "Evidence resolution" is a rule embedded in the OptionCard
  contract's own text, not a separate JSON Schema file (`contracts/` ships
  three `.schema.json` files, not four — confirmed: `ls contracts/*.schema.json
  | grep -v -e gate-token -e task-envelope` → `approval-decision.schema.json,
  option-card.schema.json, standing-permission.schema.json`, three files).
  Counted it anyway as a fourth *contract item* because the blueprint's own
  Appendix C numbers it C1–C4 and gives it independent prose — the design
  names four things, the filesystem holds three files. A reader who prefers
  "count files, not doc sections" should read row 10 as 3, N as 218.
  **This is exactly the kind of contract-vs-code mismatch `scripts/
  check_schema_code_ordering.py` exists to catch elsewhere in this repo; it
  is not a defect here, since C3 was never meant to be a schema file — it is
  a rule the other three schemas jointly enforce.**
- **Row 13.** Counted `options_inbox` by its four source modules, matching
  the extension README's own four-row table, rather than as "1 service" or
  as "14 tests." A reader who prefers counting the service as one item
  reduces N by 3 to 216; counting the 14 tests as 14 items would inflate N
  in a way nothing else in this table does (no other class counts unit
  tests), so that alternative was rejected, not merely unstated.
- **Fn 40 (superseded).** Kept as a counted, occupied row rather than
  removed, because v2's own text is explicit that removing it would break
  numbering stability. It carries a new register status of `superseded`
  (added to `docs/function-register.md`'s status vocabulary — see §2).

---

## 2. Re-classifying the deviation register against v2

The 4 September audit's deviation register carries **15** items, DRIFT-1
through DRIFT-15 (the audit's own §1 explicitly separates this from the
7-item "scorecard drift" column — two different denominators, by the
original document's own statement). The instructions for this pass named
"11 drifted items" without specifying which 11; rather than guess a subset,
every one of the 15 is re-examined against v2 below. Three were already
closed by the same-day live-verification pass (§9 of
`claude_design-vs-built-audit-2026-08.md`, folded in here as of this
baseline) before v2 re-classification even starts.

| # | 4 Sep finding | Status before v2 | v2 re-classification | Why |
|---|---|---|---|---|
| DRIFT-1 | The build was never given the blueprint | Open (root cause) | **Unchanged.** | v2 does not retroactively fix v1's history; it fixes the *going-forward* case: PR #148 lands the blueprint in version control at `docs/blueprint/`, and the sha256 pin means this cannot recur silently. Root-cause status stands for everything built before PR #148. |
| DRIFT-2 | Autonomy levels mean the opposite of the design at the bottom of the ladder | Open | **Re-scoped, not closed.** | v2 explicitly adopts the built mechanism as authoritative: "Levels are authoritative in `autonomy.yaml` ... That file changes in exactly two ways" (§G2). The *mechanism* DRIFT-2 flagged is now the design. What remains drifted is narrower: v2's own Level 1 is renamed "Options" and Level 2 is "Execute approved" — neither name appears anywhere in `services/gatekeeper/policy/autonomy.yaml`'s comments as of this baseline (`grep -n "Options\|Execute approved" services/gatekeeper/policy/autonomy.yaml` → no match). So DRIFT-2 downgrades from *"the mechanism is backwards"* (closed, per v2's own ruling) to *"the vocabulary/comments in `autonomy.yaml` don't yet use v2's level names"* — a documentation gap, not a semantic inversion. |
| DRIFT-3 | Three package ids don't match the blueprint register (02, 42, 48) | Open | **Unchanged in substance; now formally registered as `drift` status, not merely narrated.** | `docs/function-register.md` (landed PR #147, predates this audit) already carries this as a first-class `drift` status with a dedicated "Numbering drift" section — so v2 doesn't close it, but the register's existence means it is no longer only inferrable from prose. v2 adds nothing that resolves the actual collision risk (fn 2, 42, 48 in the blueprint are still unbuilt at their real numbers). |
| — | *(`17-source-scout`, folded into function-register.md as a fourth numbering item, not originally a DRIFT-N row)* | Open | **Unchanged.** | `docs/function-register.md` already states the fix: "Build-discovered functions should take ids above 112." v2's own new functions (113–127) *follow* exactly this rule — so v2 is consistent with, and implicitly validates, the fix `17-source-scout` needs but has not received. Still unresolved for `17-source-scout` itself. |
| DRIFT-4 | Weekly trigger stuck daily since 6 Aug | Open | **Unchanged, and independently reconfirmed live.** | §9.2 of the live-verification appendix: fired "at 05:00 UTC (07:00 SAST) on every single day through 4 September." v2 does not touch scheduling; this is purely an infra/ops item outside the blueprint's scope. |
| DRIFT-5 | Runbook instructs a click no template renders | Open | **Unchanged.** | v2 does not touch `console/` or `run-the-loop.md`. Outside v2's scope entirely. |
| DRIFT-6 | Console hardcoded to `mock` | Open | **Closed** (already, before v2) | §9.1 live check: `VAULT_API_MODE = real`, `GATEKEEPER_API_MODE = real` on the live revision. Not a v2 effect — recorded here only so the register in `docs/function-register.md`-adjacent documents doesn't quote it as still open. |
| DRIFT-7 | `TEAMS_WEBHOOK_URL` wired to Gatekeeper only | Open | **Closed** (already, before v2) | §9.1: the secret is a `secretRef` on both `ca-gatekeeper` and `ca-orchestrator` live, and both apps run (a Container App with an unresolvable `secretRef` does not start). Not a v2 effect. |
| DRIFT-8 | `DAILY_LOOP_BUDGET_USD` documented as enforced, read only by a test | Open | **Unchanged.** | v2 does not touch cost-cap enforcement. |
| DRIFT-9 | fn 48 gates publishing with 0 evals | Open (per 4 Sep audit text) | **Closed independently of v2.** | `docs/function-register.md`'s own "What has changed since the August audit" table: "**Closed.** 8 eval tasks." — this closure predates v2 and PR #148; it is a fact about `main` @ `9ada17a`, carried here so the register doesn't look stale next to it. |
| DRIFT-10 | Architecture doc set stale | Open | **Unchanged; now larger in scope.** | v2 adds a whole new document (`docs/blueprint/agentic-marketing-engine-v2.md`) and two audit-template HTML files that the `docs/architecture/` set does not reference at all as of this baseline (`grep -rl "blueprint-v2\|agentic-marketing-engine-v2" docs/architecture/` → no match). The staleness gap widens with this very PR unless `docs/architecture/00-executive-summary.md` and `10-product-roadmap.md` are updated to point at v2 — not done in Job 1/2, flagged as a Job-3 completion-plan item (§C, road-to-N doc). |
| DRIFT-11 | `MCP_WEB_LIVE_MODE` set by hand, declared nowhere | Open (highest severity) | **Closed** (already, before v2) | §9.1: set to `"True"` on the live revision *and* now declared in `main.bicep`; `MCP_WEB_ALLOWLIST` grew to seven domains. Not a v2 effect. |
| DRIFT-12 | Uncommitted `F-*.diff` files, `_to_delete/` residue | Open | **Unverified — not re-checked this pass.** | Working-tree residue is inherently point-in-time and specific to whatever session's filesystem the 4 September audit ran in; this session's own `git status` (see PR #148's own diff) shows a clean tree with only the two expected deliverable files added. Cannot speak to whether the *original* residue is gone without that session's own working tree, which this audit does not have access to. |
| DRIFT-13 | `session/s13-website` blocked on expired OAuth for a month | Open | **Unchanged, and reconfirmed live, this session.** | This session's own `/mcp` output: *"Failed to reconnect to canvas-wordpress (detail withheld on this connection)."* Same failure class as 3 August's diagnosis, one month plus later. |
| DRIFT-14 | Claude Agent SDK never used | Open (undecided, not a defect) | **Unchanged.** | v2 does not touch runtime architecture. Still an unrecorded decision, not a v2 concern. |
| DRIFT-15 | n8n / Power Automate never used | Open (undecided, not a defect) | **Unchanged.** | Same as DRIFT-14 — outside v2's scope. |

**Net effect of v2 on the deviation register:** zero items close *because of*
v2. Three items (DRIFT-6, -7, -11) were already closed before v2 by live
verification unrelated to the blueprint amendment. One item (DRIFT-2) is
narrowed from a mechanism-level finding to a vocabulary-level one, because v2
explicitly ratifies the built mechanism. This matches the instruction's own
framing precisely: v2 "explicitly adopts the built autonomy mechanism
(`autonomy.yaml` as authoritative, §G2)," and re-examining DRIFT-2 on that
basis is exactly what "re-classify" means here — the finding survives, only
narrower.

---

## 3. Live numbers, refreshed — 4 September 2026, ~10:37–10:45 UTC

*Method: read-only `az` against resource group `cmos-dev` (same account,
`pvz@canvasintelligence.com`, as the same-day §9 pass) and `caj-vault-query`
(read-only SQL over `vault.agent_runs`/`campaigns`), run fresh for this audit
rather than copied from §9. Two `caj-vault-query` executions:
`caj-vault-query-uevxkjd` and `caj-vault-query-uof6v1l`, both `Succeeded`,
both verified via `az rest GET .../executions/<name>` to confirm the query
override actually applied (L-0023) before trusting the result.*

| Check | §9 (09:49–09:57 UTC) | This pass (10:37–10:45 UTC) | Verdict |
|---|---|---|---|
| Brand Steward (fn 02) pass rate | 345 passed / 231 failed = **59.9%** | 323 succeeded / 181 failed / 12 running (30d) = **323/504 = 64.1%** (excluding in-flight) | **Diverges — see note below** |
| Fact-check (fn 48) pass rate | 181 passed / 254 failed = **41.6%** | 181 succeeded / 254 failed = **181/435 = 41.6%** | ✅ Confirmed, exact match |
| Dual-gate approximation | ~25% | ~64.1% × 41.6% ≈ **26.7%** | Moves with the fn 02 divergence above |
| Violation-code histogram (30d) | fabricated-proof-point 226, misstated-approved-fact 95, **unsupported-claim 81**, missing-cta 60, uncleared-client-reference 49, url-utm 48, revenue-model-misstatement 37, sa-english-spelling 28, unverifiable-client-descriptor 1 | fabricated-proof-point 226, misstated-approved-fact 95, missing-cta 60, uncleared-client-reference 49, url-utm 48, revenue-model-misstatement 37, **unsupported-claim 31**, sa-english-spelling 28, unverifiable-client-descriptor 1 | 8 of 9 codes match exactly; **unsupported-claim diverges (81 → 31)** |
| Total `agent_runs`, 30-day window | 5,494 (stated in §9.5) | 5,572 | ✅ Consistent (+78 in ~50 min of live traffic) |
| `smoke-test-v1` pollution | 3,494 of 5,494 rows (63.6%), all `pending` | **Could not reproduce.** Queried `agent_runs.input->>'function_id' = 'smoke-test-v1'` → 0 rows. Queried top-10 `campaigns.name` by row count → none named `smoke-test-v1`; the top rows are `run-<uuid>` (max 116 rows) and `gateway-smoke-test` (45 rows). | **Unverified this pass** — see note |
| `scan_profile_not_configured` events (3d) | ~221 total ("23 × 9 scanners + 7 × 2 scanners") | Sep 2: 194 · Sep 3: 27 · Sep 4 (partial day): 27 — 248 total | ✅ Consistent order of magnitude; freshly confirmed still active |
| `brief_published` frequency (7d) | Fired on 2 of the last 7 days | Fired on Sep 2 and Sep 3 only in the trailing 7-day window queried; **zero** on Sep 4 so far | ✅ Confirmed, same two days |
| `la-source-discovery-trigger` activity (7d) | Zero `propose-sources`/`probe-sources` events | Zero matches for `propose-sources`, `probe-sources`, `propose_sources`, `probe_sources` in `ca-orchestrator` logs, 7d | ✅ Confirmed. Trigger itself is `Enabled` (`az resource show`) |
| `PUBLISHER_DRY_RUN` | Absent from `ca-publisher`'s live env | Absent — `az containerapp show -n ca-publisher --query ".env[?name=='PUBLISHER_DRY_RUN']"` → `[]` | ✅ Confirmed |
| `canva-refresh-token` | Not present | Not present — `az containerapp secret list -n mcp-canva` → `canva-client-id`, `canva-client-secret` only | ✅ Confirmed |

### Two unreconciled discrepancies — reported, not smoothed over

**Brand Steward pass rate (59.9% → 64.1%) and `unsupported-claim` (81 → 31).**
Both queries ran against the identical filter (`agent_name` / violation-array
membership, 30-day window) as the methodology §9.5 describes, roughly 50
minutes apart, on a table that should only grow. A table that only grows
cannot *lose* 60 brand-steward outcome rows and 50 `unsupported-claim`
violation entries between two reads unless rows were deleted, reclassified,
or the two sessions filtered on subtly different criteria neither wrote down
precisely enough to diff. `cmos-dev` does carry a `caj-vault-retention-expiry`
Container Apps Job (confirmed via `az containerapp job list`), which is a
plausible mechanism — a retention sweep between 09:57 and 10:37 would produce
exactly this shape (some categories flat, two down) — but its recent
execution history was not checked this pass, so this is a **hypothesis, not
a finding**. Recorded as unreconciled rather than picking whichever number is
more convenient.

**`smoke-test-v1`.** §9.5's own methodology note already flags this area as
fragile: two of that pass's four attempts silently used the job's stale
default query rather than the override, because of the same L-0023 failure
mode this pass explicitly checked for. It is equally possible that (a) this
pass's guess at the tagging field (`input->>'function_id'`) is simply wrong —
the actual `.py` source for whatever wrote `smoke-test-v1` into the vault was
not located in the time available (a repo-wide grep for `smoke-test-v1`
returns only `services/vault/tests/test_contract_smoke.py`, which does not
create production rows), or (b) the pollution was genuinely cleaned up
between the two reads. Recorded as **unverified** for this pass rather than
inferring either explanation. `kpi_rollup_vault_utilisation` (named in §9.5
as the first consumer this would break) was not re-checked.

---

## 4. Human-input register scorecard — v2 Appendix B, all 30 rows

For each row, whether the human act it names still exists in the repo today
(baseline `df21490`), and which function/PR removes it. "Removed" means the
replacement function exists as a *scaffolded* package (PR #148) — none is
wired into a loop yet (Appendix D, PRs 1–13), so "removed" here means
*designed and scaffolded*, not *operating*. This distinction is the entire
point of this table and is repeated in every row rather than assumed once.

| Row | Class | Human act (v1) | Still exists today? | v2 replacement | Wiring status |
|---|---|---|---|---|---|
| H1 | REPLACE | SME interviews | **Yes** — `docs/function-register.md` fn 40 (SME Interviewer) has no build; no code path replaces manual interview capture. | Fn 113 | Scaffolded (PR #148), not wired |
| H2 | REPLACE | Pieter supplies opinions (Fn 43 ghostwriter) | **Yes, and more directly than most rows here.** `functions/43-executive-ghostwriter` is `live` (`docs/function-register.md`), and its own `prompt.md` requires a `sourced_opinion_or_quote` for every stance ("Never fabricate an opinion... write the piece without a personal opinion, or flag that a source is missing") — so the *drafting* is automated, but the human act H2 names (Pieter supplying the opinion in the first place) is the thing feeding that field, and nothing built replaces it. | Fn 114 + 115 | Scaffolded, not wired — so fn 43's live input path is unchanged from v1 |
| H3 | IRREDUCIBLE | Person on camera | **Yes**, by design — irreducible. | Fn 123 kit; decision D1 | Scaffolded, **dormant until D1 is ratified** (per the function's own register status) |
| H4 | IRREDUCIBLE | Live speakers | **Yes**, by design — irreducible. | Fn 123 / recorded | Same as H3 |
| H5 | IRREDUCIBLE | Employee consent, personal profile | **Yes** — no `functions/68-*` exists. | SP-003 standing consent | Seed defined in `policies/autonomy-matrix.yaml`/`earn-in-rules.yaml` (PR #148); not enacted (enactment requires a card, which requires PR 10) |
| H6 | REPLACE | Win/loss interviews | **Yes** — `functions/24-*` does not exist. | Fn 120 | Scaffolded, not wired |
| H7 | IRREDUCIBLE | Client consent | **Yes**, by design. | Fn 119 (drafts and tracks; register stays read-only) | Scaffolded, not wired |
| H8 | REPLACE | Sales types acceptance reasons | **Yes** — no automated inference path exists in `services/orchestrator` today (confirmed no `sales_outcome` handler in `dispatch.py`'s registered task types). | Fn 120 | Scaffolded, not wired |
| H9 | REPLACE | Expert reviews replies | **Yes** — no reply-drafting function is built. | Fn 116 | Scaffolded, not wired |
| H10 | REPLACE | Designers, visual review | **Yes** — `functions/55-*`/`56-*`/`62-*` do not exist. | Fn 121 | Scaffolded, not wired |
| H11 | REPLACE | Humans author foundations (Phase 0) | **Yes** — no `foundation.*` card type exists in `contracts/option-card.schema.json` before this PR (it does now, PR #148, but nothing emits one yet). | Fn 122 | Scaffolded, not wired |
| H12 | REPLACE | Authored first-30-days deliverables | **Yes**, same evidence as H11. | Fn 122, 126 | Scaffolded, not wired |
| H13 | REPLACE | Marketing operator runs the calendar | **Yes** — `docs/run-the-loop.md` (per the 4 Sep audit's DRIFT-5) still assumes a human operator clicking through the console. | Fn 117 + Fn 1 | Scaffolded, not wired |
| H14 | REPLACE | Edits / sampled review as the learning signal | **Yes** — `agent_runs`/`gate_decisions` carry no `recommendation_hit_rate` or `rejection_code` column as of `contracts/vault-schema/schema.sql` at this baseline (`grep -n "hit_rate\|rejection_code" contracts/vault-schema/schema.sql` → no match); Appendix D PR 1 is the migration that would add this. | Fn 126, 127 | **Not even scaffolded at the data layer yet** — PR 1 is unstarted |
| H15 | CONVERT | Legal review (Fn 5, 19) | **Yes** — no `functions/05-*` or `19-*` exists; no triage tiering is built. | Fn 124 | Scaffolded, not wired |
| H16 | REPLACE | Brand rule authorship, named owner | **Partially** — `functions/02-brand-steward-qa/prompt.md` carries hand-authored rules today (confirmed present, `qa_review_blocked` events fire against them live per §3 above); v2's replacement (rule diffs as cards via Fn 116) is scaffolded but not operating, so the human-authored rule set is still the one enforcing violations in production right now. | Fn 3 via Fn 116 | Scaffolded, not wired — **and the human-authored version is the one actually gating publishing today** |
| H17 | REPLACE | Incident coordination (Fn 8) | **Yes** — no incident-response automation exists in `services/`. | Fn 125 | Scaffolded, not wired |
| H18 | REPLACE | Someone starts a case study | **Partially.** Both `functions/47-case-study-writer` and `functions/26-client-advocacy-harvester` are `live` per `docs/function-register.md` — so the pieces Appendix B names (Fn 26 trigger → Fn 47 write) already exist as built packages, not merely scaffolded. What is not built is the H18 replacement specifically: `functions/119-client-permission-agent` (client-permission drafting) is v2 scaffolding only, so whatever human step currently secures client permission for a case study is unchanged even though the content pipeline around it is live. | Fn 26 → 119 | Fn 26 already live (pre-dates v2); Fn 119 scaffolded, not wired |
| H19 | REPLACE | Leadership accept/reject (opportunity review) | **Yes** — `functions/15-*`, `20-*`, `22-*` do not exist. | Fn 116 | Scaffolded, not wired |
| H20 | REPLACE | Spokesperson quotes | **Yes** — `functions/74-*` does not exist. | Fn 114 | Scaffolded, not wired |
| H21 | CONVERT | Spend thresholds | **Yes** — `functions/07-*`, `80-*` do not exist; no spend-card mechanism is built. | Quarterly `spend.*` card | Card kind exists in the new contract (PR #148); no function emits it |
| H22 | REPLACE | Metric owners | **Yes** — no `foundation.metric_definition` emitter exists. | Fn 122 | Scaffolded, not wired |
| H23 | CONVERT | Approve each transcript | **Yes** — Fireflies integration is unbuilt per `docs/architecture/12-integration-catalogue.md` (I18, cited in `docs/function-register.md`'s "What has changed" table lineage). | SP-001 | Seed defined; the integration it would apply to (Fireflies) does not exist to test the seed's own precondition (participant emails present) against |
| H24 | CONVERT | Seven non-negotiable approvals | **Yes** — the seven non-negotiables are enforced today via `services/gatekeeper`'s existing gate-token chain, not via typed card kinds. | Typed realtime `non_negotiable` kinds | Contract lands the kind taxonomy (PR #148); `services/gatekeeper` does not consume it yet (Appendix D PR 3) |
| H25 | CONVERT | Prompt change approval | **Yes** — no `system.prompt_change` card is ever emitted by any live path. | Card + eval diff | Card kind exists; no emitter |
| H26 | CONVERT | Level changes (trust-weighting) | **Yes** — `services/gatekeeper/policy/autonomy.yaml` is still hand-edited (no card-driven change process; confirmed no reference to `system.autonomy_level_change` anywhere under `services/gatekeeper` as of this baseline). | Fn 126 under §G2 | Not even scaffolded — Fn 126 exists as a package (PR #148) but the level-change PR path (Appendix D PR 6) is unstarted |
| H27 | CONVERT | Strategic objectives | **Yes** — `functions/38-*` does not exist (Fn 122/38 both unbuilt). | Fn 122 / 38 | Scaffolded (Fn 122 only), not wired |
| H28 | REPLACE | Approvers chased | **Yes** — `docs/run-the-loop.md`'s human-operator model (DRIFT-5) is unchanged. | Fn 117 | Scaffolded, not wired |
| H29 | CONVERT | Per-post consent, personal profiles | **Yes** — no standing-permission mechanism exists in production. | SP-003 | Seed defined, not enacted |
| H30 | CONVERT | "Fully autonomous team" exclusion, as design stance | **N/A — this is a documentation-only row.** | Reworded exclusion text | **Done** — the reworded exclusion ships in `docs/blueprint/agentic-marketing-engine-v2.md` itself (PR #148), the only one of the 30 rows actually complete at this baseline |

**Scorecard summary: 1 of 30 rows (H30) is done. 27 of 30 are scaffolded-but-
not-wired. 2 of 30 (H14, H26) are not even scaffolded at the data or
enforcement layer — both require an `agent_runs`/`gate_decisions` schema
migration (Appendix D PR 1) before any card can carry a Hit Rate or drive a
level change.** This is the measure the completion plan (`road-to-N-v2.html`)
is built against.

---

## 5. What this recount could not verify

- **The retention-expiry hypothesis for the two live-number discrepancies**
  (§3) — `caj-vault-retention-expiry`'s execution history was not queried.
- **Whether `session/s13-website` remains blocked for the same reason** as 3
  August beyond this session's own `/mcp` failure message, which withheld
  detail ("Failed to reconnect to canvas-wordpress (detail withheld on this
  connection)").
- **DRIFT-12's working-tree residue** — cannot be re-checked from a different
  session's filesystem.
- **Whether `docs/architecture/*` has been updated to reference v2 anywhere
  outside the two files this recount grepped** (`00-executive-summary.md`,
  `10-product-roadmap.md`) — the full eighteen-file set was not individually
  re-read for this pass.
- **`kpi_rollup_vault_utilisation`'s current behaviour** against whichever
  `smoke-test-v1` tagging turns out to be correct.
- **Any minutes-per-day or click-count target** — still not in either
  blueprint version. Recorded as unverified, not estimated, consistent with
  the 4 September audit's own appendix.
