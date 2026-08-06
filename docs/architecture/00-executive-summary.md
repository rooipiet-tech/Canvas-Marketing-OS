# 00 — Executive Summary

*Reverse-engineered from source. Every claim below cites a file. Where a
statement is inference rather than something the code states, it is marked
**[INFERRED]**.*

---

## 1. What this product is

**Canvas Marketing OS (CMOS) is a governed, agent-native execution platform
for marketing operations.** It is not a marketing tool with AI features
bolted on. It is a *control plane* that lets autonomous AI agents perform
real marketing work — market scanning, brief writing, content drafting, QA,
scheduling, publishing — while every action they take is metered, policy-
gated, consent-checked, content-hash-bound, human-approvable, auditable and
reversible by a kill switch.

The single most revealing fact in the repository: there are **eight
backend services**, and only *one* of them (`services/orchestrator`) is
about doing marketing work. The other seven exist to *govern* it:

| Service | Exists to |
|---|---|
| `services/orchestrator` | Do the work (decompose loops, dispatch tasks, call agents) |
| `services/model-gateway` | Meter cost, route models, block PII before it reaches a provider |
| `services/gatekeeper` | Decide whether an action is allowed; issue signed authorisation tokens |
| `services/publisher` | Verify that authorisation before anything reaches the outside world |
| `services/vault` | Be the system of record, with taxonomy, consent and retention on every object |
| `services/registry` | Version, sign and evaluate the agent definitions themselves |
| `services/analytics-ingest` | Close the loop: measure what the agents produced |
| `services/telemetry-lib` | Make every step of the above observable, with PII structurally excluded |

That ratio — **7 governance/measurement services to 1 execution service** —
is the product. What has actually been built is *AI governance
infrastructure that happens to be pointed at marketing*.

And even "one execution service" overstates it. The orchestrator is a generic
DAG executor; it contains no marketing knowledge of its own. **Every line of
marketing logic in the platform lives in five handler functions in a single
file** — `services/orchestrator/orchestrator/dispatch.py`, lines 443–1023,
**395 lines of executable Python out of 21,182 non-test Python lines (1.9%)**.
The domain knowledge those handlers act on ships as five content files staged
into the container image (`services/orchestrator/Dockerfile` L102–106) plus
the permission register — **607 lines** in total. That is the complete
marketing payload of the running system.

## 2. Who it is for

Read literally off the code, there are three distinct user populations:

**(a) The autonomous agents themselves.** This is the primary "user". The
codebase repeatedly uses the term *agent-native* as a design requirement
(`services/gatekeeper/main.py`: "Agent-native by construction (AC-17):
everything a human can observe here, an internal agent caller can observe
over plain HTTP"; `console/README.md`: "send `Accept: application/json` on
any GET route"). Every governance surface has a machine-readable equivalent.
There is no human-only step in the critical path.

**(b) The operator / marketing ops lead.** Served by `console/` — one
externally-reachable web surface with six read screens (task queue, trace
timeline, approval inbox, Vault search, cost ledger, kill switch) and
exactly one write action: `POST /kill-switch/toggle`
(`console/app/routes_write.py` — "The ONLY mutating route in the entire
console app").

**(c) The approver.** A named human who receives an Adaptive Card in
Microsoft Teams (`services/gatekeeper/app/teams_client.py`) or an inbox row,
and clicks a single-use, 24-hour-expiring, Entra-ID-authenticated deep link
(`services/gatekeeper/app/routers/approval_action.py`). Critically:
*possession of the link is never identity* — the approver recorded is the
Easy-Auth principal on the request (`services/gatekeeper/app/auth.py`).

The *customer* the marketing output is aimed at is documented in
`docs/positioning.md`: **the office of the CFO in multi-entity groups**,
South Africa and Southern Africa. The operating company is Canvas
Intelligence, a Chartered-Accountant-founded Microsoft Fabric data
engineering firm.

## 3. What business problems it solves

Four, in descending order of how much code is dedicated to them:

**Problem 1 — "We cannot let AI touch the outside world without control."**
Solved by a five-layer defence chain, each layer independently auditable:
autonomy policy (`services/gatekeeper/policy/autonomy.yaml`, fail-closed at
level 0) → human approval (`governance.approval_inbox`) → signed gate token
(`contracts/gate-token/spec.md`, RS256, `jti`, `exp`, content-hash-bound) →
independent hash re-computation at the boundary
(`services/publisher/app/hashing.py`) → single-use `jti` ledger in Postgres
(`services/publisher/app/jti_ledger.py`). Plus a kill switch checked
uncached on *every* decision and *every* publish
(`services/gatekeeper/app/kill_switch.py`).

**Problem 2 — "We cannot let client/personal data leak to a US LLM
provider."** Solved by a redaction firewall that runs *before* any provider
adapter call (`services/model-gateway/redaction.py`), scanning every
non-system message role and the `tools[]` passthrough against patterns from
a frozen contract (`contracts/model-gateway/redaction-rules.yaml`: SA ID
numbers, SA phone numbers, emails, name-shapes). Every block writes a
`gate_decisions` audit row. Plus consent gating at the data layer: any Vault
write carrying `data_subject_ref` is rejected 403 unless an active
`consent_register` row matches subject+channel+purpose
(`services/vault/vault/consent.py`).

**Problem 3 — "We cannot let AI spend money unpredictably."** Solved by
per-function daily budgets (`services/model-gateway/policy/budgets.yaml`)
with a **downgrade-don't-block** soft breach (opus→sonnet→haiku) and a
hard-breach that queues the request as an escalated `gate_decisions` row and
returns 429 with that row's id (`services/model-gateway/budget.py`,
`completion.py`). Every completion writes three `costs` rows — usd, tokens,
ms (`services/model-gateway/metering.py`) — giving an unbroken roll-up chain
`costs → agent_runs → campaigns`.

**Problem 4 — "We cannot let AI say something we can't defend."** Solved by
a default-deny client-naming register (`docs/permission-register.yaml` —
*nothing is CLEARED today*, and absence blocks identically to explicit
UNCLEARED, proven by a self-test in
`functions/02-brand-steward-qa/permission_check.py`), plus a Brand Steward
QA agent (function 02) whose `pass: false` verdict is *terminal* — the
orchestrator transitions the task to `FAILED` with reason `qa_blocked` and
**never calls `advance_dependents`**, so the downstream approval task can
never see the asset (`services/orchestrator/orchestrator/dispatch.py`,
`qa_review_handler`).

## 4. What category of software this is

The honest answer is that it spans three Gartner categories and is not
cleanly any one of them. See `08-product-positioning.md` for the full
argument. In one line:

> **An AI Agent Orchestration and Governance Platform with a vertical
> marketing-operations application layer.**

The governance half (gatekeeper, model-gateway, publisher, vault, registry)
is horizontal and domain-agnostic. The application half (23 function
packages, 3 loop definitions, 3 MCP servers) is marketing-specific. The
boundary between them is clean enough that the governance half could be
lifted out — which is the strategic option this codebase has accidentally
created.

## 5. How the platform works — the one-paragraph version

A **Logic App** fires a recurrence trigger on a schedule
(`infra/modules/scheduling/`) and drops a **heartbeat event** onto an Azure
Service Bus `event` queue. The **orchestrator**'s background worker loop
picks it up, loads the matching **loop definition** YAML
(`services/orchestrator/loops/*.yaml`), validates it as a DAG
(`loop_loader.py`, Kahn's algorithm), and **deterministically decomposes**
it into tasks whose ids are `uuid5(event_id, task_id)` — so the same
heartbeat always produces the same task ids. Each task is persisted to
`task_state` and published as a **task envelope** carrying *metadata only,
never content* (`contracts/service-bus/spec.md`). The worker consumes those
envelopes; `dispatch.py` routes each `task_type` to a handler. A handler
reads its predecessor's `result_ref` by walking the `depends_on` lineage,
loads a **prompt** from a function package (`functions/NN-*/prompt.md`),
calls the **model-gateway** (which routes, redaction-scans, budget-checks,
calls Anthropic, and meters three cost rows), parses the JSON response,
writes the artefact to the **Vault** (content-addressed blob + taxonomy +
retention policy), records a small `result_ref`, and advances its
dependents. When a task reaches `request-approval`, it calls the
**Gatekeeper**'s `/gate-check`, which evaluates the autonomy policy, writes
a `gate_decisions` row, creates a single-use approval link, and posts a Teams
card. A human clicks; the approver is taken from Easy Auth, not the link.
The next gate-check finds the approval and issues a **signed gate token**
binding `content_hash` + `function_id` in canonical JSON. The **Publisher**
verifies the signature with a pinned algorithm, re-checks the kill switch,
**independently recomputes the hash over the raw bytes**, cross-checks the
Vault's own stored hash, burns the `jti`, and only then — and only if not in
dry-run — calls Buffer via **mcp-buffer**, which can only ever create
*drafts*. Every step emits an OpenTelemetry span with five mandatory
attributes from a closed enum. Overnight, **analytics-ingest** pulls Buffer /
GA4 / Search Console / LinkedIn metrics, reconciles UTM tags, computes four
KPI rollups including *cost per accepted asset*, and exports a validated
JSON payload to blob for Microsoft Fabric.

That paragraph is the product. Everything else is detail.

## 6. Current state, honestly

- **Live and proven end-to-end:** the daily signal loop runs in Azure
  (`cmos-dev`), makes real Anthropic calls, meters real costs, and produces
  real approval cards. `.compound/index.md` L-0027 records the capstone:
  "the full deploy-infra → deploy-gateway → live completion → metering chain
  is proven end to end."
- **Deliberately dry-run:** publishing. `PUBLISHER_DRY_RUN` defaults true
  (`services/publisher/app/config.py`), and a proof-circuit-tagged asset is
  *forced* dry-run regardless of the flag. Nothing has been published to a
  live channel by this system.
- **Stubbed:** `services/publisher/app/vault_adapter.py` is an in-memory
  spy, not a real Vault write. `functions/task-worker/` is a health-check
  placeholder. The console's Gatekeeper client is still `mock` because no
  REST wrapper exists over `kill_switch.py` / `approval_inbox.py`
  (`console/README.md` documents this precisely).
- **Not built:** authentication on the Vault API (network isolation only —
  `docs/accepted-risks.md`), authorisation-by-role on the console (any
  authenticated tenant user reaches the kill switch), multi-tenancy, and any
  notion of a customer other than Canvas Intelligence itself.

## 7. The single most important structural observation

This codebase was **built by an AI agent loop, about an AI agent loop.**

`.compound/learnings/` holds 79 numbered, classified, cross-referenced
learnings with strengthening/recurrence annotations. `README.md` documents a
worktree-per-session development model. Commit messages carry finding codes
(`F-CASCADE-QA-BLOCKED`, `F-INGEST-PUBLIC-SOURCE`). Code comments cite
"heartbeat round 17" and "deploy-loop-e2e-smoke #33" — live production
incidents, root-caused inline, with the fix's reasoning preserved next to
the fix.

That is not a code smell. It is an **organisational memory system**, and it
is arguably a more differentiated asset than the marketing platform itself.
See `07-operating-model.md`.
