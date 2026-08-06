# 08 — Product Positioning

*Derived only from what the code does. No market research, no competitor
interviews — every positioning claim below traces to an implemented
capability.*

---

## 1. The positioning problem, stated plainly

The repository is named "Canvas **Marketing** OS". Its own `README.md` calls
it an *"agent-native marketing operations platform"*. But the code
distribution says something different:

**Measured, not estimated** (`wc -l`, non-test Python across `services/`,
`console/` and `mcp/`; handler region isolated by AST-style line
classification):

| Measure | Lines |
|---|---|
| Non-test Python across the whole platform | **21,182** |
| Executable Python containing **any** marketing domain logic — `dispatch.py` L443–1023 | **395** (1.9%) |
| Marketing content shipped into the running orchestrator image (5 files, Dockerfile L102–106) + the permission register | **607** |
| Marketing artefacts authored but never executed (20 inert packages: prompts, evals, checkers, skills) | **~14,500** |

**Every line of marketing logic that runs in production lives in one file**,
`services/orchestrator/orchestrator/dispatch.py`, in five handler functions —
and 37% of that file is comments. Nothing else in 21,182 lines of Python
knows what marketing is.

The marketing capability that *runs* is: fetch four URLs, summarise into
signals, render a brief, QA it, draft one LinkedIn post, request approval.
That is a modest marketing product. The governance chain around it is an
enterprise-grade one.

**The product is mis-named relative to what was built.** That is the central
positioning finding.

## 2. If Gartner classified this product

Gartner would struggle to place it in one Magic Quadrant, and would most
likely split it. Candidate categories, assessed against the code:

### Primary fit — **AI Agent Orchestration / AI Governance Platform**
*(An emergent category; Gartner's 2025–26 framing is "AI Agent Platforms"
and "AI Trust, Risk and Security Management" — TRiSM.)*

Evidence for: `autonomy.yaml` (policy-based agent authority), `gate_decisions`
(decision audit), the redaction firewall (AI TRiSM's "content anomaly
detection" primitive), per-function budgets (AI cost governance), the gate
token (agent action authorisation), `services/registry` (model/prompt
lifecycle), kill switches (AI incident response). **This is where the
strongest, most complete capability lives, and it is domain-agnostic.**

Evidence against: no agent marketplace, no low-code builder, no multi-agent
reasoning, no LLM-agnostic breadth in practice (one provider implemented).

### Secondary fit — **Content Marketing Platform / Marketing Resource Management**
Evidence for: content lifecycle from brief to publication, brand compliance
QA, approval workflow, digital asset storage with versioning, channel
scheduling.
Evidence against: no calendar UI, no DAM interface, no collaboration, no
creative brief authoring by humans, no campaign planning, one channel.

### Tertiary fit — **Data Privacy Management / Consent & Preference Management**
Evidence for: `consent_register` with lawful basis, per-channel/per-purpose
grants and revocation; consent gating enforced at write time; retention
classes with defensible disposal; cross-border transfer interdiction; full
audit.
Evidence against: no data-subject portal, no DSAR workflow, no data mapping /
RoPA, no breach management.

### Gartner would probably say

> *"A vertically-focused AI agent orchestration platform with unusually
> mature governance, applied to a narrow marketing use case. Evaluate the
> governance layer independently of the application layer."*

That sentence is also the product strategy.

## 3. Overlapping enterprise software

Mapped honestly — where the code genuinely overlaps, and where it does not:

| Vendor / category | Overlaps on | Does **not** overlap on |
|---|---|---|
| **Jasper / Writer / Typeface** (enterprise AI content) | AI content generation, brand voice enforcement, approval workflow | They have brand-voice training, editors, DAM, integrations, teams. We have deeper *governance* and *cost control* |
| **Sprinklr / Hootsuite / Sprout** (social suites) | Scheduling, publishing approval, engagement analytics | They have listening, inbox, CRM, ads, dozens of channels. We have one channel via Buffer, dry-run |
| **Adobe Workfront / Aprimo** (MRM) | Brief→asset→approval lifecycle, DAM with versioning | They have resource planning, budgets, project mgmt, proofing UI |
| **OneTrust / Securiti / TrustArc** (privacy) | Consent register with lawful basis, retention schedules, audit, cross-border controls | They have DSAR automation, data discovery, RoPA, vendor risk, breach mgmt |
| **Credo AI / Holistic AI / Fairly** (AI governance) | Policy-as-code, model registry, decision audit | They focus on model risk/fairness assessment and regulatory mapping; we focus on *runtime* action authorisation |
| **LangSmith / Langfuse / W&B Weave** (LLMOps) | Prompt versioning, eval harness, tracing, cost tracking | They have playgrounds, dataset mgmt, human labelling UI, model comparison. **We have runtime enforcement they do not** |
| **Temporal / Airflow / Dagster** (orchestration) | DAG definition, retries, dead-letter, deterministic runs | They are general-purpose with vastly richer scheduling, UI, backfill. We have governance they do not |
| **Azure AI Foundry / Content Safety** | Content filtering before provider call | Theirs is model-based and richer; ours is regex + fixtures, but *ours writes an audit row and theirs is not tied to a consent register* |

**The honest summary: there is no single vendor whose product is this
combination.** Not because the combination is brilliant, but because most
vendors pick one side. That is simultaneously the opportunity and the risk —
category creation is expensive.

## 4. What makes this fundamentally different

Six differentiators, each traceable to code, ranked by defensibility:

### D1 — Approval is bound to bytes, not to an object
`contracts/gate-token/spec.md` + `services/publisher/app/hashing.py`.
The gate token carries `content_hash` in canonical JSON; the Publisher
**independently recomputes** SHA-256 over the raw bytes and refuses on any
mismatch. It explicitly does *not* trust `assets.content_hash` because that
column is nullable and could be written by another code path.

*No competitor in the AI content space does this.* Everywhere else, "approved"
is a status flag on a row — and a row can be updated after approval. Here,
approving a draft and publishing a different one is **cryptographically
impossible**.

### D2 — The refusal is the product
Publisher records **one immutable row on every branch**, with twelve distinct
reason codes. Gatekeeper writes exactly one `gate_decisions` row per
`/gate-check` on every branch. The Vault writes an audit row on every
rejection, on an isolated connection so it survives the rollback.

Most systems log successes and swallow failures. This one treats *"the AI
tried to do X and was refused because Y"* as the primary business record.
That is what an auditor, a regulator or an enterprise procurement team
actually asks for.

### D3 — Default deny, everywhere, provably
- Unlisted `(function_id, action_class)` → autonomy level 0 → blocked.
- Client name absent from the register → blocked **identically** to explicit
  UNCLEARED, with a self-test that asserts the two produce the same
  `allowed`, the same status semantics and the same violation code.
- `data_subject_ref` present with no matching consent → 403 + audit.
- `asset_id` supplied but Vault lookup fails → refuse to publish.
- Gate token algorithm not in the pinned allowlist → refuse before any
  signature work.

Fail-closed is claimed by everyone. Here it is *tested* at every layer.

### D4 — Cost governance with graceful degradation
Soft breach **downgrades the model tier and keeps working**; hard breach
**queues the request as an escalated gate decision and hands back that row's
id**. Three cost rows per completion (usd/tokens/ms) with an unbroken FK
chain to campaigns. Then a nightly KPI: **cost per accepted asset**.

Most AI platforms report token spend. This one reports the *unit cost of
usable output* — a CFO metric, not an engineering metric.

### D5 — The dangerous capability doesn't exist
mcp-buffer has no publish tool. Not a disabled tool — **no tool**. A test
greps every tool name and description against
`publish|share.?now|send.?now|go.?live`. mcp-canva requires `template_id` on
both creation tools, so free-form design generation is unrepresentable.
mcp-web checks the host allowlist *before* any network call.

"Make the dangerous thing unrepresentable" is a stronger guarantee than
"make the dangerous thing configurable-off", and it is legible to a security
reviewer in seconds.

### D6 — Jurisdiction-specific by design
SA ID number and SA phone-number regexes in a frozen contract. POPIA s11
lawful-basis modelling (a *value*, not a boolean). POPIA s72 cross-border
transfer analysis documented as explicitly unresolved rather than assumed
away. `southafricanorth` region pinned as a literal, never
`resourceGroup().location`, with a documented reason. South African English
enforced as a brand rule with its own violation code.

**This is a South African data-sovereignty AI platform.** For a
Johannesburg CFO evaluating US AI tooling, that is not a feature — it is the
purchase criterion.

## 5. What CIOs would call this

> **"An AI action-governance layer."**

The CIO's question is *"how do I let AI agents do real work in my
environment without losing control of what they touch?"* This platform's
answer is a five-layer chain they can inspect: policy → approval → signed
token → boundary verification → audit. Plus the things a CIO checks first
and usually finds missing: no standing credentials (OIDC + managed identity
throughout), private endpoints on every data resource, internal-only ingress
on six of eight apps, a kill switch with a proven sub-5s bound, and an
accepted-risk register that *names the risks it hasn't closed*.

`docs/accepted-risks.md` is, in a CIO conversation, worth more than any
feature. It says: Service Bus has no private endpoint (here are three
compensating controls and the production path); the Vault API has no
authentication (this was a builder judgement call, not a user-approved
acceptance — flagged so it is tracked rather than silently shipped); the
registry is signed with a committed dev key (here is why Key Vault couldn't
be used, and here is the algorithm correction needed); the console
authenticates but does not authorise. **Very few vendors will show a CIO that
document.**

## 6. What CMOs would call this

> **"A marketing production line that can't embarrass us."**

The CMO's fear about AI marketing is a specific, nameable one: the tool
publishes something wrong, or names a client without permission, or makes a
claim legal can't defend, and it happens at 3am with nobody watching.

This platform's answer, in CMO language:
- **It cannot name a client we haven't cleared.** Not "it's told not to" —
  the register defaults to deny and absence is not permission.
- **Every claim needs a client, a number or an artefact.** Superlatives with
  nothing attached are a named, blocking violation.
- **It writes in our English.** `productise`, not `productize`.
- **It closes on our roof line.** Every post ends "Your Data. Delivered."
- **Nothing goes out without a human clicking, and the click is tied to the
  exact words approved.**
- **We can stop it in five seconds.**
- **We know what each usable post costs.**

Weakness to be honest about: today the CMO gets *one draft LinkedIn post per
day, in dry-run*. The governance is enterprise-grade; the output volume is
not yet. The 20 unwired function packages are the answer, and they are
mostly written.

## 7. What investors would call this

Three framings, in ascending order of valuation and of execution risk:

### Framing A — "AI-native marketing ops for regulated mid-market"
*Vertical SaaS.* Comparables: Jasper, Writer, Typeface. Multiples 6–10× ARR.
Requires: multi-tenancy, more channels, a real editorial UI, activating the
20 packages. **Risk: crowded, well-funded, and the moat is governance the
market may not yet price.**

### Framing B — "The control plane for enterprise AI agents"
*Horizontal infrastructure.* Comparables: Credo AI, Vanta-for-AI, LLMOps
platforms. Multiples 15–25× ARR. Requires: extracting the governance layer
from the marketing layer, an SDK, multi-provider support, agent-framework
integrations (LangChain, CrewAI, MCP-native).
**The code already supports this: `gatekeeper`, `model-gateway`, `publisher`,
`vault`, `registry` and `telemetry-lib` contain zero marketing logic.** The
seam is real and it is clean.

### Framing C — "Compound engineering — an AI software factory with proof"
*Method + platform.* The 79 compound learnings, the worktree-per-session
model, the spec→plan→build→multi-lens-review loop, and a live production
system built by it. Comparables: none clean. This is the framing that gets a
different kind of conversation — but it needs the method extracted from this
one repository to be credible.

### The investor question that actually matters

> *"You built a marketing platform where 1.9% of the executable code contains
> any marketing logic at all. Was that the plan, or is the governance the
> product?"*

The honest answer — and the strongest one — is: **the governance is the
product; marketing is the proof it works on something real.** Framing B
follows directly from that answer, and the code supports it today.

## 8. Positioning statement

Built to the same structure as `docs/positioning.md`'s own house style:

> **Canvas Marketing OS is the AI action-governance platform that lets
> autonomous agents do real marketing work — with every model call metered,
> every claim evidence-graded, every client name permission-checked, every
> publication cryptographically bound to the exact bytes a named human
> approved, and every refusal permanently recorded.**
>
> Short form: *Autonomy you can audit.*
>
> Elevator: *Enterprises want AI agents doing real work. Their CIOs, GCs and
> CFOs won't allow it, because nobody can prove what the agent was allowed to
> do, what it actually did, what it cost, or who said yes. We built the
> control plane that proves all four — policy as code, human approval bound
> to content hashes, per-function cost budgets, and an append-only decision
> ledger. It runs today, in production, on Azure, in South Africa, governing
> a live marketing pipeline.*

## 9. Positioning risks

| Risk | Why it's real |
|---|---|
| **The name works against the value** | "Marketing OS" invites comparison to Sprinklr; the governance layer then reads as over-engineering rather than as the point |
| **Category creation is expensive** | "AI action governance" is not a line item in anyone's budget yet |
| **Single-tenant is not a SaaS** | No `tenant_id` in any of 5 schemas; every commercial model except per-instance managed service needs a schema change |
| **Governance without volume is unconvincing** | A demo showing one dry-run LinkedIn post per day undersells a five-layer control chain |
| **Regional strength is regional limit** | SA-specific regexes and POPIA framing are a moat in Johannesburg and a rebuild in Frankfurt |
| **Provider-agnostic in design, single-provider in fact** | The extension point is real and unexercised; a buyer will ask |
