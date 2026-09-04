# Canvas Marketing OS — Design vs Built Audit

**Date:** 4 September 2026
**Design of record:** `canvas_agentic_marketing_engine_blueprint_1.docx` (22 July 2026; 1,412 extracted paragraphs; 112 functions / 15 daily agents / 7 loops)
**Build of record:** `main` @ `b90b246` (10 August 2026)
**Method:** every claim below cites a file path, config value or code line. Where I could not verify from the repo, the line says **unverified**.

---

## 0. A finding that shapes everything else

`canvas_agentic_marketing_engine_blueprint_1.docx` **is not, and never has been, in version control.**

```
$ git ls-files | grep -i "docx\|blueprint"      -> (empty)
$ git log --all -- '*blueprint*'                -> (empty)
$ git status                                    -> ?? canvas_agentic_marketing_engine_blueprint_1.docx
```

Every build session worked without it. The build knew this, and said so in the file that
defines the autonomy ladder:

> "There is no autonomy blueprint anywhere in this repo (confirmed by repo-wide grep), so
> the level semantics and the function_id / action_class taxonomy below are this session's
> first-draft convention."
> — `services/gatekeeper/policy/autonomy.yaml` L15–17

That one sentence explains most of §5's category-(d) register. The build did not drift away
from the design; **for most of its life it could not see the design.** Deviations that look
like drift are frequently independent re-derivation of something the blueprint had already
settled — with a different answer.

---

## 1. Executive verdict

The **governance spine** of the blueprint is built to a standard above the design: a signed
gate-token approval chain, a fail-closed autonomy policy, a kill switch with sub-5-second
propagation by construction, per-model cost metering, POPIA-aware retention, and a genuinely
novel forced-dry-run canary. The **agent workforce** is roughly a fifth built: 24 function
packages exist against 112 designed, 12 of those are reachable by a running handler, and the
intelligence fan-out that gives the morning brief its content is 23 declared tasks of which
17 are no-ops. The **operator experience is the weakest layer and the least honestly
documented**: the daily brief is written to a Postgres table no human-facing surface renders,
the console approval inbox is explicitly read-only, the console is hardcoded to `mock` mode in
Bicep so every governance screen shows seeded fixtures, and `docs/run-the-loop.md` step 4
instructs the operator to click an Approve button that `console/app/templates/approvals.html`
does not contain. Nothing has ever been published: Buffer's own API confirms zero posts
created via API across 100 sampled posts (`docs/architecture/19-live-verification-log.md` P1),
while 76 real posts went out through the Buffer UI in the same window — the governed path and
the real marketing workflow are disjoint.

| Designed item class | Designed | Built | Partial | Blocked | Deferred | Drifted |
|---|---:|---:|---:|---:|---:|---:|
| Function register (112 rows) | 112 | 12 | 7 | 4 | 89 | 0 |
| Daily core agents | 15 | 6 | 5 | 0 | 4 | 0 |
| Operating loops | 7 | 1 | 4 | 0 | 2 | 0 |
| Autonomy levels (0–4) | 5 | 0 | 0 | 0 | 0 | 5 |
| KPI hierarchy (families) | 8 | 0 | 3 | 0 | 5 | 0 |
| Agent-score components | 7 | 1 | 3 | 0 | 3 | 0 |
| Four-lens scorecard | 4 | 0 | 0 | 0 | 4 | 0 |
| Technology-stack integrations | 13 | 3 | 6 | 0 | 2 | 2 |
| Non-negotiable approval gates | 7 | 3 | 2 | 0 | 2 | 0 |
| **Total** | **178** | **26** | **30** | **4** | **111** | **7** |

*Counting rules — the five states are mutually exclusive, and each row sums to its Designed
figure. **Built** = a code path a running loop reaches, doing the designed work. **Partial** =
exists but inert, fixture-mode, or materially narrower. **Blocked** = specced and stopped on an
external dependency (new; see §8.2). **Deferred** = not started, correctly sequenced, with a
written owner. **Drifted** = the realisation diverges from the design with no decision behind
it.*

**Two corrections against the first version of this table** (recounted 4 September, after the
session audit in §8):

1. **Denominator, 183 → 178.** The integrations row previously counted the *repo's* own
   18-row integration catalogue as if it were the design. That is a category error: the
   correct denominator is the blueprint's own technology-stack table, 13 rows
   (L1142–1185). Recounting against it surfaced two drift items the repo catalogue could not
   see, because a catalogue of what was built cannot show what was designed and skipped.
2. **The columns are now exclusive.** The old function-register row (12/12/85/3) double-counted:
   its "3 drifted" were the misnumbered packages, which were also inside its "12 built", and
   its 12 partial counted *packages* against a column of *blueprint rows* — the six `18-0N`
   vertical packages are one designed row, not six.

**Scorecard drift (7) and the deviation register (15) are different denominators, deliberately.**
The scorecard counts *designed items whose realisation diverges*. The register in §5(d) counts
*incidents* — several of which attach to items that are otherwise correctly Built, such as a
function that runs fine but ships with no evals. Neither number is a subset of the other.

---

## 2. The user journey audit

### 2a. What the blueprint actually promises — and what it does not

Three corrections to the audit brief before comparing, because inventing design intent would
poison the result:

- The blueprint **never states a minutes-per-day or click-count target.** Its only human-load
  statement is *"One strong marketing operator can coordinate the system once the foundations
  are stable"* (L1301). Any "promised N minutes a day" is **unverified** — it is not in this
  document.
- The blueprint **never promises Teams approval cards.** Teams appears once, in the technology
  stack: *"Knowledge and approvals | SharePoint / Teams / OneDrive"* (L1159–1161). The
  approval mechanism it specifies is functional, not surface-specific — function 6, Human
  Approval Router: *"Apply autonomy levels; package concise review briefs; chase approvals; log
  decisions and reasons"* (L191). **Teams-as-the-approval-surface is a build decision, not a
  design promise** — and it is the better-evidenced one (§5, L-0033).
- There is **no "Knowledge Vault"** in the blueprint. There is a *Knowledge layer* (L67–68) and
  a *Knowledge Base Librarian*, function 106 (L861–865). The repo's Vault service is a closer
  match to the blueprint's **shared marketing object model** (L1001–1030) than to either.

What the blueprint *does* promise for the operator's day, verbatim:

| Source | Promise |
|---|---|
| L1259–1262 | **Continuous:** ingest approved signals; monitor critical anomalies, competitor changes and reputation; execute approved schedules. **Daily:** prioritised intelligence brief; account trigger alerts; production queue; publishing pre-flight; sales handoff review. |
| L1272–1281 | Mon intelligence · Tue strategy · Wed production · Thu QA + publishing · Fri analytics + retro |
| L1054–1060 | Seven action classes always requiring a human: publishing from company/personal accounts; new claims, pricing, guarantees, comparatives; client names, logos, data, testimonials; paid-media spend above threshold; legal/regulatory/sensitive statements; changes to prompts, tools, permissions or autonomy level; crisis responses |
| L770–774 | Function 92: role-specific, decision-ready **Power BI** dashboards — executive, campaign, content, funnel, agent-performance, with drill-through |
| L1386 | Flagship output: a recurring **Canvas Executive Data & AI Decision Brief** that fans out into posts, video, article, carousel, email, webinar and sales talking points |

### 2b. DESIGNED vs TODAY — the operator's day

| When | DESIGNED (blueprint) | TODAY (code, cited) |
|---|---|---|
| **Overnight** | Continuous ingestion of external + internal signals: web, competitors, Microsoft ecosystem, industry news, social/search trends, approved meeting transcripts, CRM, proposals, campaign and website data (L65–66) | `caj-analytics-nightly-ingest`, cron `0 1 * * *` UTC = 03:00 SAST (`services/orchestrator/loops/nightly-analytics-ingest-loop.yaml` header). Four sources: Buffer **live**; GA4 / Search Console have **live code paths added 7 Aug** (`ga4_client.py` L9–16) but credentials are pending — `docs/credentials-runbook.md` §11–12: *"Fixture-first is mandatory for this build — no live GA4 Data API call is made."* LinkedIn **fixture only**. **No CRM anywhere**: `grep -rn -i "dynamics\|hubspot" services/ infra/ mcp/` returns only a comment in `services/publisher/app/esp_client.py`. **No transcripts**: Fireflies is `I18 · not implemented` (`docs/architecture/12-integration-catalogue.md`). |
| **06:00** | A prioritised intelligence brief on the operator's desk | `la-daily-signal-loop-trigger` fires (`infra/modules/scheduling/daily-signal-loop-trigger.bicep` L37–44 — `hours: ['6']`, South Africa Standard Time), creating **23 tasks**. `ingest-signals` fetches **4 URLs across 3 domains** (`functions/09-market-intelligence-director/fetch_sources.yaml`) through mcp-web, truncates each body to 2,000 characters (`dispatch.py` L689), and sends them to function 09 on `claude-haiku` via model-gateway. |
| | The brief is *synthesised* by the Market Intelligence Director: commission research, deduplicate, assess confidence, convert signals into opportunities, threats and actions (L216) | `draft_brief_handler` makes **no LLM call at all** — `_render_brief()` is string concatenation over function 09's JSON, and its docstring says so: *"Deterministic rendering (NO LLM call, per plan step 9)"* (`dispatch.py` L768–771). The synthesis happened one node earlier, inside `ingest-signals`. |
| | Signal-to-opportunity loop: dedupe → assess evidence and strategic fit → create opportunity/threat cards → prioritise (L134–135) | The 11-way competitor/vertical fan-out, the dedupe join, the response strategist and **both brief-rollup nodes are declared in the loop YAML and reach no handler.** Of the daily loop's 22 distinct `task_type`s, **5 are in `DISPATCH_TABLE`**; the other 17 — including `morning-brief-rollup` and `executive-brief-rollup` — hit `legacy_task_pass_through`: `RUNNING -> COMPLETED -> advance_dependents`, no work (`dispatch.py` L2176–2193). Verified: `grep -rn "morning-brief-rollup" --include=*.py services/` returns zero non-test hits. `vault.opportunity_cards` exists in the frozen schema (`contracts/vault-schema/schema.sql` L75) and nothing ever writes it. |
| **~06:05 — where the brief lands** | On the operator's surface, as the day's first artefact | Two rows in Postgres: `vault.briefs` — *"Morning Brief — {topic}"* and *"Executive Edition — {topic}"* (`dispatch.py` L824–835). Then `teams_notify.notify_brief_ready(...)` → **no-op**, because `TEAMS_WEBHOOK_URL` is never set on `ca-orchestrator` by any Bicep file or workflow (`grep -rn "TEAMS_WEBHOOK_URL" infra/ .github/` matches only `infra/modules/governance/gatekeeper-app.bicep` L171 — the *gatekeeper* app). And the console renders **no brief page at all**: its routes are `/`, `/tasks`, `/tasks/{ref}/trace`, `/approvals`, `/vault-search`, `/costs`, `/kill-switch` (`console/app/routes_reads.py`), and `/vault-search` lists only two object types, `assets` and `signals` (`console/app/services.py` L74–77). **`briefs` is not one of them.** |
| | | **Net: there is no surface today on which Pieter can read the morning brief.** He must `curl` the unauthenticated Vault API or query Postgres directly. `docs/run-the-loop.md` — the operator runbook — has seven numbered steps and **never tells him how to read the brief.** |
| **07:00** | *Monday:* weekly plan set; briefs issued; experiment decisions; account plays selected (L1274–1275) | `la-weekly-planning-trigger` fires **every day**, not Mondays: `frequency: 'Day', interval: 1`, with a comment reading *"TEMPORARY (6 Aug 2026)… Revert to the weekly shape once the daily-review iteration is done"* (`infra/modules/scheduling/weekly-planning-trigger.bicep` L60–72). Four weeks later it is still daily. Task ids are `uuid5(event_id, task_id)` (`decompose.py` L25), so each fire creates a **fresh 26-task graph** — the full content studio runs up to 7× a week. |
| | Campaign Strategist (fn 34): audience, insight, offer, journey, content, channels, budget, experiment plan, sales handoff (L389) | `plan_content_monday_handler` = `CONTENT_PILLARS[iso_week % 5]`. Its docstring: *"Monday planning is deterministic, NOT an LLM call — there is nothing to draft yet, only a pillar to choose for the week"* (`dispatch.py` L1205–1216). No audience, no offer, no budget, no experiment, no handoff. |
| **Wednesday** | Production: drafting, design and repurposing against briefs; SME input captured (L1276–1277) | **This part genuinely works.** Six drafting handlers make real LLM calls through the gateway — functions 39, 43, 45, 46, 47, 52 (`dispatch.py` L1518–1731). All 13 weekly `task_type`s are in `DISPATCH_TABLE`. (`docs/architecture/03-user-journeys.md` L157 — *"none of these 15 task_types is in DISPATCH_TABLE"* — is **stale**: written 6 Aug, superseded by the 10 Aug round-34 work.) SME harvesting (blueprint fn 40) does not exist. |
| **Thursday** | QA and publishing: brand, evidence and compliance review; approvals cleared; queue scheduled (L1278–1279) | Per-draft dual-verdict gate, 12 tasks: Brand Steward (fn 02) **and** fact-check (fn 48) per draft, restructured from an aggregate gate on Pieter's explicit direction after a live cascade failure (`weekly-content-loop.yaml` round-34 header; `docs/content-learnings.md`). This is **better than designed** — the blueprint never specifies per-item gate isolation. |
| **Thursday — the approval click** | The Human Approval Router packages a concise review brief and routes it to the named approver (L189–193) | **There is no surface that hands the operator a clickable approve link.** (1) `console/app/templates/approvals.html` L5, verbatim: *"Read-only view of Gatekeeper approval decisions. **No approve/reject action is available here.**"* (2) The console is hardcoded to fixtures — `infra/modules/console/console-app.bicep` L174 and L182 set `VAULT_API_MODE: 'mock'` and `GATEKEEPER_API_MODE: 'mock'` as string literals, and `deploy-console.yml` L583 injects `CONSOLE_SEED_FIXTURES_JSON_B64` — so `/approvals`, `/tasks`, `/costs` and `/kill-switch` all render **seeded fixture data, not production rows**. (3) Teams is off. (4) The only real approve surface is `GET /approval-action/{link_token}?choice=approve` on `ca-gatekeeper-approval` (`services/gatekeeper/app/routers/approval_action.py` L86–90) — reachable only if you already hold the opaque token from the `/gate-check` HTTP response or from `governance.approval_inbox`. |
| | | And `docs/run-the-loop.md` **step 4 asks the operator to do the impossible**: *"Click **Approve** on the `[LOOP-PROOF]`-marked row"* on `/approvals`. That button does not exist. This is the sharpest doc-vs-code contradiction in the repo. |
| **Friday** | Publishing to approved channels; analytics; agent scorecards; learning release (L1280–1281) | `schedule-social-buffer` ×4 and `publish-newsletter` ×1 each request a **real** gate-check and then stop — neither ever calls Buffer or sends an email itself (`dispatch.py` L2020–2030). Downstream, `PUBLISHER_DRY_RUN` appears in **no** Bicep file and **no** workflow, so it takes its code default `"true"` (`services/publisher/app/config.py` L17–22). Confirmed from Buffer's own side: of 100 sampled posts, `via` is 98 `buffer` + 2 `network` — **zero created via API** (`19-live-verification-log.md` P1). |
| | Power BI executive / campaign / content / funnel / agent-performance dashboards with drill-through (fn 92) | Four KPI rollup tables — engagement-by-archetype, publishing reliability, cost-per-accepted-asset, Vault utilisation (`services/analytics-ingest/migrations/*.sql` L146–189). `analytics/powerbi/analytics-dataset.json` describes itself: *"starter definition only — no live Power BI workspace/dataset is provisioned or refreshed by this build."* |
| **Time / clicks** | **Not specified in the blueprint** (unverified) | **Clicks available in the governed path today: zero.** Every screen the operator can reach is read-only, fixture-backed, or both. The real marketing day happens in the Buffer UI, outside the platform — 76 posts sent between March and 5 August 2026. `19-live-verification-log.md` V1: *"The platform exists to govern marketing publishing. It is not in the path of any actual marketing publishing."* |

### 2c. The gaps that would close the distance

Effort key: **config** = a value change, no code · **small PR** = < ~200 lines, one service ·
**session-sized** = a full build session with review.

| # | Gap | Fix | Effort |
|---|---|---|---|
| G1 | Console shows fixtures, not production data | Flip `VAULT_API_MODE` / `GATEKEEPER_API_MODE` to `real` in `console-app.bicep` L174/L182 (both base URLs are already wired and passed in). Read TD-10 first — this is deferred integration work (INTEG-001/002), so budget a follow-up PR for shape mismatches. | **config + small PR** |
| G2 | No approve/reject action anywhere a human can find | Render `approve_url` / `reject_url` (Gatekeeper already returns both from `/gate-check`) as two links in `approvals.html`. Identity still comes from Easy Auth on `ca-gatekeeper-approval`, so this adds no new trust boundary. | **small PR** |
| G3 | `run-the-loop.md` step 4 documents a button that does not exist | Correct the step to the real `approval-action` URL, or land G2 and make the doc true. Do not leave both. | **config** (doc-only) |
| G4 | The morning brief has no reader | Add `"briefs"` to `_OBJECT_TYPE_LISTERS` (`console/app/services.py` L74) and a `/brief/latest` route rendering `body_text`. Vault already exposes `GET /briefs` (`services/vault/vault/models.py` L107). | **small PR** |
| G5 | The Teams brief card can never fire | Add `TEAMS_WEBHOOK_URL` (secretRef) to `ca-orchestrator`'s env in Bicep — it exists only on `ca-gatekeeper` — **and** populate `teams-webhook-url` in Key Vault (`credentials-runbook.md` §8: *"pending, not yet populated"*). Code is complete on both sides. | **config** |
| G6 | The weekly content studio runs daily | Revert `weekly-planning-trigger.bicep` L66–72 to the `frequency: 'Week' / weekDays: ['Monday']` shape its own comment preserves. Also removes ~6 daily LLM drafts and 12 QA verdicts of pure waste. | **config** |
| G7 | 17 of 22 daily task types are silent no-ops | Registry-driven generic dispatch (`10-product-roadmap.md` R1, scored 8.7, estimated 2–3 weeks): one handler resolving `task_type` → function package → gateway call, replacing 11 hand-written scanner handlers. Until then the morning brief carries function 09's four news URLs and nothing from the 12 intelligence packages. | **session-sized** |
| G8 | A green loop is indistinguishable from a silent no-op loop | Make `legacy_task_pass_through` emit a WARNING and a distinct span attribute; add a CI test asserting every `task_type` in every shipped loop YAML is in `DISPATCH_TABLE` or on an explicit allowlist. `09-technical-debt.md` names this exact hole: *"No test that 20 unwired packages are unwired — the pass-through gap is invisible to CI; every loop goes green."* | **small PR** |
| G9 | Nothing publishes, and nothing on screen says so | Set `PUBLISHER_DRY_RUN` explicitly in Bicep rather than by omission, and surface the effective value on the approvals page. An operator approving a card today gets no on-screen indication that approval leads to a `published_dry_run` row and no post. | **small PR** |
| G10 | fn 48 gates Friday publishing with zero evals | Write 5 golden eval tasks for `functions/48-fact-check-verdict/evals/` (currently absent — the only package of 24 with none) and get Pieter's review of the prompt its own `skill.md` flags as *"FIRST DRAFT… Not yet reviewed or approved by Pieter as settled QA policy."* | **small PR** |

---

## 3. Architecture fidelity

### 3.1 The seven loops

| Blueprint loop (L134–147) | Built | Evidence |
|---|---|---|
| Signal-to-opportunity | **PARTIAL** — ingest and brief run; dedupe, scoring, card creation and prioritisation are declared no-ops | `daily-signal-loop.yaml`; 17 of 22 task types absent from `DISPATCH_TABLE`; `vault.opportunity_cards` exists and nothing writes it (TD-21) |
| Insight-to-content | **BUILT** — the one loop matching the design end to end, minus SME harvesting | `weekly-content-loop.yaml`, 26 tasks, all 13 task types dispatched |
| Content-to-distribution | **PARTIAL** — repurposing and channel adaptation exist; scheduling stops at the gate-check; engagement/response capture absent | `dispatch.py` L2032 `schedule_social_buffer_handler`; no Social Engagement agent (blueprint fn 67) |
| Engagement-to-revenue | **ABSENT** — no CRM, no scoring, no routing, no opportunity linkage | no Dynamics/HubSpot integration anywhere in `services/`, `infra/`, `mcp/` |
| Campaign experiment | **ABSENT** | no experiment table, no variant machinery, no stopping rules |
| Performance-to-learning | **PARTIAL** — 4 nightly KPI rollups; no diagnosis, no playbook update, no backlog write-back | `analytics_ingest/rollup.py` |
| Agent improvement | **CHANGED SHAPE — see §6** | `.compound/learnings/` (79 build learnings) + `docs/content-learnings.md` (agent-output learnings). Both human-in-the-loop; neither automated |

### 3.2 The autonomy ladder — a clean semantic inversion

The most consequential changed-shape item in the build.

| Level | Blueprint (L1036–1050) | `services/gatekeeper/policy/autonomy.yaml` (L6–11) |
|---|---|---|
| 0 | **Suggest** — research, analyse, recommend; no external action | **Blocked always** — Gatekeeper refuses; no approval can unblock it |
| 1 | **Draft** — create drafts for human approval | **Approval-required**, single approver |
| 2 | **Execute approved** — carry out a pre-approved plan | **Approval-required, elevated** (behaves identically to 1 today — TD-27) |
| 3 | **Bounded optimise** — adjust within explicit limits | **Auto-approved and audited** — no human in the loop |
| 4 | **Autonomous low risk** — operate with monitoring and rollback | **Fully autonomous passthrough** — logged only |

The blueprint's ladder is a scale of **agent capability**; the built one is a scale of **gate
strictness**, and at the bottom they run in opposite directions. A blueprint-level-0 function
("new agents; high-risk domains") is the *safest* thing to deploy; a built-level-0 function
cannot execute at all. `default_level: 0` — fail-closed — is exactly right under the built
semantics and exactly wrong under the blueprint's.

**Why the built shape won, and why it should stand.** Fail-closed-by-default is a stronger
safety property than the design's, and it is enforced by a test (`tests/test_policy.py`: no
`publish` entry above level 2, no `smoke.*` / `test.*` function_id). This is not a defect — it
is a better answer to a question the design also answered. But **two documents now define
"level 2" incompatibly**, and the number is rendered to a human on the approval card
(`approvals.html`, `<th>Level</th>`). Reconcile the vocabulary; keep the built mechanism.

Missing from the built ladder: the blueprint's **trust-weighting rule** (L1051) — a function
implicated in a material failure has its autonomy reduced before its next assignment. Nothing
in `autonomy.yaml` or Gatekeeper adjusts a level from outcomes. Correctly identified as future
work in `10-product-roadmap.md` R2.

### 3.3 The 15 daily-core agents

| # | Blueprint agent | Status | Evidence |
|---|---|---|---|
| 1 | Growth Orchestrator | **CHANGED SHAPE** | `services/orchestrator` is a deterministic DAG runner over YAML, not a judging agent; it never reprioritises. This matches the blueprint's own principle — *"Deterministic workflow for routing and publishing; agents for judgment"* (L32) — more faithfully than the blueprint's own fn 1 does. |
| 2 | Brand Steward | **BUILT** | `functions/02-brand-steward-qa`, 6 evals, runs on every draft |
| 3 | Evidence and Claims Guardian | **PARTIAL** | `functions/48-fact-check-verdict` — **0 evals**, no `tools.yaml`, self-flagged unreviewed |
| 4 | Market Intelligence Director | **BUILT** | `functions/09-market-intelligence-director`; 4 URLs across 3 domains |
| 5 | Competitor Change Monitor | **PARTIAL** | `functions/11-competitor-change-monitor` exists with 6 evals; its `task_type` reaches no handler |
| 6 | Voice-of-Customer Miner | **ABSENT** | Fireflies `I18 · not implemented` |
| 7 | ICP and Account Scoring | **ABSENT** | `score-signals` is a pass-through; no account model, no `tenant_id` in any schema |
| 8 | Campaign Strategist | **ABSENT** | `plan_content_monday_handler` = `iso_week % 5` |
| 9 | Research Brief Writer | **BUILT** | `functions/41-research-brief-writer` |
| 10 | Executive/Founder Ghostwriter | **BUILT** | `functions/43-executive-ghostwriter` |
| 11 | Content Repurposing Agent | **BUILT** | `functions/52-content-repurposer` |
| 12 | Creative Director | **ABSENT** | mcp-canva runs fixture-mode; `canva-refresh-token` pending; brand-template existence unverified (P5) |
| 13 | Publishing and Scheduling | **PARTIAL** | `services/publisher` + mcp-buffer, permanently dry-run |
| 14 | Measurement Dashboard | **PARTIAL** | 4 rollups + a starter dataset JSON; no workspace provisioned |
| 15 | Agent Evaluator | **PARTIAL** | `services/registry/eval_harness.py` is build-time only; `registry.yml` CI covers **3 of 24** packages (TD-18) |

### 3.4 Governance and metering — where the build exceeds the design

Six mechanisms exist in code that the blueprint asks for only in principle, plus one it never
imagined:

- **Cryptographically signed gate tokens** — RS256 via Key Vault, a JTI replay ledger, and
  content-hash boundary binding (`services/publisher/app/verifier.py`, `jti_ledger.py`). The
  blueprint says "approval gates"; the build says "and here is the proof the approval covered
  *these bytes*."
- **Possession ≠ identity** — the approve link carries only an opaque `token_urlsafe(32)`; the
  recorded approver is the Easy Auth principal *on this request*, and `auth.py` holds no
  module-level state so an identity can never be cached between requests
  (`approval_inbox.py` L8–14).
- **Atomic single use** — `UPDATE … AND link_consumed_at IS NULL`: two concurrent clicks
  cannot both win.
- **A kill switch with a stated propagation contract** — a fresh `SELECT` on every decision in
  both Gatekeeper and Publisher, deliberately uncached: *"a cache with any TTL above zero would
  make the 5s bound a function of cache expiry rather than of the operator's action."*
- **Deliberate removal of a privileged test path** — a `smoke.governance_cycle` level-4 entry
  was removed from `autonomy.yaml` and replaced by a smoke job that pre-seeds a real approval
  row over an admin DB connection, so gaining that capability now requires database write
  credentials rather than knowledge of a function_id (L-0029; `autonomy.yaml` L63–80).
- **The S8 proof circuit** — a canary exercising the real signal→brief→draft→QA→approval path
  against the live platform, with a structural guarantee it can never publish:
  `services/publisher/app/vault_lookup.py` forces `dry_run=True` whenever the asset's Vault
  `agent_run.agent_name == "loop-proof-circuit"`, *regardless of `PUBLISHER_DRY_RUN`*. The two
  services share no library, so the constant is duplicated and a test in each asserts they stay
  equal. Nothing like this appears in the blueprint.

**Metering** matches the design's intent (fn 109, Model Router and Cost Optimizer) in data but
not in behaviour. Every gateway completion writes a `costs` row with function attribution, and
`routing.yaml` is git-versioned data with live-verified Anthropic model ids (L-0026). But
`DAILY_LOOP_BUDGET_USD` (`services/orchestrator/orchestrator/config.py` L92) is **read by
exactly one thing — an e2e test** (`tests/e2e/test_cost_metering_below_budget.py`). No runtime
code checks a budget before or during a run, while `docs/run-the-loop.md` L317–321 tells
operators to *"cite `DAILY_LOOP_BUDGET_USD` by name as the single source of truth for that
threshold"* — which reads as an enforced cap and is not one. Category (d).

---

## 4. Function census

Blueprint: 112 rows across 9 families. Repo: **24 packages** under `functions/` with a full
`prompt.md` / `skill.md` / `schema.json` shape, plus `_shared/` and `task-worker/` (a
health-check placeholder — TD-29).

| # | Blueprint family | Designed | Package exists | Reachable by a handler | Owning wave / note |
|---|---|---:|---:|---:|---|
| 1 | Executive command, governance and control | 8 | 0 | 0 | Replaced by services, not packages: Growth Orchestrator → `services/orchestrator`, Approval Router → `services/gatekeeper`, Budget Allocator → model-gateway metering. Functions **5 (Legal/Privacy/Consent Gatekeeper)** and **8 (Incident and Recovery)** have no counterpart at all. |
| 2 | Market, competitor and customer intelligence | 18 | 12 | 1 (fn 09) | S10 shipped the packages; R1 (registry-driven dispatch) owns activation |
| 3 | Segmentation, positioning and planning | 12 | 0 | 0 | Unstarted — no ICP model, no account/buying-group tables, no campaign card |
| 4 | Research, content and thought leadership studio | 16 | 9 | 9 | S11 — **the one fully-built family** |
| 5 | Creative, design and digital experience | 10 | 0 | 0 | Blueprint L1244 defers video/motion (58–60) explicitly; 55–57 and 61–64 are simply unstarted |
| 6 | Publishing, distribution, community and reputation | 11 | 0 | 0 | fn 65 exists as `services/publisher` (a service, not a package); 66–75 unstarted |
| 7 | Demand generation, account activation and revenue support | 14 | 0 | 0 | Gated on a CRM decision that has not been made |
| 8 | Measurement, experimentation and continuous learning | 13 | 0 | 0 | fns 90–93 partially exist as `services/analytics-ingest`; 94–102 unstarted |
| 9 | Agent platform, knowledge and engineering | 10 | 0 | 0 | Fully realised as *infrastructure* rather than agents: `services/vault` (106), `services/model-gateway` (109), `telemetry-lib` (110), `services/registry` (112), `mcp/` (105) |
| | **Total** | **112** | **21** | **10** | |

Plus 3 packages whose ids do not map to the blueprint register at all (§5, DRIFT-3):
`02-brand-steward-qa`, `42-linkedin-post-writer`, `48-fact-check-verdict`.

**Empty families: 1, 3, 5, 6, 7, 8, 9** — seven of nine. Families 1 and 9 are empty because
their work became services, which is the right shape; the other five are genuinely unstarted.

**Eval coverage:** 23 of 24 packages carry 5–6 golden eval tasks.
`48-fact-check-verdict` carries **zero** and would fail `validate_package.py`
(`DEFAULT_MIN_EVAL_TASKS = 5`, L54) — and would never be caught, because `registry.yml` L50–52
hardcodes three package paths (02, 09, 42) and has done since S10 flagged it
(`docs/brief-rollup-and-followups.md` §(c)).

---

## 5. Deviation register

**(a)** forced by platform reality · **(b)** explicit governance/scope ruling by the operator ·
**(c)** sequencing, correctly deferred · **(d)** genuine drift/debt — nobody decided it.

### (a) Forced by platform reality

| Deviation | Evidence | Note |
|---|---|---|
| Teams approval cards are `Action.OpenUrl` deep links, never in-card postback | L-0033 — classic O365-Connector webhooks retired May 2026; `teams_client.py` L1–15 | Also the better design: a submit-style postback would make "who clicked" a claim of the card payload rather than an authenticated identity |
| The Entra-protected approval surface is a **physically separate Container App** | L-0032; `ca-gatekeeper` internal-only vs `ca-gatekeeper-approval` external | Container Apps cannot mix per-route auth within one app |
| Registry artefact signed with a committed dev key | L-0031; `19-live-verification-log.md` P4 — Key Vault has no Ed25519 at *any* tier, including premium and Managed HSM | Not a SKU limitation; needs an ES256 code change, not a config swap (TD-25) |
| Service Bus Standard SKU on a public endpoint | `docs/accepted-risks.md`; `.loop/spec.json` INFRA-006 | Budget-owner decision plus three compensating controls |
| Vertical intelligence built as 6 packages, not 1 | `docs/function-register-coverage.md`; `functions/_shared/vertical-intelligence-method.md` | Blueprint fn 18 is one row; six verticals need six source sets |
| Redaction firewall exempts `system`-role content | L-0076 — the `full-name-like` regex false-positived on "Market Intelligence" and "South African" inside functions' own static prompts, structurally blocking every call those functions make | Escalated to the user as a security-policy ruling, not decided unilaterally |

### (b) Explicit governance / scope ruling by the operator

| Deviation | Evidence |
|---|---|
| Per-draft QA gating replaces aggregate gating | `weekly-content-loop.yaml` round-34 header: *"Restructured to the per-draft graph below on Pieter's explicit direction (chose the full per-draft rebuild over a smaller patch)"* |
| Weekly trigger temporarily made daily | `weekly-planning-trigger.bicep` L5: *"07:00 SAST recurrence on 6 Aug 2026 per Pieter's explicit instruction"* — but see DRIFT-4 |
| Newsletter ESP = Mailchimp, CRM-sourced | `services/publisher/app/esp_client.py` L10: *"per Pieter's ruling of 7 Aug"* |
| Bounded retry deliberately **not** built | `docs/content-learnings.md` L8–14: Proposals A + C now, Proposal B held ~a month — *"an LLM told 'you failed on X, try again' will often satisfy the check by deleting the offending claim rather than improving it."* A well-reasoned, direct refusal of the blueprint's agent-improvement loop as stated |
| **No client names, logos or co-branded material on any public surface, ever** | `docs/positioning.md` v2 (2 Sep 2026, uncommitted) L5 |
| Client-reference register is default-deny; nothing is CLEARED | `docs/permission-register.yaml` L14–18, L46–60 |

**The positioning-v2 ruling deserves its own paragraph.** The working-tree revision of
`docs/positioning.md` (+163/−66, dated 2 Sep, still uncommitted) states that *Centre of
Excellence as a Service is roughly 80% of revenue* and that the July brief *"under-weighted the
business… appeared once, as a bullet."* The blueprint of 22 July rests on the same
under-weighted picture: its go-to-market portfolio (L1061–1084) has no subscription-team pillar
at all. **A meaningful part of the design's commercial thesis is now known to be mis-weighted
at source.** And the permanent no-client-names rule directly narrows the blueprint's Client
Advocacy Harvester (26) and Case Study Writer (47), both of which assume a permission path to
naming clients. That is a design amendment, not a build gap.

### (c) Sequencing — correctly deferred, with a written owner

| Deferred item | Owner |
|---|---|
| Paid media (blueprint fns 78–81) | The blueprint's own exclusion, L1128–1129; `autonomy.yaml` sets `publish.paid_ad` to **level 0 — blocked outright** *"until a budget-control session ships"* |
| Video and motion (58–60) | Blueprint L1244: *"deliberately late-phase"* |
| Vault card persistence | `docs/brief-rollup-and-followups.md` §(a) — needs a frozen-contract amendment |
| Cross-brief citation checking | ibid. §(b) — named for a future measurement wave |
| Vector search | Blueprint L1122–1123 excludes it; the roadmap's "What NOT to build" reaches the same conclusion independently |
| Multi-tenancy | R14; `03-user-journeys.md`: *"No `tenant_id` anywhere in any schema"* |
| GA4 / Search Console / LinkedIn live credentials | `credentials-runbook.md` §10–12: *"Fixture-first is mandatory for this build"* |

**Not deferred — blocked.** Four register rows previously sat in this category and have been
moved out by the session audit: **51** (SEO and AI-Answer Content Optimizer), **61** (Landing
Page UX Designer), **77** (Landing Page CRO Agent) and **86** (Website Personalisation). Their
enabling session was specced and could not run (§8.2). The rule I applied: a row is *Blocked*
rather than *Deferred* when its whole function is manipulation of a surface the platform cannot
currently reach. Function **42** (Long-form Article Writer) stays Deferred on that rule — its
output is a draft, which the Vault can hold without a CMS.

### (d) Genuine drift — nobody decided this

| id | Drift | Evidence | Why it is drift, not a decision |
|---|---|---|---|
| **DRIFT-1** | **The build was never given the blueprint** | `git ls-files` — the docx is untracked; `autonomy.yaml` L15 states no autonomy blueprint exists in the repo | Root cause of DRIFT-2 and DRIFT-3. No one chose to ignore the design; it was never available to ignore. |
| **DRIFT-2** | Autonomy levels 0–4 mean the opposite of the design at the bottom of the ladder | §3.2 | Independently re-derived under an explicit "no blueprint exists" note. Two live documents now define "level 2" incompatibly, and the number is shown to a human on the approval card. |
| **DRIFT-3** | Three package ids do not match the blueprint register | `02-brand-steward-qa` (blueprint 2 = Strategy and Prioritisation; Brand Steward is **3**); `42-linkedin-post-writer` (blueprint 42 = Long-form Article Writer; LinkedIn Post Writer is **44**); `48-fact-check-verdict` (blueprint 48 = White Paper Writer; Fact Checker is **53**). The other 21 match exactly. | An accidental renumbering that will silently collide the moment the real fns 2, 42, 48 or 53 are built. Cheap now, expensive later. |
| **DRIFT-4** | The "TEMPORARY (6 Aug 2026)" daily weekly-loop trigger is still daily on 4 Sep | `weekly-planning-trigger.bicep` L60–72 | The ruling in (b) was for a *daily-review iteration*. Four weeks on, the full 26-task content studio fires 7× a week, burning ~6 LLM drafts and 12 QA verdicts a day with nowhere to publish. Nobody re-decided this; the note outlived its reason. |
| **DRIFT-5** | `run-the-loop.md` step 4 instructs a click no template renders | `run-the-loop.md` step 4 vs `approvals.html` L5 | The operator runbook — written explicitly for *"a new, unaided operator"* — is wrong on its single most important step. |
| **DRIFT-6** | Console hardcoded to `mock` in Bicep, with fixtures injected at deploy | `console-app.bicep` L174/L182 (string literals, not parameters); `deploy-console.yml` L583 | TD-10 records it as debt, but nothing records a *decision* to hardcode rather than parameterise. A parameterised default flips in one line; a literal needs a Bicep edit and a full infra deploy. |
| **DRIFT-7** | `TEAMS_WEBHOOK_URL` is wired to Gatekeeper only, never to the orchestrator | `grep -rn TEAMS_WEBHOOK_URL infra/` → one hit, `gatekeeper-app.bicep` L171 | `teams_notify.py` L9–16 promises the brief card *"activates the instant a real `TEAMS_WEBHOOK_URL` env var appears — a config flag flip, never a code change."* On `ca-orchestrator` that variable can never appear from IaC. The promise is false as deployed. |
| **DRIFT-8** | `DAILY_LOOP_BUDGET_USD` is documented as an enforced budget and read only by a test | `config.py` L92; `run-the-loop.md` L317–321; sole consumer is `tests/e2e/test_cost_metering_below_budget.py` | A cost cap that exists in prose and in CI, but not in the runtime. |
| **DRIFT-9** | fn 48 gates real publishing with 0 evals and no `tools.yaml` | `ls functions/48-fact-check-verdict/` → `prompt.md`, `schema.json`, `skill.md` only | Every other package has ≥5 evals. It escapes `validate_package.py` only because `registry.yml` hardcodes three paths. Its own `skill.md` asks for a review that has not happened. |
| **DRIFT-10** | The architecture doc set has itself gone stale — while being the repo's front door | `README.md` sends new readers to `docs/architecture/`; `03-user-journeys.md` L157 says *"none of these 15 task_types is in `DISPATCH_TABLE`"* (false since 10 Aug); `12-integration-catalogue.md` I15/I16 say GA4/GSC are *"fixture only"* (live paths added 7 Aug, commit `300aed7`); TD-17's "`dispatch.py` is 1,138 lines" is now 2,280 | Written 6 Aug, superseded by 7–10 Aug work, never re-verified. |
| **DRIFT-11** | `MCP_WEB_LIVE_MODE` is set on the live app by hand and declared nowhere | TD-31 plus `19-live-verification-log.md` P3, which confirms the mechanism against Microsoft's own ARM documentation | The next full infra deploy silently reverts all knowledge intake to a synthetic fixture, the loop stays green, and `caj-mcp-smoke` *passes* in the broken state. The highest-severity drift item in the repo. |
| **DRIFT-12** | Five uncommitted `F-*.diff` files and a `_to_delete/` directory in the repo root | `git status`; `F-GOOGLE-LIVE-CLIENTS.diff` (39 KB), `F-NEWSLETTER-ESP.diff` (39 KB), two `F-TEAMS-WEBHOOK-SMOKE*.diff`, `_to_delete/*.tar.gz` | Working-tree residue from the 7–10 Aug sessions. Harmless individually; collectively they make `git status` unreadable, which is how a superseding positioning revision came to sit uncommitted for two days. |
| **DRIFT-13** | `session/s13-website` has been blocked on an expired OAuth token for a month | `cmos-session-s13-website/.loop/` holds only `research.md` + `spec.json`; this audit session's own startup reported `canvas-wordpress (AUTH_HEADER_REJECTED) … Token validation failed: Expired token` | The block is real and was correctly diagnosed on 3 Aug. What drifted is that it was recorded **only inside an unmerged worktree's `.loop/research.md`** — no owner, no ticket, no mention in any file under `docs/`. An entire designed channel has been dark for a month and nothing in the repo says so. See §8.2. |
| **DRIFT-14** | The blueprint's named agent runtime — **Claude Agent SDK** — is not used anywhere | `grep -rniE "claude[-_ ]agent[-_ ]sdk\|import anthropic"` over `services/ mcp/ functions/ console/` returns nothing; `services/model-gateway/providers/anthropic.py` calls `https://api.anthropic.com/v1/messages` directly over httpx, and no service lists the `anthropic` package in any `requirements*.txt` or `pyproject.toml` | The blueprint specifies the Agent SDK as the runtime — *"Production agents in Python/TypeScript with tool use, state and controlled loops"* (L1147–1149). The build replaced it with a completion proxy plus a deterministic DAG. That may well be the better architecture — it matches the blueprint's own *"deterministic workflow for routing and publishing; agents for judgment"* (L32) — but **the choice was never weighed against the design.** The one written rationale (`anthropic.py` L1–5) argues httpx-over-SDK for *one endpoint's transport*; it does not address the runtime question at all. |
| **DRIFT-15** | The blueprint's named workflow layer — **n8n Community Edition or Power Automate** — is not used either | `grep -rniE "n8n\|power automate"` over the repo returns **zero** n8n hits, and Power Automate only as the *receiver* of the Teams webhook (`teams_client.py` L5, `gatekeeper-app.bicep` L60) | The blueprint selected this layer deliberately, and explicitly *excluded* Make and Zapier to get there (L1120–1121). The build uses Azure Logic Apps + Service Bus + a custom orchestrator instead. Again: defensible, arguably better inside an Azure estate — and again, no decision record. Two of the blueprint's thirteen stack rows were replaced without anyone noting that they had been. |

---

## 6. What the build discovered that the design missed

Six subsystems exist in code with no counterpart anywhere in the blueprint. Each is a
candidate amendment.

**0. The spec-driven session loop itself.** Before any of the five below, the largest missing
piece: the repo is built by a formal, governed process the design never describes. Thirteen
sessions, each in its own worktree, each carrying a `.loop/` with a numbered `spec.json`
(acceptance criteria, `locked_decisions`, `out_of_scope`, `amendments`, a stated confidence),
a `plan.md`, per-lens adversarial reviews (`lens-risk-security`, `lens-migration`,
`lens-performance`, `lens-agent-native`, `lens-data-residency`), a `review.json` and a
`test-report.json`. Across thirteen specs that is **418 numbered acceptance criteria and 101
explicit out-of-scope declarations, written before the work started** (§8.1). The blueprint's
function 112, Test and Evaluation Harness Engineer, describes testing *the agents*. Nothing in
the design describes governing *the build* — yet this process is the reason category (c) in §5
is as large and as clean as it is.

**1. The compound learning system (`.compound/`).** 79 accepted learnings across four classes
— architecture, conventions, known-hard, security — each a durable statement with a status, a
strengthening history, and `[[wiki-links]]` between them. The blueprint's agent-improvement
loop (L146–147, L993–1000) concerns *agent output*. This system concerns *the act of building*,
and it is the more mature of the two. It has caught, among others: `az containerapp job start
--yaml` silently ignoring overrides (L-0023), a Container App identity that cannot be attached
and referenced in one create call (L-0049), and a routing table whose Anthropic model ids had
all been retired while every mocked test passed (L-0026).

> **Amendment:** the blueprint needs a **tenth function family — build-loop learning**,
> distinct from function 102 (Prompt and Process Improvement Coach), which improves prompts.
> Nothing in the design captures learning about the platform's own construction.

**2. A "the tooling is the prime suspect" failure class.** L-0027, L-0064 and L-0073 record it
explicitly: *"Seventh instance on this console build of a live 'failure' tracing to tooling,
not the system under test."* L-0076 is the sharpest — after four independently verified fixes
to the same function, the unchanged symptom "1/7 stages terminal" was itself the signal,
because the poll script's own `TERMINAL_STATES = {"completed","failed"}` meant "1 of 7
resolved", not "stage 1 keeps failing." The blueprint's failure taxonomy (L996) lists nine
classes — wrong objective, missing context, weak source, reasoning, tool, format, policy,
coordination, measurement — and **has no class for "the diagnostic instrument is wrong."**

**3. Bootstrap contracts as first-class design objects.** L-0048 / L-0049 / L-0060 / L-0061 /
L-0071 codify a four-part contract for any Container App fed by CI-built images, escalated to a
STANDING CONTRACT after independently reproducing on the fourth new app. L-0062 does the same
for runtime contract-file resolution. The blueprint's Agent Platform Architect (fn 103) names
"deployment model" as a responsibility and specifies nothing about it.

**4. Governance is the product; the marketing engine is the demo.** The build reaches this
independently (`10-product-roadmap.md` Branch B, R13 — extract the governance SDK, marked ★).
The five-layer authorisation chain — autonomy policy → human approval → signed gate token →
content-hash boundary binding → replay ledger — is domain-neutral and, per V1, **has never
carried a real post.** The blueprint frames governance as a constraint on the marketing engine.
The build suggests the ordering is backwards.

**5. Counterparty verification as a documentation discipline.**
`19-live-verification-log.md` checks the platform's claims *from the external system's side*,
and produced the two most valuable findings in the repo: **V1** (the governed pipeline and the
real marketing workflow are disjoint) and **V2** (fn 02's `link-shortener` rule would fail
**86 of 100** real published posts — 85 contain `bit.ly`, and Buffer's own shortener `buff.ly`
appears zero times, so it is a deliberate systematic choice, not a tooling accident). The
blueprint's Agent Evaluator (101) scores outputs against rubrics. It never asks whether the
rubric matches reality.

> **Amendment:** before the platform enters the publishing path, run `safety_suite.py` over an
> export of real published posts as a one-off brand-rule calibration. It needs no new code and
> would have surfaced all four V2 divergences — including that fn 42's mandated roof line
> `Your Data. Delivered.` reads `Your data. Delivered.` in all six real occurrences.

---

## 7. Top 12 recommendations

Ranked by user-journey impact per unit effort.

| # | Recommendation | Effort | Why here |
|---|---|---|---|
| 1 | **Declare `MCP_WEB_LIVE_MODE` in `main.bicep`**, and reject any `fetch_url` body matching `^SYNTHETIC-TEST-DATA` in `ingest_signals_handler` | ~1 hour | DRIFT-11. Today one routine infra deploy turns every morning brief into hallucination over a placeholder — silently, with the loop green. Highest severity, lowest cost. |
| 2 | **Give the operator a brief to read and a button to click** — G2 + G4 together: approve/reject links on `/approvals`, and a `/brief/latest` page | small PR | The two changes that convert "a system that runs" into "a system someone uses." Both additive; neither touches a trust boundary. |
| 3 | **Fix `run-the-loop.md` step 4** | doc-only | DRIFT-5. The runbook is wrong on its most important step, for exactly the reader least able to detect it. |
| 4 | **Re-authorize the `canvas-wordpress` connector (`/mcp`), then resume s13** | config to unblock; session-sized for the build | DRIFT-13. The cheapest action in this audit that opens an entire designed channel. s13's spec, research and locked decisions are already written, so the session *resumes* rather than restarts — and until it does, the platform can measure the website but cannot touch it. |
| 5 | **Revert the weekly trigger to Monday** | config | DRIFT-4. Removes ~6 wasted LLM drafts and 12 QA verdicts per day and restores the designed Mon–Fri rhythm. |
| 6 | **Add a CI test that every loop `task_type` is dispatched or explicitly allowlisted**, and make `legacy_task_pass_through` log a WARNING | small PR | G8. The repo's own testing-gaps table names this hole; it is what let 17 silent no-ops ship green. |
| 7 | **Calibrate fn 02's brand rules against real published output** before the platform enters the publishing path | small PR | V2. 86% of real posts would fail one rule. Either the rule or the practice must change — and only Pieter can choose, but nobody can choose until the number is on the table. |
| 8 | **Reconcile the autonomy vocabulary** — keep the built mechanism, adopt one set of level names, amend the blueprint | small PR | DRIFT-2. Two live definitions of "level 2", one of which is rendered to a human on the approval card. |
| 9 | **Give fn 48 five evals and Pieter's review; auto-discover packages in `registry.yml`** | small PR | DRIFT-9 + TD-18. An unreviewed, unevaluated function currently holds a publish gate. |
| 10 | **Registry-driven generic dispatch (R1)** | session-sized | G7. The single change that turns 12 shipped-but-inert intelligence packages into a morning brief with real competitor and vertical content. Highest ceiling; correctly sequenced after 1–8. |
| 11 | **Commit the positioning v2 revision and amend the blueprint's commercial thesis** | config + a strategy session | The design's go-to-market portfolio has no CoEaaS pillar, and CoEaaS is ~80% of revenue. Every drafting prompt reads `docs/positioning.md`; leaving v2 uncommitted means every draft this week is written against the superseded July brief. |
| 12 | **Record the two skipped stack choices** — Claude Agent SDK and n8n/Power Automate — as decisions, in `docs/accepted-risks.md` or `.compound/` | doc-only | DRIFT-14 + DRIFT-15. Both replacements are probably right. Neither is written down anywhere, so the next person to read the blueprint will re-open both questions from scratch. |

---

---

## 8. Session process audit — s0 to s13

*Added 4 September 2026. The original audit treated the build as a single artefact and
attributed deviations to "waves" loosely. It did not enumerate the sessions that actually
produced the repo. This section closes that gap, and it changes one classification in §5.*

### 8.1 The session inventory

The repo is built by a spec-driven agent loop, one session per `git worktree` on a
`session/{id}` branch (`README.md`, "Development workflow: worktree-per-session"). Fourteen
session branches exist; **thirteen ran, twelve delivered.**

| Session | Delivered | Landed | Evidence |
|---|---|---|---|
| `s0-foundation` | Contracts, infra, CI/CD scaffold | PR #1, #2 | 4 later commits never merged — see 8.3 |
| `s1-gateway` | `services/model-gateway` | PR #5 | |
| `s2-vault` | `services/vault` | PR #12 | |
| `s3-orchestrator` | `services/orchestrator` | PR #7 | |
| `s4-governance` | `services/gatekeeper`, `services/publisher` | PR #4 | |
| `s5-mcp` | `mcp-web`, `mcp-buffer`, `mcp-canva` | PR #25 | |
| `s6-registry` | `services/registry`, eval harness | PR #6 | |
| `s7-console` | `console/`, `services/telemetry-lib` | PR #26 | |
| `s8-first-loop` | Dispatch table, 5 handlers, proof circuit, `run-the-loop.md`, `tests/e2e` | PR #45 | 34 pre-squash commits |
| `s9-analytics` | `services/analytics-ingest`, 4 KPI rollups, Fabric export | merged | |
| `s10-intelligence` | 12 intelligence function packages + the daily fan-out graph | PR #28, #31 | **the packages that are inert** |
| `s11-content` | 9 content functions + `weekly-content-loop.yaml` | PR #33 | |
| **`s12`** | — | — | **No branch, no worktree, no reference anywhere.** The numbering skips it. |
| **`s13-website`** | **Nothing** | — | **See 8.2. The branch has zero commits of its own.** |

Every session s1&ndash;s11 carries a complete process record in its worktree's `.loop/`:
`spec.json` (numbered acceptance criteria, `locked_decisions`, `out_of_scope`, `amendments`,
a stated confidence), `plan.md`, `review.json`, `test-report.json`, and for most, per-lens
review files (`lens-risk-security.json`, `lens-migration.json`, `lens-performance.json`,
`lens-agent-native.json`, `lens-data-residency.json`) and `learnings-candidates.json`.

| Session | spec version | Acceptance criteria | Out-of-scope items | Amendments | Stated confidence |
|---|---:|---:|---:|---:|---:|
| s0 | 5 | 40 | 6 | 4 | 0.86 |
| s1 | 3 | 35 | 10 | 1 | 0.79 |
| s2 | 3 | 25 | 7 | 0 | 0.82 |
| s3 | 5 | 36 | 7 | 0 | 0.88 |
| s4 | 3 | 37 | 8 | 0 | 0.80 |
| s5 | 2 | 23 | 5 | 1 | 0.75 |
| s6 | 2 | 27 | 8 | 0 | 0.80 |
| s7 | 6 | 36 | 7 | 0 | 0.80 |
| s8 | 4 | 32 | 9 | 0 | 0.87 |
| s9 | 2 | 35 | 8 | 0 | 0.85 |
| s10 | 4 | 42 | 7 | 3 | 0.81 |
| s11 | 6 | 30 | 6 | 5 | 0.82 |
| **s13** | **1** | **20** | **13** | **0** | **0.74** |

That is 418 numbered acceptance criteria and 101 explicit out-of-scope declarations across
thirteen specs. The out-of-scope column is the single best evidence for §5's category (c):
deferrals in this build were nearly always *written down before the work started*, not
discovered afterwards.

### 8.2 s13-website — specced, blocked, never built

`session/s13-website` is the only session that produced no code. Its worktree
(`C:/Users/rooip/cmos-session-s13-website`) holds a `.loop/` containing **only `research.md`
and `spec.json`** — no `plan.md`, no `review.json`, no `test-report.json`. The branch tip is
`fa91419`, a commit that belongs to `main`; the session added nothing of its own.

`spec.json` is complete and serious: 20 acceptance criteria, 13 out-of-scope items, three
`locked_decisions`, confidence 0.74. It was written and then the session stopped. Its own
research pass records exactly why:

> "The only WordPress-capable tool surfaced in this session is
> `mcp__claude_ai_canvas_wordpress__*` &hellip; Calling `authenticate` returned: *'This is a
> claude.ai MCP connector. Ask the user to run /mcp and select "claude.ai canvas wordpress"
> to authenticate.'* — this tool cannot complete an OAuth flow on its own in this
> environment; only the user, via `/mcp`, can authorize it."
> — `cmos-session-s13-website/.loop/research.md` R-2

Its `LD-3` then froze that finding into the spec rather than deferring it: AC-02 (the
information-architecture proposal) was kept in scope with no WordPress dependency; AC-01,
AC-07 and AC-11/12/13, plus all draft creation, were declared blocked pending the user
completing `/mcp` authorization. That is L-0001 applied correctly — the session refused to
mark work done on the strength of the goal's assertion that site access "is verified working
as of 2 Aug", on the grounds that *sessions do not inherit OAuth grants*.

**The block is still in force one month later.** This audit session's own startup reported:

```
canvas-wordpress (AUTH_HEADER_REJECTED): "Server rejected the configured Authorization
header (HTTP 403) ... Token validation failed: Expired token"
```

**Why this matters more than a skipped session.** The blueprint makes the website a primary
channel — *"LinkedIn, website/search, email, webinars/events, partner channels, targeted paid
media"* (L26) — and four separate register functions depend on it: Long-form Article Writer
(42), SEO and AI-Answer Content Optimizer (51), Landing Page UX Designer (61), Landing Page
CRO Agent (77), plus Website Personalisation (86) and the "Vertical landing page" experiment
(L1107&ndash;1108).

None of it exists. A repo-wide search for website or WordPress artefacts returns **nothing but
GA4 measurement dimensions** — `landingPagePlusQueryString`, `landing_page`
(`analytics_ingest/ga4_client.py` L56&ndash;177). `docs/website-content/` and
`docs/website-runbook.md`, the two deliverables s13's `LD-1` committed to, do not exist.

> **The platform can measure the website and cannot touch it.** That asymmetry is not in the
> blueprint, and the original audit missed it by counting family 6 as merely "unstarted."

### 8.3 s0-foundation's four orphaned commits — the origin of DRIFT-3

`session/s0-foundation` is four commits ahead of `main`, and those commits are where the
package-numbering drift in §5 (DRIFT-3) actually began:

```
f61e16d  Merge remote-tracking branch 'origin/main' into session/s0-foundation
bb93ef6  Record L-0015: diagnose against live/deployed state, not just logs
abb6c11  Add positioning brief and two marketing function packages
084f0ed  Fix caj-vault-migrate: base64-encode schema.sql to survive Container Apps
```

`abb6c11` created `functions/042/` and `functions/brand-steward-qa/` — a zero-padded numeric
id and a bare slug, two different naming conventions in one commit. Neither directory exists
on `main`; both were superseded by `functions/42-linkedin-post-writer` and
`functions/02-brand-steward-qa`. The renaming resolved the *convention* and silently locked in
the *wrong numbers*: the blueprint's fn 42 is the Long-form Article Writer and its LinkedIn
Post Writer is fn 44. `functions/042/` was never checked against a register nobody had.

The other two commits' content did land by another route (`infra/modules/migration-job.bicep`
and L-0015 are both on `main`), so the only genuinely orphaned artefacts are the two
superseded package drafts. Harmless in themselves — but they are the fingerprint of DRIFT-1.

### 8.4 Branch hygiene

`git branch` lists **44 unmerged local branches** beyond the session branches, almost all
single-commit `fix/*` branches whose content squash-landed on `main` long ago
(`fix/orchestrator-campaign-taxonomy-uuid`, `fix/scheduling-trigger-schema-v3`, and so on),
plus `session/teams-webhook-wiring`, whose two commits are fully contained in `main` today.
None of this is orphaned work — I checked the significant ones directly — but it is the same
hygiene problem as DRIFT-12 at a larger scale, and it makes "is there unmerged work?" an
expensive question to answer rather than a cheap one.

One branch is not noise: **`docs/positioning-v2` is 179 commits ahead of `main`.** That is the
positioning revision already flagged in §5(b) as uncommitted in the working tree — it also has
a branch of its own that has never been merged.

### 8.5 What this changed elsewhere in the audit — applied, not just noted

The session audit forced a full recount of §1. All of the following are now applied in the
sections named; this table is the audit trail, not a to-do list.

| Change | Applied in |
|---|---|
| Register rows **51, 61, 77, 86** moved from *Deferred* to a new **Blocked** state — a session was specced to start them and could not run. Function 42 stays Deferred; the rule is stated in §5(c). | §1 scorecard, §5(c) |
| **DRIFT-13** added — s13 blocked for a month, recorded only inside an unmerged worktree. | §5(d) |
| Recounting the integrations row against the blueprint's own stack table rather than the repo's catalogue surfaced **DRIFT-14** (Claude Agent SDK never used) and **DRIFT-15** (n8n / Power Automate never used). Neither was visible before, because a catalogue of what was built cannot show what was designed and skipped. | §1, §5(d) |
| Two arithmetic faults in the original scorecard corrected: a double-counted function-register row, and *packages* counted against a column of *blueprint rows*. Designed total 183 → **178**; columns are now mutually exclusive and every row sums. | §1 |
| The spec-driven session loop added as discovery **0** — the largest thing the build has that the design never describes. | §6 |
| Two recommendations added: re-authorize the connector and resume s13 (rank 4); record the two skipped stack choices as decisions (rank 12). | §7 |

### 8.6 The one action this section adds

**Re-authorize the `canvas-wordpress` MCP connector (`/mcp`), then resume s13.** Effort:
**config** for the authorization; **session-sized** for the build it unblocks. It is the
cheapest action in this audit that opens an entire designed channel — and s13's spec, research
and locked decisions are already written and waiting, so the session resumes rather than
restarts. It now sits at rank 4 in §7.

---

## 9. Live verification — 4 September 2026

*Added after the fact. The appendix below states that live Azure state could not be
inspected. **That was never attempted, and it was wrong.** Read-only `az` works from the
session that wrote this audit; the limitation was assumed, not tested. What follows replaces
the appendix's first bullet.*

**Method:** read-only `az` against resource group `cmos-dev`, subscription *Canvas Internal
Admin*, as `pvz@canvasintelligence.com`. `show` / `list` / `query` and Log Analytics only. No
writes, no deployments, no job starts, and no `keyvault secret show` — secret *names* and
resolution state answer every question here; values answer none.

### 9.1 Three drift items close on live evidence

| Finding | Live state | Verdict |
|---|---|---|
| **DRIFT-11** — `MCP_WEB_LIVE_MODE` set by hand and declared nowhere; called *"the single most consequential open question in this documentation set"* | `MCP_WEB_LIVE_MODE = "True"` on the live `mcp-web` revision, **and** now declared in `main.bicep`. `MCP_WEB_ALLOWLIST` has grown to seven domains. Knowledge intake is live, and a redeploy can no longer silently revert it. | **Closed** |
| **DRIFT-7** — Teams webhook wired to Gatekeeper only, so the morning-brief card could never fire | `TEAMS_WEBHOOK_URL` is a `secretRef` to `teams-webhook-url` on **both** `ca-gatekeeper` and `ca-orchestrator`. Both apps are `Running`, and a Container App with an unresolvable Key Vault `secretRef` does not start — so the secret exists and resolves. | **Closed** |
| **DRIFT-6** — console hardcoded to `mock`, every governance screen a fixture | `VAULT_API_MODE = real`, `GATEKEEPER_API_MODE = real` on the live revision. | **Closed** |

Also holding: **L-0065**'s recurring silent reset of `ca-orchestrator`'s FQDN environment
variables has not recurred. All seven are present and correct, including two added since this
audit was written (`CMOS_MCP_CANVA_BASE_URL`, `CMOS_CONSOLE_BASE_URL`).

### 9.2 Two findings confirmed open, now on evidence rather than inference

- **DRIFT-4** — `la-weekly-planning-trigger` has fired at 05:00 UTC (07:00 SAST) on **every
  single day** through 4 September. The "TEMPORARY (6 Aug 2026)" note is five weeks old.
- **Nothing has published.** `PUBLISHER_DRY_RUN` is absent from `ca-publisher`'s live
  environment, so the code default `true` governs. `la-publish-trigger` nonetheless fires
  **hourly**, sweeping for approved assets it can only ever dry-run.
- **Unlock C stays closed.** `mcp-canva` carries `CANVA_CLIENT_ID` and `CANVA_CLIENT_SECRET`
  only — no `canva-refresh-token`.

### 9.3 The finding that outranks everything else in this document

All five Logic Apps are Enabled and firing on schedule. That is where the good news stops.

**Over three days: 493 tasks dispatched, 408 cascade dead-lettered.**

The cause is a single event, repeated: `scan_profile_not_configured` — 23 occurrences each for
nine scanners, 7 each for two more. **Eleven of the twelve scan profiles carry no source
URLs.** Each fails on every run, and the failure cascades through `dedupe-signal-cards` →
`competitive-response-strategize` → `morning-brief-rollup` → `executive-brief-rollup`.

`brief_published` fired on **two of the last seven days**.

This audit's §2 reported that the intelligence fan-out was *declared but never dispatched*.
That has been fixed — every scanner now has a handler. What replaced it is worse in one
specific sense and better in another: the tasks now genuinely run, genuinely fail, and
genuinely dead-letter, where before they silently passed. The failure is at last **visible** —
which is exactly what `test_every_loop_task_type_has_a_handler` and the dead-letter alerting
were built to achieve. Nobody has acted on it yet.

And `la-source-discovery-trigger` — the loop built precisely to find and promote scan sources —
is Enabled but has logged **zero `propose-sources` or `probe-sources` events in seven days**.
The remedy is wired and not running.

Supporting signal from the same window: `ingest_source_below_content_floor` ×138,
`qa_review_blocked` 1–6 per day, `qa_review_false_positive_dropped` ×34.

### 9.4 What remains genuinely unverifiable

- **Key Vault's data plane refuses this account**: *"Public network access is disabled and
  request is not from a trusted service nor via an approved private link."* That is L-0012's
  documented posture working exactly as designed. The full secret inventory therefore stays
  inferred from which apps resolve which `secretRef` and stay `Running`.
- **Postgres is private.** `governance.publish_attempts` row counts and a true first-pass
  acceptance rate need `caj-vault-query`, which is a job *start* rather than a read. Not run
  here — it is the obvious next step, and it would produce the acceptance-rate baseline the
  delivery plan's first wave is built to create.

## Appendix — what I could not verify

- ~~**Live Azure state.** No `az` access from this session.~~ **Superseded by §9** — `az` access was available and never tested. The bullet below stood uncorrected until 4 September. Every deployment claim rests on
  Bicep, workflow definitions, and comments recording live verification. In particular, whether
  `MCP_WEB_LIVE_MODE` is set on `ca-mcp-web` **right now** is unverified — and it is the most
  consequential open question in the repo.
- **Whether either loop succeeded on any date after 10 August 2026.**
  `docs/content-learnings.md` ends at round 34 (10 Aug); the git history ends the same day.
  Three and a half weeks of production behaviour are unrecorded.
- **Whether Canva brand templates exist** (P5). Function 45's carousel path and
  `bulk_create_from_csv` are template-locked — `template_id` is required. No templates would be
  a hard blocker on the weekly loop that nothing in the repo records.
- **Whether `fetch_sources.yaml`'s four URLs still resolve** (P7). The file's own header asks
  for periodic re-verification; there is no record of one having happened.
- **Any minutes-per-day or click-count target.** Not in the blueprint. Recorded as unverified
  rather than estimated.
