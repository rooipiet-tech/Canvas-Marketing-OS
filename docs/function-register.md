# Function register — design of record

The 112-row agent register from `canvas_agentic_marketing_engine_blueprint_1.docx`
(22 July 2026), transcribed into version control, with each row's build status
as of `main` @ `9ada17a` (3 September 2026).

## Why this file exists

The blueprint was never committed. `git log --all -- '*blueprint*'` returned
empty for the whole life of the project, so every build session worked from
whichever slice of the register happened to be pasted into its prompt that
day — and no session could check its own numbering against anything.

The cost is recorded in the repository itself.
`services/gatekeeper/policy/autonomy.yaml` L15-17 says, in the file:

> "There is no autonomy blueprint anywhere in this repo (confirmed by repo-wide
> grep), so the level semantics and the function_id / action_class taxonomy
> below are this session's first-draft convention."

Four of the 25 function packages built to date carry a number that belongs to a
different function, or to no register row at all (see **Numbering drift**
below). The two sessions that *did* have the register in front of them —
`session/s10-intelligence` (commit `ded518d`, whose message reads "per the
blueprint register") and `session/s11-content` (`4f854d9`) — numbered all
twenty of their packages correctly. Every drift is in a package built outside
those two sessions.

**Before adding a function package, check its id here.**

## Provenance

| | |
|---|---|
| Source | `canvas_agentic_marketing_engine_blueprint_1.docx`, committed alongside this file |
| Source sha256 | `cf0dbfb774383fcda6d500a0c8ed9bf4307afd528b115d307327e116d1e6b7c6` |
| Extraction | `word/document.xml` paragraph text; 1,412 paragraphs; rows read as id / name / mission |
| Rows | 112 across 9 families, sized 8/18/12/16/10/11/14/13/10 — matches the blueprint's own count |
| Build status as of | `main` @ `9ada17a`, 3 September 2026 |

Mission text is verbatim from the blueprint. The two right-hand columns are
this repository's state, and are the only part of this file that should change
without a corresponding design amendment.

## Status vocabulary

| Status | Meaning |
|---|---|
| `live` | A function package a running loop reaches through `orchestrator.dispatch.DISPATCH_TABLE`. |
| `service` | The designed job exists in the platform as infrastructure rather than as an agent package. |
| `drift` | The row's id is occupied by a package that belongs at a different row, or at none. Nobody decided this. |
| `—` | Not started. |

## Counts

| Status | Rows |
|---|---:|
| live | 19 |
| service | 13 |
| drift | 4 |
| not started | 76 |
| **Total** | **112** |

`live` counts 19 rows but **25 packages**, because designed function 18
(Industry Trend Analyst) shipped as six independent vertical packages — see
`functions/_shared/vertical-intelligence-method.md`.

**This is a moving number.** `claude_design-vs-built-audit-2026-08.md` reported
12 live / 12 unwired against `main` @ `b90b246` (10 August). All twelve of those
unwired packages have since been given handlers, `48-fact-check-verdict` has
gone from zero golden eval tasks to eight, and a twenty-fifth package
(`17-source-scout`) has been added. Every package under `functions/` is now
reachable from a running loop. Any earlier count you hold is stale — see
**What has changed since the August audit** below.

## The register

### 1. Executive command, governance and control

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 1 | Growth Orchestrator / Chief Marketing Agent | Own the end-to-end marketing objective and coordinate every squad. | service | `services/orchestrator` |
| 2 | Strategy and Prioritisation Agent | Rank markets, offers, campaigns and experiments by expected value. | **drift** | `functions/02-brand-steward-qa` — holds Brand Steward (row 3) |
| 3 | Brand Steward | Protect Canvas voice, positioning, visual identity and category consistency. | live | `functions/02-brand-steward-qa` |
| 4 | Evidence and Claims Guardian | Ensure every factual, technical and performance claim is defensible. | — | — |
| 5 | Legal, Privacy and Consent Gatekeeper | Prevent unlawful, non-consensual or contractually restricted marketing actions. | — | — |
| 6 | Human Approval Router | Route decisions to the correct executive, SME, sales or legal approver. | service | `services/gatekeeper` |
| 7 | Budget and Capacity Allocator | Allocate people, model, media and production capacity to the best opportunities. | service | `services/model-gateway (cost metering)` |
| 8 | Incident and Recovery Agent | Detect, contain and learn from brand, data, publishing or automation failures. | — | — |

### 2. Market, competitor and customer intelligence

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 9 | Market Intelligence Director | Synthesize external signals into a daily and weekly decision brief. | live | `functions/09-market-intelligence-director` |
| 10 | Competitor Discovery Crawler | Continuously identify direct, adjacent and emerging competitors worldwide. | live | `functions/10-competitor-discovery-scanner` |
| 11 | Competitor Change Monitor | Detect material changes in competitors' websites, hiring, messaging, products and campaigns. | live | `functions/11-competitor-change-monitor` |
| 12 | Competitor Positioning Analyst | Explain how each competitor frames value, differentiation and proof. | live | `functions/12-competitive-positioning-analyst` |
| 13 | Competitor Content Performance Scout | Find competitor and peer content that earns disproportionate engagement or search visibility. | live | `functions/13-competitor-content-performance-scout` |
| 14 | Pricing and Packaging Tracker | Monitor public pricing, packaging, free trials, guarantees and commercial models. | — | — |
| 15 | New Product and Service Scout | Find global products and services that Canvas could partner with, emulate or integrate. | — | — |
| 16 | Microsoft, Fabric and Power BI Ecosystem Scout | Track changes in Canvas's core Microsoft ecosystem. | live | `functions/16-microsoft-fabric-ecosystem-scout` |
| 17 | Adjacent Technology Scout | Monitor AI agents, data platforms, automation, ERP, CRM and vertical analytics outside Microsoft. | **drift** | `functions/17-source-scout` — holds Source Scout, which has no blueprint row |
| 18 | Industry Trend Analyst | Translate sector developments into Canvas-specific implications. | live | `functions/18-01..18-06-vertical-intel-* (six packages)` |
| 19 | Regulatory and Policy Monitor | Identify regulation that creates data, reporting, AI governance or automation demand. | — | — |
| 20 | Tender and RFP Opportunity Scout | Find public and partner-led opportunities aligned to Canvas capabilities. | — | — |
| 21 | Event, Conference and Webinar Scout | Identify speaking, sponsorship, networking and content opportunities. | — | — |
| 22 | Partner and Channel Intelligence Agent | Map Microsoft partners, ERP vendors, ISVs, consultants and referral channels. | — | — |
| 23 | Voice-of-Customer Miner | Extract recurring pains, language, objections and desired outcomes from customer interactions. | — | — |
| 24 | Win/Loss Analyst | Determine why Canvas wins, loses, stalls or gets displaced. | — | — |
| 25 | Competitive Response Strategist | Convert material competitor moves into fast, proportionate response decisions. | live | `functions/25-competitive-response-strategist` |
| 26 | Client Advocacy Harvester | Systematically convert delivery success into approved, reusable proof. | live | `functions/26-client-advocacy-harvester` |

### 3. Segmentation, positioning and planning

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 27 | ICP Architect | Define the accounts Canvas should and should not target. | — | — |
| 28 | Buying Committee Mapper | Map economic buyers, champions, users, blockers and hidden decision-makers. | — | — |
| 29 | Persona and Jobs-to-be-Done Agent | Turn role and context into actionable needs and decision jobs. | — | — |
| 30 | Account Segmentation and Scoring Agent | Rank named accounts for ABM and outbound support. | — | — |
| 31 | Vertical Prioritisation Agent | Select industries and use cases that deserve dedicated campaigns. | — | — |
| 32 | Positioning and Messaging Architect | Create the category narrative and message hierarchy. | — | — |
| 33 | Offer and Package Designer | Turn capabilities into clear, buyable offers. | — | — |
| 34 | Campaign Strategist | Design integrated campaigns tied to a commercial objective. | — | — |
| 35 | ABM Account Planner | Create one-to-one and one-to-few plans for strategic accounts. | — | — |
| 36 | Channel Mix Planner | Choose the right balance of owned, earned, partner, social, email, events and paid media. | — | — |
| 37 | Editorial Portfolio Planner | Balance evergreen, campaign, proof, executive and reactive content. | — | — |
| 38 | Forecast and Scenario Planner | Forecast reach, leads, pipeline, cost and capacity under alternative plans. | — | — |

### 4. Research, content and thought leadership studio

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 39 | Insight-to-Story Editor | Turn raw research into a sharp, differentiated story angle. | live | `functions/39-insight-to-story-editor` |
| 40 | SME Interviewer and Knowledge Harvester | Capture genuine Canvas expertise from executives and delivery teams. | — | — |
| 41 | Research Brief Writer | Create evidence-rich briefs for every major asset. | live | `functions/41-research-brief-writer` |
| 42 | Long-form Article Writer | Write authoritative website articles and guides. | **drift** | `functions/42-linkedin-post-writer` — holds LinkedIn Post Writer (row 44) |
| 43 | Executive and Founder Ghostwriter | Convert Pieter and other leaders' expertise into authentic viewpoints. | live | `functions/43-executive-ghostwriter` |
| 44 | LinkedIn Post Writer | Create concise, credible B2B posts for leaders and the company page. | live | `functions/42-linkedin-post-writer` |
| 45 | Carousel and Document Post Writer | Design slide-by-slide narratives for LinkedIn document posts. | live | `functions/45-carousel-post-writer` |
| 46 | Email and Newsletter Writer | Create useful email sequences and editorial newsletters. | live | `functions/46-newsletter-writer` |
| 47 | Case Study Writer | Convert delivery outcomes into credible proof assets. | live | `functions/47-case-study-writer` |
| 48 | White Paper and Benchmark Report Writer | Produce signature research that creates category authority. | **drift** | `functions/48-fact-check-verdict` — holds Fact Checker (row 53) |
| 49 | Webinar, Podcast and Video Script Writer | Create structured, natural scripts that reveal expertise. | — | — |
| 50 | Sales Enablement Writer | Create assets that help sales diagnose, explain and close. | — | — |
| 51 | SEO and AI-Answer Content Optimizer | Improve discoverability in search and AI answer systems without degrading quality. | — | — |
| 52 | Content Repurposing Agent | Convert one high-value source into many channel-native assets. | live | `functions/52-content-repurposer` |
| 53 | Fact Checker and Citation Verifier | Independently verify claims, numbers, quotes and source recency. | live | `functions/48-fact-check-verdict` |
| 54 | Editorial Quality Editor | Improve clarity, logic, originality, tone and usefulness. | — | — |

### 5. Creative, design and digital experience

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 55 | Creative Director | Translate strategy into a coherent creative concept and system. | — | — |
| 56 | Visual Design Agent | Produce on-brand static assets and layouts. | — | — |
| 57 | Data Visualisation Storyteller | Turn data into accurate, persuasive visual explanations. | — | — |
| 58 | Video Producer | Plan video formats that can be produced consistently. | — | — |
| 59 | Video Editor and Clipper | Create polished long and short video assets. | — | — |
| 60 | Motion and Animation Agent | Explain complex data and AI concepts through motion. | — | — |
| 61 | Landing Page UX Designer | Design low-friction, trust-rich campaign experiences. | — | — |
| 62 | Brand Template Librarian | Maintain controlled, reusable creative systems. | — | — |
| 63 | Accessibility and Readability Reviewer | Ensure assets are legible and usable across audiences and devices. | — | — |
| 64 | Asset Rights and Metadata Manager | Control ownership, permissions, versions and discoverability of assets. | — | — |

### 6. Publishing, distribution, community and reputation

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 65 | Publishing and Scheduling Agent | Publish approved assets at planned times with correct metadata. | service | `services/publisher` |
| 66 | Channel Formatter | Adapt approved master content to each platform's native format. | — | — |
| 67 | Social Engagement and Comment Agent | Support timely, useful participation in relevant conversations. | — | — |
| 68 | Employee Advocacy Agent | Help experts and staff share useful content authentically. | — | — |
| 69 | Partner Co-Marketing Agent | Plan and execute joint content and campaigns with partners. | — | — |
| 70 | Creator and Industry-Expert Scout | Identify credible niche voices for collaboration. | — | — |
| 71 | Community Listening Agent | Monitor relevant discussions for questions, needs, objections and opportunities. | — | — |
| 72 | Newsletter Operations Agent | Manage list health, segmentation, send operations and reporting. | — | — |
| 73 | Webinar and Event Operations Agent | Run registration, reminders, attendee experience and follow-up. | — | — |
| 74 | PR and Media Pitch Agent | Earn credible coverage, contributed articles and expert commentary. | — | — |
| 75 | Reputation and Crisis Monitor | Detect negative narratives, misinformation or sensitive issues early. | — | — |

### 7. Demand generation, account activation and revenue support

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 76 | Lead Magnet and Interactive Tool Agent | Create practical assets that exchange real value for attention and data. | — | — |
| 77 | Landing Page CRO Agent | Continuously improve qualified conversion on campaign pages. | — | — |
| 78 | Paid Media Strategist | Define paid media role, audiences, objectives and budget guardrails. | — | — |
| 79 | Ad Copy and Creative Variant Agent | Generate disciplined variants tied to specific hypotheses. | — | — |
| 80 | Media Buying and Bid Optimizer | Execute budget and bid changes within approved limits. | — | — |
| 81 | Retargeting Agent | Sequence useful follow-up based on demonstrated interest. | — | — |
| 82 | ABM Activation Agent | Coordinate personalized touches across a named account and buying group. | — | — |
| 83 | Lead Capture and Form Optimisation Agent | Capture enough information for action without unnecessary friction. | — | — |
| 84 | Lead Enrichment and Routing Agent | Enrich and route leads to the right owner with context and urgency. | — | — |
| 85 | Nurture Sequence Agent | Move relevant prospects toward a useful next step over time. | — | — |
| 86 | Website Personalisation Agent | Adapt proof, use cases and CTAs to known audience context. | — | — |
| 87 | Sales Handoff and SLA Agent | Ensure marketing signals become timely, contextual sales action. | — | — |
| 88 | Proposal and RFP Support Agent | Reuse approved marketing proof and research in sales documents. | — | — |
| 89 | Revenue Attribution Agent | Connect marketing activity to account, opportunity, revenue and gross profit. | — | — |

### 8. Measurement, experimentation and continuous learning

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 90 | Measurement Architect | Define the metric tree, event taxonomy, targets and decision rules. | service | `services/analytics-ingest (partial)` |
| 91 | Marketing Data Engineer | Build reliable pipelines from channels, website, CRM and content systems. | service | `services/analytics-ingest (partial)` |
| 92 | Marketing Dashboard and BI Agent | Deliver role-specific, decision-ready dashboards in Power BI. | service | `analytics/powerbi (starter definition only)` |
| 93 | Data Quality and Identity Resolution Agent | Maintain trustworthy account, contact, campaign and content identities. | service | `services/analytics-ingest (partial)` |
| 94 | Content Performance Analyst | Explain which content creates attention, trust and commercial movement. | — | — |
| 95 | SEO and Search Performance Analyst | Diagnose organic visibility, query demand and technical issues. | — | — |
| 96 | Social Performance Analyst | Evaluate social activity beyond vanity metrics. | — | — |
| 97 | Funnel and Conversion Analyst | Find leakage and friction from first touch to revenue. | — | — |
| 98 | Experiment Designer | Turn ideas into testable, decision-ready experiments. | — | — |
| 99 | Experiment Statistician | Analyze results without overclaiming weak evidence. | — | — |
| 100 | Anomaly Detection Agent | Detect unexpected changes in traffic, spend, conversion, data or reputation. | — | — |
| 101 | Agent Evaluator | Score agent outputs and trajectories against objective rubrics and test sets. | — | — |
| 102 | Prompt and Process Improvement Coach | Propose safe improvements to prompts, tools and workflows. | — | — |

### 9. Agent platform, knowledge and engineering

| ID | Agent / function | Mission (blueprint, verbatim) | Status | Built as |
|---:|---|---|---|---|
| 103 | Agent Platform Architect | Design the runtime, orchestration, state, permissions and deployment model. | — | — |
| 104 | Workflow Automation Engineer | Implement deterministic triggers, routing and integrations. | — | — |
| 105 | MCP and Integration Engineer | Connect Claude agents to approved tools and data using controlled interfaces. | service | `mcp/` |
| 106 | Knowledge Base Librarian | Maintain the trusted source of truth for brand, offers, evidence and learning. | service | `services/vault` |
| 107 | Prompt and Skill Engineer | Create reusable, versioned agent instructions and output schemas. | — | — |
| 108 | Context and Memory Manager | Provide each task with the right context while preventing leakage and bloat. | — | — |
| 109 | Model Router and Cost Optimizer | Use the least expensive model and tool path that meets the quality bar. | service | `services/model-gateway` |
| 110 | Observability and Logging Agent | Make every important agent decision traceable. | service | `services/telemetry-lib` |
| 111 | Security and Access Control Agent | Enforce least privilege, secret handling and environment separation. | — | — |
| 112 | Test and Evaluation Harness Engineer | Build repeatable unit, integration, scenario, safety and regression tests. | service | `services/registry` |

## Numbering drift

Four packages sit under a number that already belonged to another function, or
to no register row at all. Each is a working package built correctly and filed
wrongly — and the row it took belongs to something still unbuilt, so every
collision is latent rather than active.

| Package | Filed at row | That row is really | Belongs at | Origin |
|---|---:|---|---:|---|
| `functions/02-brand-steward-qa` | 2 | Strategy and Prioritisation Agent | 3 | `241be03`, 28 Jul — shipped as one of "3 worked-example functions" for the registry tooling, not as a register entry. Its own commit message says "function 2-**scope**". |
| `functions/17-source-scout` | 17 | Adjacent Technology Scout | none | Proposes candidate source URLs for a scan profile. A genuine build-discovered need with **no blueprint row at all** — eleven scan profiles shipped without sources and nothing in the design covers finding them. |
| `functions/42-linkedin-post-writer` | 42 | Long-form Article Writer | 44 | `241be03`, 28 Jul — same commit, same cause. |
| `functions/48-fact-check-verdict` | 48 | White Paper and Benchmark Report Writer | 53 | `496805a`, 6 Aug — added mid-PR while implementing dispatch handlers; its own commit calls it "a first-draft fact-check policy (function 48), unreviewed". |

### What to do about them

Renaming a package directory changes `function_id`, which appears in
`services/gatekeeper/policy/autonomy.yaml`, in `governance.approval_inbox`
rows, in the costs ledger, and on rendered approval cards. It is a data
migration, not a `git mv` — so it is not free, but it gets more expensive with
every row written under the wrong id.

All four displaced rows (2, 17, 42, 48) are unbuilt today, so nothing collides
yet. Rows 42 and 48 sit in the content studio, the most active family in the
build; long-form articles and the benchmark report are plausible next work.
Decide before then, not during.

`functions/17-source-scout` is the cheapest to fix and the most instructive: it
is not a mis-numbering of an existing row but a function the design does not
have, given a register id anyway. Build-discovered functions should take ids
above 112, so that "is this in the blueprint?" stays answerable from the number.

## What has changed since the August audit

`claude_design-vs-built-audit-2026-08.md` was written against `main` @
`b90b246` (10 August). `main` has moved 178 commits since, and several of its
findings are closed:

| Audit finding | State at `9ada17a` |
|---|---|
| §1: 12 of 24 packages reachable; 17 of 22 daily task types are silent no-ops | **Closed.** All 25 packages reachable; every task type in every non-documentary loop has a handler. |
| DRIFT-9 / G10: `48-fact-check-verdict` ships 0 golden evals while gating publishing | **Closed.** 8 eval tasks. |
| G8: nothing in CI detects an unwired package | **Closed** by `test_dispatch_scanner_tail.py::test_every_loop_task_type_has_a_handler`, which globs `loops/*.yaml` and skips documentary loops. |
| DRIFT-3: three mis-numbered packages | **Open — and now four.** See above. |

Three loops have also shipped since (`publish-loop`, `source-discovery-loop`,
`month-end-reporting-loop`), and the scanners' own follow-on problem has
changed shape: they are wired, but eleven of twelve scan profiles carry no
source URLs, which `17-source-scout` exists to fix. A fresh audit against
`9ada17a` is warranted; do not quote the August one as current.

## Related

- `claude_design-vs-built-audit-2026-08.md` — design-vs-built audit **as of 10 August**; read the table above before quoting it
- `docs/function-register-coverage.md` — `session/s10-intelligence`'s own coverage report
- `docs/content-studio-coverage.md` — `session/s11-content`'s equivalent
- `services/orchestrator/tests/test_dispatch_scanner_tail.py` — asserts no loop task can silently do nothing again
