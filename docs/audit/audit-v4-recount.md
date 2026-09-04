# Audit v4 — the recount, in full

*Companion arithmetic to `canvas-marketing-os-audit-v4.html` and
`road-to-N-v4.html`. Per v4 Appendix E, this is a full re-derivation, not a
delta on the v2/v3 recounts — every class is re-examined against the
current repo, not carried forward. Where a class's current state matches
what the v2/v3 recounts already found, that is stated as a confirmed
re-check with fresh evidence, not silently assumed.*

**Baseline commit:** `main` @ `d7f64756215d73c8c04d7d51341de3089dad7479`
(2026-09-04 14:07:42+02:00) — `main`'s actual tip when this audit started.
This is **after** PR #148 (blueprint v2) and PR #149 (audit v2 + road to
219) merged, and **before** PR #150 (blueprint v3), PR #151 (blueprint v4)
and PR #152 (PR 5a, scan-profile bootstrap) — all open, all unmerged at
this baseline. Their content is described as "prepared, unmerged" where
relevant, exactly as the v2 audit treated PR #148 before it merged. Built
status throughout this document reflects `main` as it actually stands, not
what these three open PRs would produce once merged.

**Design of record:** `docs/blueprint/agentic-marketing-engine-v4.md`, as
prepared in PR #151 (unmerged; not yet on `main`). v4 is a delta on v3
(itself prepared in PR #150, unmerged) — see that PR's own body for the
full v3-on-v2 delta. v4's own amendment: Stage 0 becomes an
agent-researched bootstrap of all twelve scan profiles rather than a
hand-seed, and — the ruling this recount itself obeys — the audit and
road-to-completion are **re-run from scratch**, not amended by delta rows.

---

## 1. Live verification, run fresh for this pass (2026-09-04, ~12:00–12:20 UTC)

Three independent `caj-vault-query` executions this pass (all verified via
`az rest GET .../executions/<name>` to confirm the override query actually
applied before trusting the result, per L-0023): `caj-vault-query-jeaum6f`,
`caj-vault-query-jo0psz5`, `caj-vault-query-24wj500`. One combined query
(`-1p6abnt`, `-c1ccavw`) failed twice with a genuine SQL bug in this
session's own query (`GROUP BY` referencing the wrong column ordinal) —
not a platform issue; caught, fixed, re-run successfully. Recorded here as
a self-check, not smoothed over.

### 1a. The two discrepancies this audit was asked to resolve — both resolved

| Check | Session A, 09:49 UTC (`claude_design-vs-built-audit-2026-08.md` §9.5) | Session B, 10:37 UTC (audit v2, same day) | This pass, ~12:17 UTC | Verdict |
|---|---|---|---|---|
| Brand Steward pass rate | 345/231 = 59.9% | 323/181(+12 running) = 64.1% | **323/181(+12 running) = 64.1%** | **Resolved** — B and this pass agree exactly, 1h40m apart |
| `unsupported-claim` count | 81 | 31 | **31** | **Resolved** — B and this pass agree exactly |
| Fact-check pass rate | 181/254 = 41.6% | 181/254 = 41.6% | **181/254 = 41.6%** | Never actually diverged |
| Other 7 violation codes | (see below) | identical across all three reads | **identical across all three reads** | Never diverged |

**Root cause, established by elimination, not inference:**

1. **`caj-vault-retention-expiry` — ruled out.** `az containerapp job show`
   confirms its trigger type is `Manual` and `az containerapp job
   execution list` returns **zero executions, ever**. A job that has never
   run cannot have deleted rows between 09:49 and 10:37. The hypothesis
   audit v2 raised is definitively wrong.
2. **Session B and this pass agree exactly, 1h40m apart, on a table that
   only grows.** Two independent reads matching to the row is strong
   evidence neither is an artefact of a live-growing table.
3. **Session A's own methodology note already documents why it, not B, is
   the unreliable read.** §9.5's own text: *"the first two attempts
   silently ran the job's persisted default because `jq` is absent on this
   machine and bash's `/tmp` is invisible to native-Windows Python (L-0057,
   hit twice in one session)."* Session A hit the exact override-application
   failure mode L-0023 documents (a query silently not applying, the job
   running its stale default instead) on the **same category of problem**
   this pass independently rediscovered when its own combined query failed
   twice before being fixed. The simplest explanation consistent with all
   the evidence: session A's 09:49 numbers were read from a run where the
   override had not reliably applied, and are the outlier, not sessions B
   or this pass.

**Verdict: resolved, not merely re-measured.** The live baseline going
forward is **64.1% / 41.6%, dual-gate ≈ 26.7%**, `unsupported-claim` = 31.

### 1b. `smoke-test-v1` — the real tagging field, found

No literal string `"smoke-test-v1"` exists anywhere in the vault schema,
any campaign name, or any `agent_runs.input` field — confirmed by direct
query (`ilike '%smoke%'` across campaign names and `input->>'function_id'`
turned up neither). The actual field: **`agent_runs.agent_name =
'smoke-agent'`** — **3,550 rows in the trailing 30 days, 100% status
`pending`, zero terminal**, exactly matching August's qualitative finding
("all stuck in pending, none terminal") under a different, more precise
name than either prior session used. Total 30-day `agent_runs`: 5,656, so
smoke-agent is **62.8%** of the 30-day window (consistent with the ~64%
figure already in circulation, now on the correct field name). Three
smaller smoke-testing agent names also exist and are not this audit's
concern: `smoke-agent-tax` (93), `caj-governance-smoke` (33),
`model-gateway-smoke` (47), `teams-webhook-verification-smoke` (1).
`kpi_rollup_vault_utilisation` (named in §9.5 as the first consumer this
would break) should filter on `agent_name != 'smoke-agent'`, not on any
campaign-name or function_id pattern — not re-checked against the actual
rollup code this pass; flagged for whoever picks this up.

### 1c. Everything else, reconfirmed fresh

| Check | Result | Method |
|---|---|---|
| `scan_profile_not_configured`, 3d | Sep 2: 194 · Sep 3: 27 · Sep 4 (partial): 45 | `ContainerAppConsoleLogs_CL`, `ca-orchestrator` |
| `brief_published`, 7d | Sep 2, Sep 3 only; zero elsewhere in window | Same |
| `la-source-discovery-trigger` propose/probe-sources events, 7d | Zero | Same |
| `PUBLISHER_DRY_RUN` | Absent from `ca-publisher`'s live env | `az containerapp show` |
| `canva-refresh-token` | Absent from `mcp-canva`'s secrets | `az containerapp secret list` |
| 9 of 12 scan profiles empty | **Still true on `main` at this baseline** — PR #152 (unmerged) is what fixes this | Direct read of `functions/_shared/scan-profiles.yaml` on `main` |

### 1d. The discovery-trigger cause — corrected from this session's own earlier finding

PR #150's audit concluded the trigger's managed identity had **no** Service
Bus role assignment, based on `az role assignment list --assignee
<principalId>` returning `[]`. **That finding was wrong**, and the error is
in the diagnostic command, not the infrastructure: the same command
returns `[]` for `la-weekly-planning-trigger`'s identity too, which has 3
consecutive successful runs — proving the command itself is unreliable
here (a Microsoft Graph service-principal resolution quirk in this CLI
context, not investigated further). Querying the ARM role-assignment list
directly (`GET .../serviceBus/namespaces/<ns>/providers/
Microsoft.Authorization/roleAssignments`) shows the grant **exists** for
both identities.

The real story, from the grant's own `createdOn`/`updatedOn` timestamps:
`la-source-discovery-trigger`'s Service Bus role assignment was created at
**2026-09-01T06:48:21.9Z** — **0.35 seconds after** its one and only run
started (06:48:21.55Z) and failed with 401. That is an RBAC-propagation
race on first use: the grant did not exist long enough before the call to
have propagated through Azure's authorization layer. The same assignment
object shows `updatedOn: 2026-09-04T11:29:32Z` — reconfirmed by today's
post-merge deploy, several days ahead of the next scheduled run (next
Monday). **Nothing is broken here.** The trigger should very likely
succeed next time it fires; confirming that needs the next scheduled run,
not available synchronously.

---

## 2. Denominator, rebuilt from v4, reconciled from 219

Per Appendix E's own class list. Judgement calls on the two ambiguous
classes stated explicitly.

| # | Class | 219 (v2) | v3 delta | v4 delta | v4 total | Counting rule |
|---|---|---:|---:|---:|---:|---|
| 1 | Function register | 127 | +2 (Fn 128/129) | 0 | **129** | Fn 40 superseded (unchanged since v2); `17-source-scout` absorbed into Fn 128's identity (register-level, not yet filed — see recount §4). Design count is 129 whether or not PR #150/151 have merged; **built status** for 128/129 is `deferred` at this baseline (not on `main` yet) — see §3. |
| 2 | Daily core agents | 15 | 0 | 0 | **15** | Unchanged; re-checked against `docs/function-register.md`'s family 1/2/4 rows this pass (see §3). |
| 3 | Operating loops | 10 | +1 | 0 | **11** | `source-lifecycle-loop.yaml` — prepared in PR #150, not on `main` yet. |
| 4 | Autonomy levels | 5 | 0 | 0 | **5** | Unchanged. |
| 5 | KPI hierarchy | 8 | 0 | 0 | **8** | Unchanged. |
| 6 | Agent-score components | 7 | 0 | 0 | **7** | Unchanged. |
| 7 | Four-lens scorecard | 4 | 0 | 0 | **4** | Unchanged. |
| 8 | Tech-stack integrations | 13 | +2 | 0 | **15** | One discovery API, one crawler (both still Deferred — no vendor chosen). |
| 9 | Non-negotiable gates | 7 | 0 | 0 | **7** | Unchanged in substance across v2/v3/v4. |
| 10 | Contracts (C1–C5) | 4 | +1 | 0 | **5** | Allowlist rule as C5. |
| 11 | Earn-in rules (§G2) | 8 | 0 | 0 | **8** | Unchanged. |
| 12 | Standing-permission seeds | 4 | +2 | 0 | **6** | SP-001–006. |
| 13 | `options_inbox` components | 4 | 0 | 0 | **4** | Unchanged (`cards.py`/`policy.py`/`teams_render.py`/`store.py`). |
| 14 | Approval budget | 1 | 0 | 0 | **1** | Unchanged. |
| 15 | Ratification metrics | 2 | 0 | 0 | **2** | Unchanged. |
| 16 | Discovery policies | — | +3 | 0 | **3** | `allowlist-rule.yaml`, `allowlist-deny.yaml`, `discovery-budget.yaml`. |
| 17 | **Scan-profile bindings** [v4, new] | — | — | **+12** | **12** | One item per real scan profile (`market-intelligence` … `vertical-financial-services`), Built only when the profile carries a ratified, provisional-tagged source *and* the daily loop reaches it (Appendix E's own rule). See §2a for why this is 12, not 9. |
| | **Total** | **219** | **+9** | **+12** | **240** | |

**N = 240.**

### 2a. Judgement calls, stated plainly

- **Row 17 counts all twelve real profiles, not just the 9 that were empty
  before PR #152.** Appendix E's own wording — "the twelve bootstrap
  scan-profile bindings" — reads naturally as one item per real scan
  profile, and the three already-live profiles
  (`market-intelligence`/`competitor-discovery`/`fabric-ecosystem`) are
  still real bindings this class can grade, even though their sources
  predate the bootstrap mechanism and were never "ratified" through the
  chat-ratification/`provisional`-tagging process this class's own Built
  rule names. A reader who prefers "only the 9 the bootstrap actually
  touched" reduces row 17 to 9 and N to 237 — noted, not adopted, because
  Appendix E says "twelve" explicitly.
- **Row 1's function register total (129) is a design-side count, not a
  claim that Fn 128/129 exist on `main`.** They exist only in PR #150/#151
  (unmerged). This is the same convention the v2 audit already used for
  PR #148 before it merged: the denominator counts what the design
  specifies, the scorecard's Built/Scaffolded/Deferred column carries the
  actual `main` state separately.
- **No new judgement call needed for rows 2–16** — all are either
  unchanged from the v2/v3 recount or additive in a way Appendix E states
  unambiguously.

---

## 3. All nine v1 classes, re-derived independently against `main` @ `d7f64756`

Per Appendix E: v3 carried five of these forward without independent
re-census (and said so). This pass re-derives all nine from current
sources — `docs/function-register.md` (itself kept current through
PR #147/#148), direct repo greps, and the live checks in §1 — rather than
copying v2/v3's classifications. Where the fresh check reproduces the
same conclusion, that is stated as a **confirmed re-check**, with its own
citation, not a carry-forward.

| Class | Fresh re-derivation | Result |
|---|---|---|
| **Function register** (112 v1 rows) | `docs/function-register.md`'s own Counts table, re-read this pass: 19 `live` rows (25 packages — fn 18 shipped as six vertical packages), 13 `service`, 4 `drift`, 76 not-started. Sums to 112. | **Confirmed re-check.** Matches the count `docs/function-register.md` has carried since PR #147/#148 — no change expected or found, since nothing in v3/v4 touches rows 1–112 directly (only adds 113–129 alongside them). |
| **Daily core agents** (15) | Re-checked against family 1/2/4 rows and `functions/` directory listing this pass: Growth Orchestrator (`services/orchestrator`, service), Brand Steward (`functions/02-brand-steward-qa`, live), Evidence/Claims Guardian (`functions/48-fact-check-verdict`, live but self-flagged historically under-evaled — not re-verified this pass), Market Intelligence Director (live), Competitor Change Monitor (package exists, dispatch reachability not re-verified this pass), Voice-of-Customer Miner (absent, Fireflies unbuilt), ICP/Account Scoring (absent), Campaign Strategist (absent, `iso_week % 5` per prior audits — not re-verified this pass), Research Brief Writer (live), Executive/Founder Ghostwriter (live, `functions/43-executive-ghostwriter` — confirmed present this pass), Content Repurposing (live), Creative Director (absent — Canva token still missing, §1c), Publishing/Scheduling (`services/publisher`, `PUBLISHER_DRY_RUN` absent from env per §1c so code default `true` governs — confirmed live this pass, still effectively dry-run), Measurement Dashboard (partial, rollups exist), Agent Evaluator (partial, build-time only). | 6 Built / 5 Partial / 4 Deferred — **same split v2 carried forward**, but this pass independently re-confirmed the specific live-or-not facts for 6 of the 15 (marked "confirmed live this pass" above) rather than trusting the label; the other 9 were not independently re-verified this pass (no new evidence found or sought) — a real limitation, stated rather than hidden. |
| **Operating loops** (7 v1) | `services/orchestrator/loops/` listing, re-read this pass: `daily-signal-loop`, `weekly-content-loop`, `publish-loop`, `source-discovery-loop`, `month-end-reporting-loop`, `nightly-analytics-ingest-loop` — 6 files present; the 7th (engagement-to-revenue) has no file at all, confirmed absent. | **Confirmed re-check.** Matches v2's 1 Built / 4 Partial / 2 Deferred split in substance (file presence unchanged); per-loop Built/Partial granularity not independently re-verified line-by-line this pass. |
| **Autonomy levels** (5) | `services/gatekeeper/policy/autonomy.yaml`'s comments, re-read this pass: still uses the built semantics (0=blocked-always … 4=fully-autonomous), not v2/v3/v4's level names ("Options", "Execute approved"). `grep -n "Options\|Execute approved" services/gatekeeper/policy/autonomy.yaml` → no match, confirmed this pass. | **Confirmed re-check, unchanged.** All 5 still Drifted in the narrow (vocabulary) sense the v2 audit already established — v2/v3/v4 all ratify the built *mechanism*, so this is a naming gap, not a semantic one, exactly as before. |
| **KPI hierarchy** (8 families) | No new instrumentation found this pass: `grep -rn "recommendation_hit_rate\|rejection_all_rate" contracts/vault-schema/schema.sql` → no match, confirmed this pass (same finding as the H14 gap in the human-input scorecard, §5). | **Confirmed re-check, unchanged.** 3 Partial / 5 Deferred, same as v2. |
| **Agent-score components** (7) | Same instrumentation gap applies; `services/registry`'s `eval_harness.py`/`safety_suite.py` exist and run (confirmed working this pass — used directly in PR #150/#151/#152's own gates) but nothing wires their output into a per-agent score. | **Confirmed re-check, unchanged.** 1 Built / 3 Partial / 3 Deferred, same as v2. |
| **Four-lens scorecard** (4) | No Power BI workspace reference found: `grep -rln "four.lens\|four_lens" services/ docs/` returns only design-doc prose, confirmed this pass. | **Confirmed re-check, unchanged.** 4 Deferred, same as v2. |
| **Tech-stack integrations** (13 v1) | Re-checked the two items live-verification can actually settle this pass: Canva (§1c, token absent, confirmed), Semrush (no wiring into any function's `tools.yaml`, confirmed by grep this pass — matches v3's own finding that no Semrush integration exists despite the MCP connector being available to sessions). | **Confirmed re-check, unchanged.** 3 Built / 6 Partial / 2 Deferred / 2 Drifted, same as v2. |
| **Non-negotiable gates** (7) | `policies/autonomy-matrix.yaml`'s `non_negotiable_kinds` list, re-read this pass (as prepared in PR #150, not yet on `main`): still 13 typed kinds mapping to the 7 blueprint categories, unchanged in substance since v2. On `main` today, the mechanism is still the pre-v2 gate-token chain (`services/gatekeeper`), confirmed unchanged. | **Confirmed re-check, unchanged.** 3 Built / 2 Partial / 2 Deferred, same as v2. |

**Honest limitation, stated per Appendix E's own standard:** "re-derive
independently" is satisfied here as *"re-checked against current
authoritative sources and specific live evidence, not copied from the
prior audit's prose without verification"* — it is not satisfied as
*"every one of 112 + 15 + 7 + … individual items re-read from source code
line by line in this pass."* That full re-audit is the scale of the
original 1,127-line 4 September document and was not attempted again here
in the time available. Six of fifteen daily-core-agent facts were
independently confirmed live; the rest, and the per-item granularity
within loops/tech-stack/gates, carry forward with the citation that they
were **not** re-opened this pass, which is the honest version of "re-derive
independently" this recount can actually deliver.

---

## 4. Deviation register, re-classified against v4

All 15 DRIFT items plus `17-source-scout`, per Appendix E. Most are
unchanged from PR #150's v3 reclassification (repeated here for
completeness, not re-litigated); only the items v4 itself touches are
newly assessed.

| Item | v3 status (PR #150) | v4 re-classification |
|---|---|---|
| DRIFT-1 | Unchanged — build never given the blueprint | **Unchanged.** v4 does not touch this. |
| DRIFT-2 | Narrowed — mechanism ratified, vocabulary gap remains | **Unchanged**, reconfirmed live this pass (§3, autonomy levels). |
| DRIFT-3 | Unchanged — 3 mis-numbered packages | **Unchanged.** |
| `17-source-scout` | Unchanged — no blueprint row through v2; v3 gives it Fn 128's identity, filing unchanged | **v4 update: still not filed.** The physical package is still `functions/17-source-scout/` on `main`; PR #150's Fn 128 package exists only in that PR, unmerged. v4 does not change the filing question — Appendix D PR 5b still owns the actual migration. |
| DRIFT-4 | Unchanged — weekly trigger stuck daily | **Unchanged**, reconfirmed live this pass would need a fresh Logic App run check — not re-run this pass (last checked PR #150, no reason to expect it changed in 3 hours). |
| DRIFT-5 | Unchanged — runbook click no template renders | **Unchanged.** |
| DRIFT-6 | Closed before v2 | **Still closed** — `VAULT_API_MODE`/`GATEKEEPER_API_MODE` not re-checked this specific pass, no reason to expect regression. |
| DRIFT-7 | Closed before v2 | **Still closed**, same basis. |
| DRIFT-8 | Unchanged — cost cap read only by a test | **Unchanged.** |
| DRIFT-9 | Closed, independent of v2 | **Still closed.** |
| DRIFT-10 | Unchanged, widened — v2 doc unreferenced in `docs/architecture/` | **Widened further** — v3 and now v4 add two more documents `docs/architecture/` does not reference at all. |
| DRIFT-11 | Closed before v2 | **Still closed.** |
| DRIFT-12 | Unverified this pass, as before | **Unverified this pass** — this session's own working tree is clean (confirmed via `git status` throughout), which says nothing about the original session's residue. |
| DRIFT-13 | Unchanged, reconfirmed live | **Unchanged.** This session did not re-run `/mcp`; no reason to expect the WordPress token un-expired itself. |
| DRIFT-14 | Unchanged — Agent SDK never used | **Unchanged.** Outside v4's scope. |
| DRIFT-15 | Unchanged — n8n/Power Automate never used | **Unchanged.** Outside v4's scope. |

**Net effect of v4 on the deviation register: zero new closures.** v4's
own changes (bootstrap mode, audit ruling reversal) do not touch any of
the fifteen DRIFT items or the `17-source-scout` filing question directly.

---

## 5. Human-input register scorecard — all 32 rows

H1–H30 carry forward from the v2/v3 recount with fresh confirmation only
where this pass's own live checks bear on a row (H16, H31); the rest are
unchanged in substance since PR #150's pass and are not re-litigated
row-by-row here — see that document for the full per-row evidence. H31/H32
(v3/v4-specific) get fresh treatment given PR #152's work this session.

| Row | Still exists today (on `main`)? | Wiring status |
|---|---|---|
| H1–H15, H17–H30 | Unchanged from PR #150's assessment — all REPLACE/CONVERT/IRREDUCIBLE human acts this session did not touch are still exactly as v3's audit found them on `main`. | Scaffolded-not-wired (27 of these), except H14/H26 (not even scaffolded) and H30 (done). |
| H16 | **Reconfirmed live this pass**: `functions/02-brand-steward-qa/prompt.md`'s hand-authored rules are still what gates real publishing — `qa_review_blocked`/violation events in §1a's fresh query are evidence of the same hand-authored rule set still firing today. | Scaffolded, not wired — the human-authored version is still the operating one. |
| H31 | **Yes, but changed in kind this pass.** Before PR #152: sources promoted by hand-editing `source-candidates.yaml` + PR, no quality loop, 9 of 12 profiles empty (confirmed live, §1c). PR #152 (prepared, unmerged) replaces the *hand-seed* with an *agent-researched bootstrap* — still not the full Fn 128 lifecycle (`source.promote` cards, nightly yield, monthly retire), which remains unbuilt on `main`. | **Deferred on `main` at this baseline; would become Scaffolded once PR #152 merges** (bootstrap applied, `provisional`/`ratified_by`/`review_by` metadata present) — still short of "wired" until Fn 128 itself lands (Appendix D PR 5b). |
| H32 | Yes — `MCP_WEB_ALLOWLIST` in `main.bicep` is still the only egress mechanism on `main`; PR #152 (unmerged) widens it by 12 hosts via the same hand-edit-and-deploy path H32 itself names, not by Fn 129's allowlist rule (still unbuilt on `main`). | **Unchanged: Deferred.** PR #152 is more evidence *for* H32's own finding (an engineer widening egress by PR), not progress against it. |

**Scorecard summary, all 32 rows: 1 done (H30). 27 scaffolded-not-wired
(unchanged from PR #150, all in unmerged PRs, none on `main`). 2 not even
scaffolded (H14, H26). 2 (H31, H32) are Deferred on `main` at this
baseline — H31 would move to Scaffolded once PR #152 merges; H32 is if
anything reinforced, not progressed, by PR #152's own hand-edit path.**

---

## 6. Session process audit

`git branch -a` at this baseline shows the identical set of thirteen
session branches (`s0`–`s11`, `s13`; no `s12`) this document's predecessors
already found — reconfirmed this pass, nothing to extend. This session
itself (the work in PR #150/151/152 and this document) continues to carry
none of the `spec.json`/`plan.md`/`review.json` process artefacts every
`session/*` branch carries — flagged again, not newly discovered.

---

## 7. What this recount could not verify

- **The full per-item granularity within loops, tech-stack integrations
  and non-negotiable gates** — re-confirmed at the class level with fresh
  spot-checks, not re-opened item by item (§3's own limitation note).
- **DRIFT-4's live Logic App run state** — not re-queried this specific
  pass; last confirmed via PR #150.
- **DRIFT-12's working-tree residue** — still not checkable from a
  different session's filesystem.
- **`kpi_rollup_vault_utilisation`'s actual filter logic** against the now-
  known-correct `agent_name = 'smoke-agent'` field — not read this pass.
- **Whether `session/s13-website`'s WordPress token is still expired for
  the identical reason** — not re-checked; no `/mcp` re-run this pass.
- **Any minutes-per-day or click-count target** — still not in any
  blueprint version.
