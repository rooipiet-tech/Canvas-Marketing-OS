# Remediation Backlog

**Source:** `docs/architecture/Comprehensive-System-Architecture-and-Process-Map.md` (revision 2.1)
**Verified against:** `main` @ `53a2560`, 17 August 2026
**Status:** proposed — **none of this has been implemented.** No branch carries any of it.

Every item below is a finding that survived re-verification against the current tree. Each is scoped to be a **separate branch and a separate PR**. They are deliberately ordered so that the two that unblock others come first.

---

## How to use this list

Each item is self-contained: it names the finding it closes, the files it touches, what "done" looks like, and what it must not break. Pick one, branch from `main`, and use the prompt at the end of this file.

**Do not batch them.** Several touch `services/orchestrator/orchestrator/dispatch.py`, which is 7,068 lines and the highest-change-rate file in the repo. Two branches editing it concurrently will conflict badly. If you run these in parallel, run **C1 last and alone**.

| Wave | Items | Can run concurrently? |
|---|---|---|
| **1** | A1, A2, A3 | Yes — disjoint files |
| **2** | B1, B2 | Yes, after wave 1 |
| **3** | C1 | **Alone.** It rewrites the file half of the others touch |

---

## Wave 1 — independent, no dependencies

### A1 · Tag published posts so measurement can attribute them
**Closes:** the last unpopulated join key (§14.0, "the one attribution gap that survived") · **Size:** S · **Risk:** low

`analytics.post_archetype` has no writer anywhere. It is read by `_render_month_end_report` and by `db.py`'s engagement query, so the headline KPI — *engagement rate by post archetype* — groups on a column nothing fills. `scheduled_posts` and `utm_campaign_map` were closed upstream; this is the third and last.

Root cause: `services/publisher/app/buffer_client.py`'s `create_draft` sends exactly `channel_id` and `text`.

**The constraint that shapes this work.** AC-09 is a real safety invariant: `create_draft` must never accept a status/mode/state argument, and `mcp-buffer` is pytest-guarded against any tool name or description matching `publish|share.?now|send.?now|go.?live`. **Add attribution metadata without widening the publish surface.** Both new fields are opaque labels; neither can transition a post's state.

| File | Change |
|---|---|
| `mcp/mcp-buffer/tools.yaml`, `mcp/mcp-buffer/app/main.py` | Add optional `utm_campaign` and `post_archetype` to `create_draft`'s `inputSchema` |
| `mcp/mcp-buffer/app/dispatch.py` | Forward both into the GraphQL mutation as tags. Status stays hardcoded server-side |
| `services/publisher/app/buffer_client.py` | Widen `create_draft` to pass them. Its docstring's "ONLY 2 arguments" claim becomes "no status/mode/state argument" — the invariant that actually matters |
| `services/publisher/app/routers/publish.py` | Resolve both from the `asset_id` Vault lookup that already runs: `assets.asset_type` → archetype; campaign from the loop's `result_ref` |
| `services/analytics-ingest/analytics_ingest/buffer_client.py` | Add `archetype` and `utmCampaign` to `ASSUMED_METRIC_FIELDS` so `buffer_introspect.py` actually verifies them |

**Done when:** a published asset's archetype round-trips — publish → ingest fixture → `kpi_rollup_engagement_by_archetype` produces a non-null group.

**Must not break:** the AC-09 test that `create_draft` rejects status/mode/state; `pytest -m mcp_buffer_surface`.

**Expect this to surface a second problem.** Adding the two fields to `ASSUMED_METRIC_FIELDS` makes `buffer_introspect.py` verify them against Buffer's live schema, and `archetype` may not exist there. That is the check doing its job — better to learn it at deploy time than to ship a KPI that groups on NULL forever. If it does not exist, carry the archetype in `utm_content` or a campaign-slug convention instead, and say so in the module docstring.

---

### A2 · Alert on the failures that currently go unnoticed
**Closes:** F6, O2, B5 (they compound) · **Size:** M · **Risk:** low

`infra/` contains **no `metricAlerts`, no `scheduledQueryRules`, no action groups.** Nothing pages on anything. Three specific holes:

- **F6** — a dead-lettered task emits a `DeadLetterAlert` onto the `event` queue; the worker logs `dead_letter_alert_received` and explicitly does nothing with it. No consumer, no alert.
- **B5** — the worker is a single `asyncio.Task` inside the FastAPI process. If its startup raised, `worker_task = None`, `worker_loop_start_failed` is logged at WARNING, and `/health` still returns 200. **The system stalls completely while looking healthy.**
- **O1** — a missing `TEAMS_WEBHOOK_URL`, `DATABASE_URL` or App Insights connection string all log and continue, so config-absent is indistinguishable from config-broken.

**Do:**
1. Add a `GET /readiness` to `services/orchestrator/main.py`, distinct from `/health`: report worker-task liveness, DB reachability, and each expected-but-absent integration. `/health` stays a dumb liveness probe.
2. Introduce explicit expectation env vars (e.g. `CMOS_EXPECT_TEAMS=true`) so absence becomes an error rather than a silent default.
3. Add alert rules **in Bicep** — dead-letter rate, QA-block rate, budget hard breaches, and loop-completion age (nothing completed in N hours).

**Done when:** killing the worker turns `/readiness` red while `/health` stays green, and a dead-letter fires an alert rule defined in IaC.

**Note:** alerts may exist portal-side today. If so they are invisible to anyone reading the repo, which §14.7 O2 treats as a finding in its own right — bring them into Bicep either way.

---

### A3 · Resolve the two dormant components
**Closes:** F9, F11 · **Size:** S · **Risk:** low, but **needs a decision first**

**F9 — `mcp-canva` is deployed and invoked by nothing.** A live Container App with its own managed identity, Key Vault and ACR role assignments, and two Canva OAuth secrets (`CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`), built and shipped by `deploy-mcp.yml`, with zero callers outside `mcp/`. Function 45 still emits a `canva_bulk_create_csv` manifest into every carousel asset that no handler consumes. This is standing compute cost plus a live third-party credential on a service with no consumer.

**Ask the owner to pick before writing code:**
- **Wire it** — have the carousel handler call `bulk_create_from_csv` with function 45's manifest, so the asset it already produces is used; or
- **Decommission it** — remove `idMcpCanva`, `mcpCanvaKvRole`, `mcpCanvaAcrRole`, `mcpCanvaApp` from `infra/main.bicep`, drop it from `deploy-mcp.yml`, and **revoke the Canva credentials**.

Leaving it running is the only option with cost and risk but no benefit.

**F11 — delete `functions/task-worker/`.** 23 lines, one health route, confirmed neither deployed nor built (no reference in `infra/` or any workflow). Its docstring says the real consumer "is implemented in a later wave" — it was, in the orchestrator. Deletion is risk-free; keeping it costs every reader the time to work out that the Azure Functions tier it implies does not exist.

---

## Wave 2 — after wave 1

### B1 · Decide the Buffer queue-cap posture before publishing goes live
**Closes:** F13 · **Size:** S (code) but **decision-led** · **Risk:** medium if ignored

Each content cycle requests 4 Buffer posts (`friday-schedule-social-buffer-*` × 4). At the daily cadence that is ~28 queued posts a week against a **free-tier cap of 10**, enforced by a live `list_queue` count in `services/publisher/app/config.py`.

Masked today: `PUBLISHER_DRY_RUN` defaults true and is set nowhere in infra, so nothing is queued. It fails safe when it bites — a `buffer_queue_cap_exceeded` refusal row, not a crash — but it will start refusing silently around day 3 of live publishing.

**Options, for the owner:** a paid Buffer tier; fewer posts per cycle; or accept the cap as a deliberate throttle. Whichever is chosen, **record it in `config.py` next to `BUFFER_FREE_TIER_QUEUE_CAP`** so the next reader does not rediscover it live. Depends on A1 only in that both touch the Buffer path — sequence them.

---

### B2 · Get the fact-check prompt reviewed, or gate it off
**Closes:** F12 · **Size:** S · **Risk:** low mechanically, **high in consequence**

`functions/48-fact-check-verdict/prompt.md` opens with:

> **FIRST DRAFT — 6 Aug 2026. Not yet reviewed or approved by Pieter as settled QA policy.**

That prompt gates whether real content reaches Buffer and the newsletter. An over-strict verdict blocks good content; an under-strict one lets a fabricated number reach a client's inbox. It has been in the production critical path since 6 August.

**Do:** have the owner read it and either sign it off — deleting the banner and dating the approval — or set the Thursday fact-check tasks aside until they have. **Do not quietly delete the banner without a real review**; the banner is the only thing currently signalling the risk.

---

## Wave 3 — alone, last

### C1 · Split `dispatch.py`
**Closes:** C1 · **Size:** L · **Risk:** medium — mitigated by the existing test suite

**7,068 lines**, up from 2,890 when this document was first written and still growing. It holds routing, ~28 handlers plus an 11-handler factory, the QA retry loop, scoring, scanning, dedupe, month-end reporting, lineage resolution, JSON parsing and 4 exception classes. It is the highest-change-rate file in the repo and every incident touches it.

**Proposed shape:**

```
dispatch/router.py            DISPATCH_TABLE + the five-way readiness branch
dispatch/handlers/ingest.py   scan, score, scanners, dedupe
dispatch/handlers/brief.py    draft-brief, rollups, publish-brief
dispatch/handlers/content.py  the drafting handlers
dispatch/handlers/qa.py       review handlers
dispatch/handlers/publish.py  approval, buffer, newsletter, month-end
dispatch/qa_retry.py          the retry loop
dispatch/lineage.py           resolve_lineage_result
dispatch/parsing.py           _parse_json_content and the render helpers
```

Keep `DISPATCH_TABLE` as the single registration point, and keep the `**SCANNER_HANDLERS` factory spread intact.

**Non-negotiable:** this is a **pure move**. No behaviour changes in the same PR. **Preserve every incident-history comment verbatim** — the dated root-cause writeups are the file's highest-value content and the main reason the system is understandable at all.

**Done when:** the orchestrator test suite passes unchanged, and `git log --follow` still reaches the history for the moved code.

---

## Explicitly not on this list

| Item | Why not |
|---|---|
| Revert the weekly trigger to Monday | **Withdrawn.** Daily is the intended cadence, confirmed 17 Aug 2026. The old F3/R2 was a misreading of a stale comment |
| Wire the newsletter ESP (F8) | Blocked on non-code prerequisites: a Mailchimp account, an audience with a physical mailing address, and CRM sync. Not actionable in a code branch yet |
| Implement the nightly-analytics loop handlers (F2) | Working as designed. The loop file is documentary; the real pipeline is `caj-analytics-nightly-ingest` |
| Feed published performance back into planning (F5) | Real, but blocked on **A1**. Planning is evidence-led off *signal* scores today; making it read *published performance* needs the archetype join key first. Revisit after A1 lands |

---

## Open questions the repository cannot answer

| Question | Why it matters |
|---|---|
| Are `mcp-canva`'s Canva OAuth credentials still live? | Decides whether **A3** is a tidy-up or a credential-revocation task |
| Do Azure Monitor alert rules exist portal-side, outside IaC? | Changes the scope of **A2**, though they should be brought into Bicep regardless |
| Which Buffer tier is intended? | Decides **B1** |
