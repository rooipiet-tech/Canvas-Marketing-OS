# Canvas Marketing OS — Architecture & Product Documentation

**Reverse-engineered from source, August 2026.** The source code was treated
as the single source of truth; no prior documentation was assumed. Where a
statement is inference rather than something the code states explicitly, it
is marked **[INFERRED]**.

This set is written to onboard a new CTO. Read `00` first, then follow the
path that matches your role.

---

## The document set

| # | Document | Read it for |
|---|---|---|
| [00](00-executive-summary.md) | **Executive Summary** | What this is, who it's for, what it solves, how it works — in one sitting |
| [01](01-system-architecture.md) | **System Architecture** | Every layer, with diagrams: frontend, services, database, queues, scheduling, AI, memory, storage, reporting, messaging, deployment, observability |
| [02](02-module-catalogue.md) | **Module Catalogue** | All 13 modules: purpose, features, data, dependencies, maturity, what's missing |
| [03](03-user-journeys.md) | **User Journeys** | Every flow that exists — and the ones a SaaS would have that this doesn't |
| [04](04-data-model.md) | **Data Model** | 27 tables across 5 schemas: entities, relationships, ownership, lifecycle, information flow |
| [05](05-ai-architecture.md) | **AI Architecture** | Agents, prompts, tools, memory, knowledge, decision-making, failures, context, approvals, learning gaps |
| [06](06-business-architecture.md) | **Business Architecture** | Capability map; every feature → department, process, objective, exec owner, value |
| [07](07-operating-model.md) | **Operating Model** | What you meant to build, what you accidentally built, and the gap analysis |
| [08](08-product-positioning.md) | **Product Positioning** | Gartner category, overlapping vendors, differentiators, CIO/CMO/investor framings |
| [09](09-technical-debt.md) | **Technical Debt** | 31 items prioritised by business impact, plus security, testing and documentation gaps |
| [10](10-product-roadmap.md) | **Product Roadmap** | 20 items scored on 6 axes across 3 horizons, plus what *not* to build |
| [11](11-api-catalogue.md) | **API Catalogue** | All ~69 routes across 8 services |
| [12](12-integration-catalogue.md) | **Integration Catalogue** | 18 external systems: auth, data crossing the boundary, failure behaviour |
| [13](13-ai-agent-catalogue.md) | **AI Agent Catalogue** | All 23 function packages, wired vs referenced, and the design patterns |
| [14](14-security-and-permission-model.md) | **Security & Permission Model** | Trust boundaries, the 5-layer authorisation chain, threat model, 4 permission systems |
| [15](15-deployment-and-configuration.md) | **Deployment & Configuration** | Azure topology, CI/CD, hard-won deployment patterns, every env var, bootstrap sequence |
| [16](16-product-vision-and-strategy.md) | **Product Vision & Strategy** | Vision, strategy, the moat, what could kill it, the decision that matters most |
| [17](17-enterprise-vocabulary.md) | **Enterprise Vocabulary** | For each thing you built: what enterprise vendors and consultants call it |
| [18](18-marketing-code-analysis.md) | **The Marketing Code, Analysed** | Line map, structural comparison of the 5 handlers, coupling, 8 analysis hooks, the extraction argument |

---

## Reading paths

**New CTO (first week)** → 00 → 01 → 04 → 09 → 15 → 07

**New engineer (first day)** → 00 → 01 → 02 → 03 → then `.compound/index.md`
for *why* things are the way they are

**Analysing the marketing logic itself** → 18 → `_marketing-code/dispatch-marketing-extract.py` → `dispatch.py`

**Board / investor** → 00 → 08 → 16 → 10

**Enterprise buyer's security review** → 14 → 12 → `docs/accepted-risks.md`

**Sales / marketing** → 08 → 17 → 06

**Compliance / legal** → 14 (Part 2) → 04 (§2.9, §3) → 06 (§6)

---

## The six findings that matter most

1. **1.9% of the executable code contains any marketing logic.** Measured:
   395 of 21,182 non-test Python lines, all of it in five handler functions in
   `dispatch.py`. Eight services; seven contain no marketing logic at all, and
   the eighth is a generic DAG executor. The product is mis-named relative to
   what was built. → `00`, `08`

2. **20 of 23 AI agents never execute.** Every `task_type` not in
   `DISPATCH_TABLE` falls through to a no-op pass-through, so loops run green
   and produce nothing. The single biggest execution gap, and the single
   highest-leverage fix. → `09` TD-01, `10` R1

3. **The system captures rich learning signal and uses none of it.** Approval
   rates, QA violation frequencies, engagement by archetype and cost per
   accepted asset are all written to durable tables, and nothing reads them
   back. This is the gap between a pipeline and an operating system. → `05`
   §12, `07` §D.1, `10` R2

4. **Approval is bound to bytes, not to an object.** The gate token carries
   a content hash in canonical JSON; the Publisher independently recomputes
   SHA-256 over the raw bytes and refuses on mismatch. Approve-A-publish-B is
   cryptographically impossible. This is the strongest differentiator in the
   platform, and it has an exact enterprise analogue: PSD2 dynamic linking.
   → `14` §1.4, `17` §3

5. **The platform's entire knowledge intake rests on one hand-set environment
   variable.** `MCP_WEB_LIVE_MODE` is not declared in `infra/`. Without it,
   `fetch_url` returns a synthetic placeholder for every source and the daily
   loop still reports success — writing hallucinated signals to the Vault with
   real-looking source URLs. The next full infra deploy reverts it. → `09`
   TD-31

6. **The compound engineering loop may be worth more than the platform.**
   79 classified learnings with recurrence tracking, a worktree-per-session
   build model, frozen contracts as the coordination protocol between
   parallel AI sessions — and a live production system as the exhibit. It is
   entirely unproductised. → `07` Part B, `16` §7

---

## Method

- Read every file in the repository (917 files) before drawing conclusions.
- Treated `contracts/` as authoritative, then verified each service against
  its contract.
- Read module docstrings and inline comments as primary evidence — this
  codebase preserves root-cause narratives next to fixes, which made much of
  the reasoning recoverable.
- Cross-checked claims against `.compound/learnings/` (79 entries) and
  `docs/accepted-risks.md`.
- Assessed maturity on evidence: tests present, deployed in Bicep, proven
  live in a comment or learning, or stubbed.
- Marked **[INFERRED]** anything not stated by the code.

## Known limitations of this analysis

- The `.loop/` directory (`spec.json`, `plan.json`, `review.json`,
  `lenses.json`, `domain.md`) is referenced ~26 times across the codebase but
  is **gitignored as session-local state** (`.gitignore` line 2), so it was
  not available. Acceptance-criteria references (`AC-xx`), constraint
  references (`C-x`), decision references (`DE-x`) and review-finding codes
  were interpreted from their usage in code comments, not from the source
  documents. Supplying those files would materially sharpen requirements
  traceability.
- Live Azure state was not inspected. Deployment claims rest on Bicep, on
  workflow definitions, and on comments recording live verification.
- Test *coverage* was not measured — 128 test files and ~400 test functions
  were counted, and gaps were identified by absence of a test category rather
  than by a coverage tool.
