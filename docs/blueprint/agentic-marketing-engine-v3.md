# Canvas Intelligence — Agentic Marketing Engine, blueprint v3

**CANVAS INTELLIGENCE \| AGENTIC MARKETING ENGINE**

**CANVAS INTELLIGENCE**

Agentic Marketing Engine

A full operating blueprint for a continuous AI-powered B2B marketing
team

**129 specialist functions • 15-agent daily core • ratification model • earn-in autonomy • continuous discovery**
improvement loops**

**Prepared for Pieter van Zyl** Canvas Intelligence · v1 22 July 2026 · v2 4 September 2026 · **v3 4 September 2026** Johannesburg, South Africa
Johannesburg, South Africa

Research status: v1 sources checked 22 July 2026. v2 amendments are grounded in CMOS production
telemetry to 4 September 2026 (see §0). Commercial plans and platform features can change; verify
before implementation.

**Provenance.** v1 is `canvas_agentic_marketing_engine_blueprint_1.docx`, sha256
`cf0dbfb774383fcda6d500a0c8ed9bf4307afd528b115d307327e116d1e6b7c6` (pinned in PR #147). v1 is never edited or replaced. This
document is v2: v1 carried forward in full, with every amendment listed in Appendix A and the
machine-readable half — `docs/function-register.md` — updated in the same commit. A Claude Code
session plans and checks its work against this file and the register, nothing else.
**v3 provenance.** v2 landed as `docs/blueprint/agentic-marketing-engine-v2.md` (PR #148) and is never edited. v3 is v2 carried forward with one new chapter — §11 Continuous Discovery — plus the amendments it requires, all listed in Appendix A under the v2 → v3 heading. Audit v2 and Road to 219 are amended by delta rows (Appendix E), not re-run. Rulings that shaped v3 (Pieter, 4 Sep 2026): source approvals go through Teams as option cards like every other decision; discovery runs **daily across all signal classes**; **one discovery API and one crawler** are permitted, overriding the v1 exclusion; the egress allowlist **auto-widens for reputable domains under a deterministic rule**; v3 is a delta.

**§0 baseline, v3 status.** v3 does **not** restate §0. Two of its numbers are unreconciled between two same-day reads of a table that should only grow (Brand Steward pass 59.9% → 64.1%; `unsupported-claim` 81 → 31; hypothesis: `caj-vault-retention-expiry`, unchecked). Until that is resolved the v2 §0 figures stand as *recorded on 4 Sep 09:49 UTC*, not as a settled baseline, and no v3 threshold is derived from them. Resolving the discrepancy is PR 5b's first task (it is the same vault the yield rows will live in). One §0 fact is corrected here because it is a live fact, not a design choice: **9 of 12** scan profiles carry no source URLs, not 11 of 12 as first reported.

**Open decisions carried forward from v2, unchanged.** D1 founder video (record / synthetic / drop — recommendation: record); D2 approval budget (6 cards per working day shipped as the default in `policies/autonomy-matrix.yaml`; ratify or change before Appendix D PR 5); Canva refresh token (unlock C; blocks Fn 121); WordPress re-authorisation (unlock B; blocks s13-website); scan-profile source approval (now: one-time ratification of the provisional Stage 0 seed, then `source.promote` cards under §11). v3 adds one: the choice of discovery API and crawler vendor — the ruling permits them; the vendor is an errand recorded in `docs/accepted-risks.md` at PR 5c.

**Operating principle of v2 — agents author, humans ratify.** v1 assumed humans are the source of
expertise (SME interviews, founder opinions), of ground truth (sales acceptance reasons, human
edits as the learning signal) and of operations (a marketing operator). v2 removes all three as
human *authoring* roles. Every human touchpoint becomes an option card: two or three materially
distinct options, a recommendation, evidence, a risk tier and a declared timeout behaviour.
Accountability stays human; authorship moves to the system. Autonomy is then *earned* per function
from decision telemetry (§G2), never granted by fiat.

**§0 — Live baseline that v2 is written against (30 days to 4 Sep 2026)**

| Measure | Value | Consequence for v2 |
|---|---|---|
| Drafts clearing both QA gates | ~25% (59.9% Brand Steward × 41.6% Fact Check) | Downstream autonomy is not lowered anywhere in v2 |
| Dominant failure code | `fabricated-proof-point`, 226 events — 3× the next code, a month and three prompt fixes after first diagnosis | Evidence references become resolvable-or-rejected (§C3); prompts alone have not fixed this |
| Items published via the governed path | 0 | Level promotion to L3 downstream requires 20 clean governed publishes (§G2) |
| Human eval-authoring backlog | ~380 tasks for the remaining functions | Upstream autonomy is pushed hard: Fn 127 generates evals; humans ratify sets by sampling |

Upstream (produce, analyse, evaluate) and downstream (publish, claim, spend) point in opposite
directions in this data. v2 treats them as two autonomy axes, not one.

**EXECUTIVE DECISION**

# Executive summary

Canvas should build a continuous marketing operating system, not a
collection of disconnected content bots. The system should ingest market
and customer signals, convert them into prioritised commercial
hypotheses, produce proof-led content and campaigns, distribute them
through the appropriate channels, connect engagement to CRM
opportunities, and use results to improve the next cycle.

The recommended design defines 112 specialist functions but deploys only
about 15 core agents in the daily operating loop. Most specialist roles
should be invoked as sub-agents or reusable skills. This avoids
duplicated context, runaway cost and coordination failure while still
giving Canvas a complete functional team.

The commercial centre of gravity should be enterprise trust and
qualified pipeline. Canvas already positions itself as a Microsoft
partner and specialist in data engineering, analytics, pre-developed
Azure/Power BI data platforms, customised solutions, advisory and Centre
of Excellence services. Marketing must make these capabilities easier to
buy by translating them into vertical problems, quantified outcomes,
implementation proof, executive viewpoints and clear entry offers.

| **Recommended north star** ICP Qualified Pipeline Efficiency = the expected gross-profit value of new, sales-accepted opportunities from ideal-customer-profile accounts, divided by total marketing cost. Reach, clicks and engagement remain diagnostics—not the final objective. |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

| **Design decision**   | **Recommendation**                                                                      | **Reason**                                                              |
|-----------------------|-----------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| Agent count           | 128 defined functions (112 v1 + 16 ratification/earn-in functions, IDs 113–128 provisional until assigned in `docs/function-register.md`); 15 core agents active daily | Completeness without running dozens of duplicated autonomous processes. |
| Primary market motion | Proof-led B2B thought leadership + account-based demand generation                      | Canvas sells high-consideration enterprise services and platforms.      |
| Primary channels      | LinkedIn, website/search, email, webinars/events, partner channels, targeted paid media | Matches buying committees and supports long, multi-touch journeys.      |
| Production principle  | One high-value insight source becomes many channel-native assets                        | Maximises SME leverage while reducing low-value AI content.             |
| Automation principle  | Deterministic workflow for routing and publishing; agents for judgment                  | Improves reliability, traceability and cost control.                    |
| Autonomy principle    | Agents author, humans ratify. Every human touchpoint is an option card; publish, spend and claim stay gated; autonomy is earned per function and action class from decision telemetry (§G2). | Protects reputation and confidentiality while removing every human authoring role. Grounded in §0. |
| Discovery principle [v3] | Sources are discovered, not configured. Daily discovery across every signal class through Claude web research, Semrush, one discovery API and one crawler, governed by Fn 129; candidates and retirements are option cards; the allowlist widens by rule. | 9 of 12 scan profiles were empty (corrected 4 Sep from the 11/12 first reported) and the discovery trigger silent for a week while the daily loop dead-lettered 400+ tasks per three days. Hand-managed sources do not survive contact with a daily loop. |

**RESEARCH BASIS**

# What current evidence means for Canvas

| **Finding**               | **Canvas implication**                                                                                                                                                                                                                                                                                        |
|---------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Canvas proposition        | The public proposition is broad and credible: Microsoft-partner data/AI expertise, pre-developed Azure/Power BI platforms, customised BI, advisory and a Centre of Excellence model. The marketing engine should create clearer vertical entry points and proof rather than invent a new generic AI identity. |
| B2B operating model       | McKinsey’s 2026 B2B research describes omnichannel as a minimum expectation and highlights AI, hyperpersonalisation and sales accountability as part of the new growth operating system.                                                                                                                      |
| Trust and human expertise | LinkedIn’s 2025 benchmark reports that 94% of surveyed B2B marketers see trust as key and that 78% use video. Canvas should make founders, Chartered Accountants, architects and delivery experts visible.                                                                                                    |
| Content quality           | Google explicitly prioritises helpful, reliable, people-first content and says automatically generated content must focus on accuracy, quality and relevance. Volume without genuine expertise is a risk.                                                                                                     |
| Agent architecture        | Anthropic distinguishes structured workflows from autonomous agents and recommends simple, well-tooled systems. Its evaluation guidance combines automated evals, production monitoring and periodic human review.                                                                                            |
| Technology fit            | Claude Agent SDK can run tool-using agents in Python or TypeScript; Claude sub-agents and MCP provide a practical mechanism for specialist roles and controlled access to tools and data.                                                                                                                     |

**COMMERCIAL THESIS**

# The marketing engine Canvas should build

1.  Own a narrow set of high-value decision problems: governed data
    foundations, executive decision intelligence, finance and operations
    modernisation, Power BI/Fabric value, AI grounded in trusted
    business context, and vertical analytics.

2.  Use expert-led proof as the raw material: client outcomes, delivery
    patterns, benchmarks, calculators, architecture explainers,
    executive opinions and anonymised lessons from real projects.

3.  Build account-level journeys rather than treating every lead as
    equal. Map CFO, CIO, COO, data, finance and operational stakeholders
    within target accounts.

4.  Link every campaign to a buyable next step: diagnostic, workshop,
    benchmark, ROI calculator, pilot, proof of value, platform package
    or managed service.

5.  Measure pipeline quality, buying-group engagement, stage
    progression, sales adoption and revenue influence—not merely
    impressions or posting frequency.

6.  Create a closed learning loop where every insight, asset, campaign,
    sales outcome and human edit becomes structured evidence for the
    next cycle.

**OPERATING ARCHITECTURE**

# How the full environment fits together

| **Layer**             | **Purpose**                                                                                                                                                                        |
|-----------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1\. Signal layer      | Web and competitor sources; Microsoft and technology ecosystems; industry news; social and search trends; approved meeting transcripts; CRM; proposals; campaign and website data. |
| 2\. Knowledge layer   | Versioned brand rules, offers, ICPs, personas, proof library, client permissions, source evidence, content inventory, experiment history and agent eval sets.                      |
| 3\. Command layer     | Growth Orchestrator, prioritisation, budget, governance and human approvals.                                                                                                       |
| 4\. Specialist squads | Intelligence; strategy; content; creative; distribution; demand generation; analytics; platform engineering.                                                                       |
| 5\. Execution layer   | Website/CMS, LinkedIn and social scheduler, email, CRM, events, paid-media platforms, sales enablement and partner channels.                                                       |
| 6\. Measurement layer | Power BI marketing model combining web, social, content, CRM, account, opportunity, cost and agent-performance data.                                                               |
| 7\. Learning layer    | Automated checks, production monitoring, human scoring, experiment results, prompt/process proposals and controlled releases.                                                      |

| **Core architectural rule** Do not let every agent talk to every other agent. The Orchestrator assigns a task with a typed brief; specialists return structured outputs; approval and measurement events are logged. Shared state lives in the marketing data model and knowledge base—not in uncontrolled chat history. |
|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

**MINIMUM VIABLE ORGANISATION**

# The 15 agents to deploy first

| **\#** | **Core agent**                  | **Initial responsibility**                                         |
|--------|---------------------------------|--------------------------------------------------------------------|
| 1      | Growth Orchestrator             | Own priorities, handoffs and daily operating loop.                 |
| 2      | Brand Steward                   | Protect voice, positioning and approved claims.                    |
| 3      | Evidence and Claims Guardian    | Verify facts and proof before release.                             |
| 4      | Market Intelligence Director    | Create the daily/weekly external intelligence brief.               |
| 5      | Competitor Change Monitor       | Track meaningful competitor and peer movement.                     |
| 6      | Voice-of-Customer Miner         | Extract pains, objections and language from approved interactions. |
| 7      | ICP and Account Scoring Agent   | Prioritise segments and named accounts.                            |
| 8      | Campaign Strategist             | Convert opportunities into integrated campaign plans.              |
| 9      | Research Brief Writer           | Create evidence-rich briefs for assets.                            |
| 10     | Executive/Founder Ghostwriter   | Turn genuine expert input into thought leadership.                 |
| 11     | Content Repurposing Agent       | Create channel-native derivatives from approved sources.           |
| 12     | Creative Director               | Create consistent briefs and review visual execution.              |
| 13     | Publishing and Scheduling Agent | Operate approved distribution reliably.                            |
| 14     | Measurement Dashboard Agent     | Connect activity to account, pipeline and cost.                    |
| 15     | Agent Evaluator                 | Run quality, safety, factuality and performance evaluations.       |

**CONTINUOUS ENGINE**

# The seven operating loops

| **Loop**                     | **Flow**                                                                                                                                            |
|------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| Signal-to-opportunity loop   | Continuously ingest external and internal signals → deduplicate → assess evidence and strategic fit → create opportunity/threat cards → prioritise. |
| Insight-to-content loop      | Select a validated insight → harvest SME expertise → research and verify → create master asset → editorial and brand review → approve.              |
| Content-to-distribution loop | Repurpose master asset → adapt by channel → schedule/publish → engage relevant conversations → capture target-account response.                     |
| Engagement-to-revenue loop   | Identify account/contact → enrich and score → route to sales → record action → connect to opportunity stages and outcomes.                          |
| Campaign experiment loop     | Write hypothesis → create controlled variants → launch within budget → monitor guardrails → analyse → decide scale, iterate or stop.                |
| Performance-to-learning loop | Combine channel, content, account and revenue data → diagnose drivers → update playbooks, scores and backlog.                                       |
| Agent improvement loop       | Capture human edits and production failures → add eval cases → propose prompt/tool/process change → test → approve → release with rollback.         |

**FULL FUNCTIONAL DESIGN**

# The 112-agent function register

Each row is a functional agent definition. Several rows should be
implemented as skills inside one runtime agent rather than as
permanently running services. KPIs are agent-level diagnostics;
commercial outcome metrics remain owned by the Growth Orchestrator.

## 1. Executive command, governance and control

| **ID** | **Agent / function**                            | **Mission**                                                                      | **Core tasks**                                                                                                                           | **Primary KPIs**                                                                                  | **Self-improvement method**                                                                                                        |
|--------|-------------------------------------------------|----------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| 1      | **Growth Orchestrator / Chief Marketing Agent** | Own the end-to-end marketing objective and coordinate every squad.               | Translate company goals into quarterly priorities; assign work; resolve conflicts; maintain the campaign portfolio; escalate exceptions. | Qualified pipeline created; portfolio ROI; on-time campaign completion; cross-agent failure rate. | Reweight priorities using actual pipeline contribution and post-mortems; never change strategic objectives without human approval. |
| 2      | **Strategy and Prioritisation Agent**           | Rank markets, offers, campaigns and experiments by expected value.               | Score opportunities using impact, confidence, effort, strategic fit and time-to-learning; maintain the opportunity backlog.              | Forecast accuracy; value delivered per backlog point; percentage of capacity on top priorities.   | Compare predicted versus actual impact and recalibrate scoring weights monthly.                                                    |
| 3 | **Brand Steward** | Protect Canvas voice, positioning, visual identity and category consistency. | Maintain brand rules; review messaging per option (never aggregate); detect drift; propose rule diffs as `foundation.brand_rule` option cards. | Brand adherence score; Recommendation Hit Rate on brand-rule cards; message consistency across channels. | Learn from chosen/rejected options and rejection codes (`off_brand_voice`); versioned rulebook; Pieter is the named owner by ratification, not by authorship. [v2] |
| 4      | **Evidence and Claims Guardian**                | Ensure every factual, technical and performance claim is defensible.             | Trace claims to approved evidence; label assumptions; block unsupported superlatives; maintain proof library.                            | Evidence coverage; unsupported claim rate; correction/retraction count.                           | Add failed claims to an eval set and tighten source requirements by claim risk level.                                              |
| 5 | **Legal, Privacy and Consent Gatekeeper** | Prevent unlawful, non-consensual or contractually restricted marketing actions. | Check POPIA/GDPR consent, client confidentiality, data use, scraping terms, image rights and testimonial permissions. Operates as three-tier triage via Fn 124: GREEN auto-passes under a standing permission, AMBER emits a `legal.amber` card, RED (`legal.sensitive_statement`) goes to outside counsel. | Policy violation rate; percentage of assets with permission record; triage calibration against ratifier decisions. | Update rules after counsel decisions and incidents. Outside counsel on RED is the only non-ratifier human in the system. [v2] |
| 6 | **Human Approval Router (implemented as Fn 117)** | Batch decisions into a budgeted daily digest; apply standing permissions and earned timeouts; escalate only breaches. | Apply autonomy levels; package option cards; enforce the approval budget (6 cards per working day, one 07:30 digest; non-negotiables realtime); apply standing permissions; apply *earned* timeout defaults; log decisions. Never chases a person — silence is an answer: expire, or default only where earned. | Budget adherence; queue age; Recommendation Hit Rate by function; expired-unresolved rate. | Learn routing from decisions; cannot remove mandatory gates; when the queue grows for 5 days it proposes a standing permission (Fn 118) or a volume cut, never a bigger digest. [v2] |
| 7      | **Budget and Capacity Allocator**               | Allocate people, model, media and production capacity to the best opportunities. | Set spend caps; reserve test budgets; balance evergreen and campaign work; monitor token and vendor cost.                                | Marketing efficiency ratio; spend variance; cost per accepted asset; idle/overload rate.          | Shift capacity only after statistically or commercially meaningful evidence; retain exploration budget.                            |
| 8 | **Incident and Recovery Agent (implemented as Fn 125)** | Detect, contain and draft recovery from brand, data, publishing or automation failures. | Auto-pause affected lane; suspend matching standing permissions; snapshot logs; classify failure; write the reproducing eval case; draft the correction as a `crisis.correction` card and the control change as an `incident.control_change` card with eval diff; reactivate only after the control change is chosen. | Mean time to detect; mean time to contain; repeat incident rate; recovery completeness. | Every incident becomes a regression test and a demotion event under §G2 before reactivation. Nobody coordinates; the system reports. [v2] |

## 2. Market, competitor and customer intelligence

| **ID** | **Agent / function**                               | **Mission**                                                                                       | **Core tasks**                                                                                                                                                                                                                                                                                                                               | **Primary KPIs**                                                                                                                                  | **Self-improvement method**                                                                                                              |
|--------|----------------------------------------------------|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| 9      | **Market Intelligence Director**                   | Synthesize external signals into a daily and weekly decision brief.                               | Commission research; deduplicate findings; assess confidence; convert signals into opportunities, threats and actions.                                                                                                                                                                                                                       | Actionable insight rate; executive usefulness score; source diversity; freshness SLA.                                                             | Track which insights changed decisions or produced outcomes and refine briefing criteria.                                                |
| 10     | **Competitor Discovery Crawler**                   | Continuously identify direct, adjacent and emerging competitors worldwide.                        | Search product directories, partner ecosystems, search results, social channels, tenders and industry media; maintain entity records.                                                                                                                                                                                                        | New relevant entities found; precision of competitor classification; stale-record rate.                                                           | Use reviewer corrections to improve inclusion/exclusion rules and entity matching.                                                       |
| 11     | **Competitor Change Monitor**                      | Detect material changes in competitors’ websites, hiring, messaging, products and campaigns.      | Snapshot monitored sources; detect changes; classify significance; alert only on meaningful movement.                                                                                                                                                                                                                                        | Precision of alerts; missed-change rate; alert latency; duplicate alert rate.                                                                     | Tune thresholds from false-positive and missed-event reviews.                                                                            |
| 12     | **Competitor Positioning Analyst**                 | Explain how each competitor frames value, differentiation and proof.                              | Map category language, audience, claims, use cases, proof points, objections and calls to action.                                                                                                                                                                                                                                            | Coverage of priority competitors; positioning map accuracy; strategic actions generated.                                                          | Compare analysis with sales feedback and win/loss evidence; revise competitive archetypes.                                               |
| 13     | **Competitor Content Performance Scout**           | Find competitor and peer content that earns disproportionate engagement or search visibility.     | Capture posts, formats, hooks, cadence and audience reaction; normalize performance for audience size and age.                                                                                                                                                                                                                               | High-performing patterns discovered; pattern reuse lift; false-viral rate.                                                                        | Measure Canvas experiments inspired by each pattern and retire patterns that fail repeatedly.                                            |
| 14     | **Pricing and Packaging Tracker**                  | Monitor public pricing, packaging, free trials, guarantees and commercial models.                 | Record changes; compare inclusions; flag new entry points, usage models and bundling tactics.                                                                                                                                                                                                                                                | Price coverage; change detection latency; packaging recommendations adopted.                                                                      | Validate public data with sales intelligence and label uncertain estimates.                                                              |
| 15     | **New Product and Service Scout**                  | Find global products and services that Canvas could partner with, emulate or integrate.           | Scan launches, marketplaces, funding, partner announcements and customer reviews; score strategic fit.                                                                                                                                                                                                                                       | Qualified opportunities; time from launch to detection; opportunities advanced to validation.                                                     | Back-test scoring against opportunities accepted or rejected by leadership.                                                              |
| 16     | **Microsoft, Fabric and Power BI Ecosystem Scout** | Track changes in Canvas’s core Microsoft ecosystem.                                               | Monitor product releases, partner programmes, reference architectures, certifications and go-to-market opportunities.                                                                                                                                                                                                                        | Relevant ecosystem signals; partner opportunities; content ideas generated; freshness.                                                            | Compare predictions with Microsoft announcements and sales usage; strengthen trusted-source hierarchy.                                   |
| 17     | **Adjacent Technology Scout**                      | Monitor AI agents, data platforms, automation, ERP, CRM and vertical analytics outside Microsoft. | Track Anthropic, OpenAI, Google, Databricks, Snowflake, SAP, Salesforce and specialist vendors.                                                                                                                                                                                                                                              | Strategic signal quality; integration ideas; threat/opportunity actions.                                                                          | Use technology adoption and client inquiry data to tune relevance weights.                                                               |
| 18     | **Industry Trend Analyst**                         | Translate sector developments into Canvas-specific implications.                                  | Monitor mining, logistics, manufacturing, FMCG, retail and financial services; identify data/AI decision needs.                                                                                                                                                                                                                              | Vertical insights published; target-account engagement; sales usefulness score.                                                                   | Compare predicted pain points with meetings, proposals and pipeline data.                                                                |
| 19 | **Regulatory and Policy Monitor** | Identify regulation that creates data, reporting, AI governance or automation demand. | Monitor relevant South African and international rules; summarise business impact; propose content and offers. | Material changes detected; accuracy; regulated-industry opportunities generated. | Require authoritative sources; regulatory summaries route through Fn 124 triage (AMBER by default); record interpretation changes. [v2] |
| 20     | **Tender and RFP Opportunity Scout**               | Find public and partner-led opportunities aligned to Canvas capabilities.                         | Monitor portals, partner channels and procurement notices; qualify fit, deadline, decision criteria and incumbent risk.                                                                                                                                                                                                                      | Qualified opportunities; response lead time; tender win rate; false-positive rate.                                                                | Learn from bid/no-bid and win/loss decisions; tune qualification thresholds.                                                             |
| 21     | **Event, Conference and Webinar Scout**            | Identify speaking, sponsorship, networking and content opportunities.                             | Track events, calls for speakers, webinars and awards; map target audiences and deadlines.                                                                                                                                                                                                                                                   | Relevant opportunities; speaking placements; pipeline influenced; cost per opportunity.                                                           | Score events by actual attendee quality and downstream engagement.                                                                       |
| 22     | **Partner and Channel Intelligence Agent**         | Map Microsoft partners, ERP vendors, ISVs, consultants and referral channels.                     | Identify complementary partners; track co-marketing activity; assess overlap, reach and reciprocity.                                                                                                                                                                                                                                         | Qualified partners; co-marketing launches; partner-sourced pipeline; relationship health.                                                         | Update partner scores using referrals, response times and joint outcomes.                                                                |
| 23     | **Voice-of-Customer Miner**                        | Extract recurring pains, language, objections and desired outcomes from customer interactions.    | Analyze approved meeting transcripts, CRM notes, support data, surveys and proposals; anonymize sensitive detail.                                                                                                                                                                                                                            | New validated insights; coverage of interactions; insight-to-content conversion; privacy compliance.                                              | Compare themes with sales/SME validation and update confidence as evidence accumulates.                                                  |
| 24 | **Win/Loss Analyst** | Determine why Canvas wins, loses, stalls or gets displaced. | Infer from CRM, Fireflies deal calls and email threads via Fn 120 — no interviews; separate controllable from external factors; emit a `sales.win_loss` card with three ranked, evidenced root causes; optional one-question prospect email drafted for approval. | Coverage of closed opportunities; root-cause confidence; actions implemented; win-rate improvement. | Track whether recommended changes improve subsequent cohorts. [v2] |
| 25     | **Competitive Response Strategist**                | Convert material competitor moves into fast, proportionate response decisions.                    | Rank threat severity; draft response options (public content, private sales enablement, product/offer adjustment, partner action, or deliberate non-response); maintain pre-built response playbooks for priority scenarios such as RIB BI+ or BuildSmart-native-BI announcements; brief sales within 48 hours of a confirmed material move. | Response time from confirmed move to decision brief; share of recommendations accepted; measured uplift after response; false-alarm rate.         | Review each response against outcome and over/under-reaction; refine severity thresholds and retire playbooks that consistently misfire. |
| 26     | **Client Advocacy Harvester**                      | Systematically convert delivery success into approved, reusable proof.                            | Mine approved Fireflies transcripts, delivery milestones and project close-outs for testimonial-worthy quotes, measurable before/after outcomes and case-study candidates; track client permission status; maintain the referenceability register; feed the Case Study Writer (47) and Proof Library.                                        | Approved proof assets harvested per quarter; permission coverage; proof reuse in sales and content; time from delivery milestone to usable proof. | Compare which proof types advance opportunities; prioritise harvesting toward the verticals and claims sales actually uses.              |

**FORMAT INNOVATION**

### The Canvas-ify innovation protocol

Every externally observed high-performing format, hook or advertising
pattern must pass through five steps before it enters the Canvas
repertoire. This governs the Competitor Content Performance Scout (13)
and the Experiment Designer (98).

| **Step**          | **Discipline**                                                                                                                                                                                                                                                                                                 |
|-------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1\. Scan          | Monitor LinkedIn top posts, competitor pages, ad libraries and relevant communities for anomalous success — content performing far above its account’s baseline.                                                                                                                                               |
| 2\. Deconstruct   | Isolate why it worked: hook, structure, proof density, format, timing or audience — not surface aesthetics.                                                                                                                                                                                                    |
| 3\. Canvas-ify    | Translate the mechanism into a Canvas-relevant, on-brand, on-claim version. Example: a trending “teardown” format becomes an anonymised dashboard or architecture teardown with what is wrong and how to fix it. Formats that cannot be translated without violating brand or evidence rules are dropped here. |
| 4\. Test          | Ship as one controlled, low-stakes experiment with a hypothesis and stopping rule, per the experiment loop.                                                                                                                                                                                                    |
| 5\. Scale or kill | The measurement squad decides from evidence. Winners enter the template library with their performance record; losers are archived with the reason, so they are not re-proposed.                                                                                                                               |

**Diversity check:** if the system recommends the same format three
times consecutively, it must inject one deliberate wildcard variant to
prevent convergence on a local maximum.

## 3. Segmentation, positioning and planning

| **ID** | **Agent / function**                       | **Mission**                                                                               | **Core tasks**                                                                                                           | **Primary KPIs**                                                                       | **Self-improvement method**                                                   |
|--------|--------------------------------------------|-------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| 27     | **ICP Architect**                          | Define the accounts Canvas should and should not target.                                  | Specify industry, size, systems, data maturity, pain, urgency, buying ability and disqualifiers.                         | ICP opportunity win rate; average deal quality; disqualification accuracy.             | Refit the ICP quarterly using won/lost cohorts and delivery profitability.    |
| 28     | **Buying Committee Mapper**                | Map economic buyers, champions, users, blockers and hidden decision-makers.               | Create role-specific concerns, evidence needs, channels and objections for CFO, CIO, COO, data and functional leaders.   | Contact coverage per account; multi-threaded opportunity rate; stakeholder engagement. | Learn from opportunity contact maps and stage progression.                    |
| 29     | **Persona and Jobs-to-be-Done Agent**      | Turn role and context into actionable needs and decision jobs.                            | Define triggers, anxieties, desired outcomes, alternatives, proof thresholds and language.                               | Persona validation rate; content relevance score; conversion by persona.               | Retire unsupported assumptions and incorporate interview evidence.            |
| 30     | **Account Segmentation and Scoring Agent** | Rank named accounts for ABM and outbound support.                                         | Combine fit, intent, relationship, trigger events, technology, pain and engagement into tiers.                           | Tier conversion; score calibration; target-account penetration; stale score rate.      | Back-test scores against meetings, opportunities and wins.                    |
| 31     | **Vertical Prioritisation Agent**          | Select industries and use cases that deserve dedicated campaigns.                         | Compare TAM, urgency, Canvas proof, partner access, sales cycle, margin and competitive intensity.                       | Vertical pipeline; win rate; content efficiency; forecast accuracy.                    | Update weights from campaign and delivery economics.                          |
| 32     | **Positioning and Messaging Architect**    | Create the category narrative and message hierarchy.                                      | Develop audience-specific value propositions, differentiation, proof, objections and message matrices.                   | Message comprehension; sales adoption; conversion lift; brand consistency.             | A/B test messages and incorporate sales call language and objections.         |
| 33     | **Offer and Package Designer**             | Turn capabilities into clear, buyable offers.                                             | Define diagnostic, pilot, platform, managed service and expansion packages; specify outcome, scope, proof and next step. | Offer conversion; sales cycle; gross margin; expansion rate.                           | Compare package assumptions with delivery effort and client outcomes.         |
| 34     | **Campaign Strategist**                    | Design integrated campaigns tied to a commercial objective.                               | Set audience, insight, offer, journey, content, channels, budget, experiment plan and sales handoff.                     | Qualified pipeline; target-account engagement; campaign ROI; learning velocity.        | Run campaign post-mortems and reuse only validated components.                |
| 35     | **ABM Account Planner**                    | Create one-to-one and one-to-few plans for strategic accounts.                            | Research account priorities; map stakeholders; select personalized proof and engagement plays.                           | Engaged buying groups; meetings; opportunity creation; account progression.            | Use account-level outcomes to refine trigger and content selection.           |
| 36     | **Channel Mix Planner**                    | Choose the right balance of owned, earned, partner, social, email, events and paid media. | Model reach, trust, cost, control and stage fit; define channel role and frequency.                                      | Incremental pipeline by channel; blended CAC; overlap/waste; channel saturation.       | Use incrementality tests and attribution triangulation, not last-click alone. |
| 37     | **Editorial Portfolio Planner**            | Balance evergreen, campaign, proof, executive and reactive content.                       | Maintain pillar-topic map; prevent duplication; align content to audience and funnel stage.                              | Coverage gaps; content reuse; pipeline-assisted assets; publishing consistency.        | Shift mix based on topic-level outcomes and sales demand.                     |
| 38     | **Forecast and Scenario Planner**          | Forecast reach, leads, pipeline, cost and capacity under alternative plans.               | Build base/upside/downside scenarios; expose assumptions; update with actuals.                                           | Forecast error; decision usefulness; scenario response speed.                          | Calibrate assumptions by cohort and preserve forecast history.                |

## 4. Research, content and thought leadership studio

| **ID** | **Agent / function**                         | **Mission**                                                                        | **Core tasks**                                                                                              | **Primary KPIs**                                                                          | **Self-improvement method**                                                        |
|--------|----------------------------------------------|------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| 39     | **Insight-to-Story Editor**                  | Turn raw research into a sharp, differentiated story angle.                        | Select the tension, audience, thesis, evidence, implication and call to action.                             | Brief acceptance; originality score; performance versus baseline.                         | Compare predicted hook strength with actual retention and engagement.              |
| 40 | **SME Interviewer and Knowledge Harvester — SUPERSEDED** | Superseded by Fn 113 Expertise Corpus Miner. Row retained for numbering stability. | None. No interviews are conducted in v2; the approved corpus (Fireflies transcripts under SP-001, proposals, project docs, published posts, internal threads) is mined nightly. | — | — [v2] |
| 41     | **Research Brief Writer**                    | Create evidence-rich briefs for every major asset.                                 | Define question, sources, claims, counterpoints, audience relevance and open uncertainties.                 | Source quality; evidence coverage; factual correction rate; turnaround.                   | Add reviewer corrections and weak-source patterns to the research rubric.          |
| 42     | **Long-form Article Writer**                 | Write authoritative website articles and guides.                                   | Draft clear, people-first content with expert evidence, practical frameworks and internal links.            | Qualified organic visits; engaged time; assisted conversions; editorial acceptance.       | Analyze search intent, reader behaviour and sales usage; refresh decaying content. |
| 43 | **Executive and Founder Ghostwriter** | Convert a *ratified* position into authentic long-form and short-form content in the leader's voice. | Draft only the chosen `content.founder_position` option from Fn 115, using the Fn 114 voice profile; return a `content.publish` card with two length/format variants. Every stance traces to corpus evidence or carries the visible label "New stance — you have not said this before". | Recommendation Hit Rate on founder cards (pause at < 40%); target-audience engagement; inbound conversations. | Learn from chosen/rejected options; never fabricate personal stories, opinions or numbers — choosing an evidenced option is ratification, not fabrication. [v2] |
| 44     | **LinkedIn Post Writer**                     | Create concise, credible B2B posts for leaders and the company page.               | Generate hooks, narratives, practical insights, proof and conversation prompts; vary format intentionally.  | ICP reach; meaningful comments; saves/shares; profile visits; meetings assisted.          | Learn by post archetype, audience and topic—not by copying surface-level virality. |
| 45     | **Carousel and Document Post Writer**        | Design slide-by-slide narratives for LinkedIn document posts.                      | Create title, flow, one-idea-per-page copy, proof and CTA; brief visual design.                             | Open-to-completion proxy; saves; shares; qualified clicks; reuse.                         | Compare page-level drop-off where available and refine information density.        |
| 46     | **Email and Newsletter Writer**              | Create useful email sequences and editorial newsletters.                           | Write subject lines, body, CTA and segmentation variants; control frequency and relevance.                  | Reply rate; qualified click rate; unsubscribe/spam rate; pipeline influenced.             | Use holdouts and cohort analysis; suppress fatigue-prone segments.                 |
| 47 | **Case Study Writer** | Convert delivery outcomes into credible proof assets. | Triggered by Fn 26 milestone detection (not human-initiated); structure problem, context, intervention, measurable outcome, implementation and lessons; named use only via a Fn 119 `client.permission_request` card; anonymised path needs no permission once it passes the combination test. | Approved case studies; sales usage; opportunity influence; proof completeness. | Track which proof points sales uses and which cases advance deals. [v2] |
| 48     | **White Paper and Benchmark Report Writer**  | Produce signature research that creates category authority.                        | Develop methodology, analysis, narrative, visuals, implications and lead-capture assets.                    | Downloads by ICP; citations/mentions; meetings; pipeline; data quality.                   | Review methodology and commercial outcomes; build recurring benchmark series.      |
| 49     | **Webinar, Podcast and Video Script Writer** | Create structured, natural scripts that reveal expertise.                          | Write opening, questions, narrative beats, demonstrations, proof, objections and CTA.                       | Attendance/view completion; engagement; clips generated; qualified follow-up.             | Use retention and question data to improve pacing and topic selection.             |
| 50     | **Sales Enablement Writer**                  | Create assets that help sales diagnose, explain and close.                         | Produce one-pagers, discovery guides, battlecards, objection responses, ROI stories and follow-up emails.   | Sales adoption; usage frequency; stage conversion; time saved.                            | Collect sales feedback and opportunity outcomes; retire unused assets.             |
| 51     | **SEO and AI-Answer Content Optimizer**      | Improve discoverability in search and AI answer systems without degrading quality. | Map intent; improve structure, entities, internal linking, metadata, evidence and expert attribution.       | Non-branded impressions; qualified clicks; citations/mentions; conversions; index health. | Refresh based on query gaps and content decay; enforce people-first quality rules. |
| 52     | **Content Repurposing Agent**                | Convert one high-value source into many channel-native assets.                     | Create post, carousel, clips, email, sales snippet, FAQ and landing-page derivatives without repetition.    | Assets per source; incremental reach; reuse rate; quality acceptance.                     | Measure derivative performance independently and retain best transformations.      |
| 53     | **Fact Checker and Citation Verifier**       | Independently verify claims, numbers, quotes and source recency.                   | Check primary sources; identify contradictions; label inference; confirm dates and links.                   | Factual error rate; citation accuracy; recency compliance; blocked-risk count.            | Maintain a regression set of past errors and high-risk claim types.                |
| 54     | **Editorial Quality Editor**                 | Improve clarity, logic, originality, tone and usefulness.                          | Edit structure, language, density, jargon, repetition and CTA; score against editorial rubric.              | Accepted-output rate; edit distance; readability; audience usefulness score.              | Analyze recurring defects by writer/format and prescribe targeted improvements.    |

## 5. Creative, design and digital experience

| **ID** | **Agent / function**                       | **Mission**                                                             | **Core tasks**                                                                                         | **Primary KPIs**                                                                    | **Self-improvement method**                                                   |
|--------|--------------------------------------------|-------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| 55     | **Creative Director**                      | Translate strategy into a coherent creative concept and system.         | Set visual idea, emotional tone, format, references and quality bar; review variants.                  | Creative acceptance; brand distinctiveness; campaign performance; reuse.            | Compare creative hypotheses with attention, recall and conversion data.       |
| 56     | **Visual Design Agent**                    | Produce on-brand static assets and layouts.                             | Create social graphics, diagrams, one-pagers, ads and presentation components from approved templates. | First-pass approval; production speed; brand compliance; engagement lift.           | Learn from approved design changes and maintain template constraints.         |
| 57     | **Data Visualisation Storyteller**         | Turn data into accurate, persuasive visual explanations.                | Select chart, annotation, hierarchy and narrative; prevent misleading scales or comparisons.           | Comprehension score; data accuracy; reuse in sales/content; engagement.             | Test alternative visual explanations and record common interpretation errors. |
| 58     | **Video Producer**                         | Plan video formats that can be produced consistently.                   | Create concept, shot list, interview plan, B-roll needs, location, script and distribution versions.   | Production throughput; completion rate; cost per usable minute; pipeline influence. | Use retention curves and production post-mortems to refine formats.           |
| 59     | **Video Editor and Clipper**               | Create polished long and short video assets.                            | Edit pacing, captions, hooks, proof overlays, aspect ratios and clips; maintain source traceability.   | View-through rate; hook retention; clip yield; revision rate.                       | Compare edit patterns with retention and save successful recipes.             |
| 60     | **Motion and Animation Agent**             | Explain complex data and AI concepts through motion.                    | Create lightweight animations, product flows, metric transitions and explainer sequences.              | Comprehension; completion rate; reuse; production efficiency.                       | Use audience feedback and retention to simplify or enrich sequences.          |
| 61     | **Landing Page UX Designer**               | Design low-friction, trust-rich campaign experiences.                   | Create hierarchy, proof placement, forms, navigation, mobile layout and experiment variants.           | Conversion rate; qualified conversion rate; form abandonment; page speed.           | Use analytics, recordings and heatmaps to prioritize design changes.          |
| 62     | **Brand Template Librarian**               | Maintain controlled, reusable creative systems.                         | Version templates; define locked/editable elements; archive obsolete assets; document use.             | Template adoption; off-brand asset reduction; production time saved.                | Promote high-performing templates and retire low-use or error-prone versions. |
| 63     | **Accessibility and Readability Reviewer** | Ensure assets are legible and usable across audiences and devices.      | Review contrast, font size, captions, alt text, reading order, mobile layout and plain language.       | Accessibility defect rate; mobile readability; caption/alt-text coverage.           | Add defects to checklists and automated tests; verify fixes in render.        |
| 64     | **Asset Rights and Metadata Manager**      | Control ownership, permissions, versions and discoverability of assets. | Record source, licence, client permission, expiry, tags, campaign and derivatives.                     | Rights completeness; duplicate asset rate; time to find; expired-use incidents.     | Improve taxonomy based on search failures and rights incidents.               |

## 6. Publishing, distribution, community and reputation

| **ID** | **Agent / function**                    | **Mission**                                                                      | **Core tasks**                                                                                                                                                                                 | **Primary KPIs**                                                                            | **Self-improvement method**                                                                |
|--------|-----------------------------------------|----------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| 65     | **Publishing and Scheduling Agent**     | Publish approved assets at planned times with correct metadata.                  | Load content; validate links/tags; schedule; confirm publication; retry safely; log IDs.                                                                                                       | Publishing success; schedule adherence; broken-link rate; duplicate post rate.              | Analyze operational failures and add pre-flight checks.                                    |
| 66     | **Channel Formatter**                   | Adapt approved master content to each platform’s native format.                  | Apply length, aspect ratio, link, hashtag, caption and CTA rules without changing claims.                                                                                                      | Format compliance; first-pass acceptance; channel performance versus generic copy.          | Update rules from platform changes and format experiments.                                 |
| 67     | **Social Engagement and Comment Agent** | Support timely, useful participation in relevant conversations.                  | Prioritize comments; draft expert responses; flag sales or reputation signals; avoid spam.                                                                                                     | Meaningful response rate; conversations advanced; response time; escalation accuracy.       | Learn which comment types generate profile visits, replies and meetings.                   |
| 68     | **Employee Advocacy Agent**             | Help experts and staff share useful content authentically.                       | Recommend posts, personalize optional drafts, coordinate launches and track participation.                                                                                                     | Active advocates; employee-generated reach; engagement quality; opt-out satisfaction.       | Use participation feedback; never automate posting from personal profiles without consent. |
| 69     | **Partner Co-Marketing Agent**          | Plan and execute joint content and campaigns with partners.                      | Match audiences and offers; draft joint plan; coordinate approvals; track sourced outcomes.                                                                                                    | Partner campaigns; partner-sourced pipeline; audience overlap; reciprocity.                 | Score partners by responsiveness, reach, lead quality and joint execution.                 |
| 70     | **Creator and Industry-Expert Scout**   | Identify credible niche voices for collaboration.                                | Assess expertise, audience fit, trust, conflicts, content quality and partnership models.                                                                                                      | Qualified creators; collaboration conversion; engaged ICP reach; brand safety.              | Weight fit and credibility above follower count; track commercial outcomes.                |
| 71     | **Community Listening Agent**           | Monitor relevant discussions for questions, needs, objections and opportunities. | Listen across approved public channels, including Clutch, Google Business reviews and LinkedIn recommendations for Canvas and competitors; cluster themes; alert SMEs; feed editorial backlog. | Actionable themes; response opportunities; false-alert rate; insight freshness.             | Compare detected themes with actual inquiry and pipeline data.                             |
| 72     | **Newsletter Operations Agent**         | Manage list health, segmentation, send operations and reporting.                 | Validate consent; suppress invalid/fatigued contacts; schedule; monitor deliverability and replies.                                                                                            | Deliverability; spam complaints; active subscriber rate; qualified replies.                 | Use cohort and frequency tests; clean list continuously.                                   |
| 73     | **Webinar and Event Operations Agent**  | Run registration, reminders, attendee experience and follow-up.                  | Build event page; manage invites; prepare speakers; capture questions; route follow-up.                                                                                                        | ICP registration; attendance; engagement; meetings and pipeline; operational defects.       | Score topics, speakers and channels by attendee quality and downstream outcomes.           |
| 74     | **PR and Media Pitch Agent**            | Earn credible coverage, contributed articles and expert commentary.              | Map journalists and outlets; match stories; draft tailored pitches; coordinate proof and spokespeople.                                                                                         | Relevant placements; response rate; referral traffic; pipeline influence; message accuracy. | Learn outlet interests and pitch timing; avoid mass generic outreach.                      |
| 75     | **Reputation and Crisis Monitor**       | Detect negative narratives, misinformation or sensitive issues early.            | Monitor brand mentions; assess severity and spread; gather facts; trigger response protocol.                                                                                                   | Detection latency; severity classification accuracy; response time; recurrence.             | Use incident reviews to improve thresholds and response playbooks.                         |

## 7. Demand generation, account activation and revenue support

| **ID** | **Agent / function**                         | **Mission**                                                                   | **Core tasks**                                                                                                  | **Primary KPIs**                                                                                  | **Self-improvement method**                                                                 |
|--------|----------------------------------------------|-------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| 76     | **Lead Magnet and Interactive Tool Agent**   | Create practical assets that exchange real value for attention and data.      | Design diagnostics, calculators, checklists, benchmarks and assessment tools linked to an offer.                | Qualified completion rate; meeting conversion; data completeness; pipeline per tool.              | Analyze question-level drop-off and opportunity outcomes; update assumptions.               |
| 77     | **Landing Page CRO Agent**                   | Continuously improve qualified conversion on campaign pages.                  | Audit message match, proof, CTA, forms, friction and mobile performance; propose tests.                         | Qualified conversion rate; bounce/engagement; form completion; experiment win rate.               | Prioritize changes using funnel analytics, Clarity evidence and test results.               |
| 78     | **Paid Media Strategist**                    | Define paid media role, audiences, objectives and budget guardrails.          | Select channels; map campaign structure; set exclusions, bids, creative needs and measurement.                  | Qualified pipeline; CAC; target-account reach; incremental lift.                                  | Use holdouts, cohort quality and sales feedback; stop spend that only produces cheap leads. |
| 79     | **Ad Copy and Creative Variant Agent**       | Generate disciplined variants tied to specific hypotheses.                    | Vary hook, proof, audience, format and CTA one dimension at a time; maintain claim controls.                    | CTR and qualified CTR; conversion; creative fatigue; compliance.                                  | Promote winning components through controlled tests, not uncontrolled remixing.             |
| 80     | **Media Buying and Bid Optimizer**           | Execute budget and bid changes within approved limits.                        | Monitor pacing, frequency, placement, audience quality and conversion; adjust or pause.                         | Spend variance; qualified CPA; frequency; wasted spend; policy violations.                        | Use bounded rules and require approval above budget or strategy thresholds.                 |
| 81     | **Retargeting Agent**                        | Sequence useful follow-up based on demonstrated interest.                     | Create audience windows; suppress customers/poor fits; select stage-appropriate proof and frequency.            | Assisted conversion; frequency; cost per qualified return; opt-out rate.                          | Compare sequence and window performance; prevent overexposure.                              |
| 82     | **ABM Activation Agent**                     | Coordinate personalized touches across a named account and buying group.      | Trigger ads, content, outreach support, event invites and executive engagement from account signals.            | Buying-group engagement; meetings; opportunity creation; progression; cost per account.           | Use account post-mortems to refine play selection and thresholds.                           |
| 83     | **Lead Capture and Form Optimisation Agent** | Capture enough information for action without unnecessary friction.           | Choose fields, progressive profiling, validation, routing and consent language.                                 | Form completion; data quality; enrichment success; qualified lead rate.                           | Test field value versus abandonment and remove low-value questions.                         |
| 84     | **Lead Enrichment and Routing Agent**        | Enrich and route leads to the right owner with context and urgency.           | Match account; append firmographic/technology data; score; deduplicate; create CRM task and brief.              | Match accuracy; routing SLA; duplicate rate; sales acceptance.                                    | Learn from sales rejection reasons and corrected account/contact matches.                   |
| 85     | **Nurture Sequence Agent**                   | Move relevant prospects toward a useful next step over time.                  | Select content by role, problem, stage and behaviour; set cadence; stop on sales activity.                      | Progression; qualified reply; unsubscribe; opportunity influence; time to next step.              | Use cohorts and holdouts; reduce frequency where fatigue appears.                           |
| 86     | **Website Personalisation Agent**            | Adapt proof, use cases and CTAs to known audience context.                    | Choose approved variants by industry, role, source, account tier or behaviour.                                  | Qualified conversion lift; engagement; error/mismatch rate; privacy compliance.                   | Test personalization against a generic control and remove weak rules.                       |
| 87 | **Sales Handoff and SLA Agent** | Ensure marketing signals become timely, contextual sales action. | Package reason-to-contact, content consumed, stakeholders, pain and recommended next step; acceptance and stage are *inferred* by Fn 120 from CRM, calendar, Fireflies and email and confirmed by one tap — sales never authors a reason. | Time to follow-up; inferred-vs-confirmed agreement; meeting conversion; leakage rate. | Analyse rejected or ignored handoffs and improve thresholds/context. [v2] |
| 88     | **Proposal and RFP Support Agent**           | Reuse approved marketing proof and research in sales documents.               | Retrieve relevant cases, differentiators, team credentials, architecture and objections; tailor to requirement. | Response time saved; compliance completeness; reuse accuracy; win influence.                      | Capture evaluator feedback and winning language into approved libraries.                    |
| 89     | **Revenue Attribution Agent**                | Connect marketing activity to account, opportunity, revenue and gross profit. | Unify campaign, content, CRM, web and sales-touch data; report multiple attribution views.                      | Coverage of opportunity journeys; reconciliation accuracy; decision usage; unassigned touch rate. | Triangulate attribution with experiments, sales evidence and account timelines.             |

## 8. Measurement, experimentation and continuous learning

| **ID** | **Agent / function**                           | **Mission**                                                                   | **Core tasks**                                                                                            | **Primary KPIs**                                                                             | **Self-improvement method**                                                           |
|--------|------------------------------------------------|-------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| 90     | **Measurement Architect**                      | Define the metric tree, event taxonomy, targets and decision rules.           | Map north-star, leading, diagnostic and guardrail metrics; specify formulas and owners.                   | Metric coverage; definition disputes; decision adoption; instrumentation completeness.       | Review metrics when they fail to predict commercial outcomes or invite gaming.        |
| 91     | **Marketing Data Engineer**                    | Build reliable pipelines from channels, website, CRM and content systems.     | Ingest, normalize, model, document and monitor data; preserve lineage and consent.                        | Pipeline reliability; freshness; cost; schema break recovery; lineage coverage.              | Automate tests and add monitoring after every failure or source change.               |
| 92     | **Marketing Dashboard and BI Agent**           | Deliver role-specific, decision-ready dashboards in Power BI.                 | Build executive, campaign, content, funnel and agent-performance views with drill-through.                | Active usage; time-to-insight; reconciled metrics; decision actions logged.                  | Use query and usage logs to simplify dashboards and add missing decisions.            |
| 93     | **Data Quality and Identity Resolution Agent** | Maintain trustworthy account, contact, campaign and content identities.       | Deduplicate; resolve domains and names; validate UTMs; flag missing or impossible values.                 | Duplicate rate; match accuracy; UTM completeness; data-quality incidents.                    | Learn from corrected matches and create deterministic rules before model inference.   |
| 94     | **Content Performance Analyst**                | Explain which content creates attention, trust and commercial movement.       | Analyze by topic, format, audience, stage, author, proof type and distribution.                           | Actionable recommendations; content-assisted pipeline; prediction accuracy; decay detection. | Test recommendations and track whether changes outperform prior baselines.            |
| 95     | **SEO and Search Performance Analyst**         | Diagnose organic visibility, query demand and technical issues.               | Analyze Search Console, rankings, indexation, content gaps, CTR and conversions.                          | Qualified organic pipeline; non-brand visibility; issue resolution; content refresh lift.    | Maintain query clusters and compare refresh actions with controlled pre/post windows. |
| 96     | **Social Performance Analyst**                 | Evaluate social activity beyond vanity metrics.                               | Measure ICP reach, quality engagement, follower relevance, clicks, profile actions and assisted pipeline. | Qualified engagement rate; audience quality; post archetype lift; attribution coverage.      | Normalize by audience size and format; use matched baselines.                         |
| 97     | **Funnel and Conversion Analyst**              | Find leakage and friction from first touch to revenue.                        | Map stage conversion, velocity, source, cohort, role and account tier; identify bottlenecks.              | Leakage identified/resolved; stage conversion; velocity; forecast accuracy.                  | Validate suspected causes with qualitative evidence and experiments.                  |
| 98     | **Experiment Designer**                        | Turn ideas into testable, decision-ready experiments.                         | Write hypothesis, primary metric, guardrails, sample, duration, variant and stopping rule.                | Valid experiments launched; decision rate; test velocity; contamination rate.                | Audit failed tests for design flaws and update templates.                             |
| 99     | **Experiment Statistician**                    | Analyze results without overclaiming weak evidence.                           | Check sample, variance, significance/credible interval, practical effect and segmentation.                | Analysis accuracy; false discovery rate; decision confidence; reproducibility.               | Calibrate methods using simulations and retrospective outcome checks.                 |
| 100    | **Anomaly Detection Agent**                    | Detect unexpected changes in traffic, spend, conversion, data or reputation.  | Monitor baselines; distinguish real shifts from seasonality or tracking failures; route alerts.           | Precision/recall; alert latency; time to resolution; alert fatigue.                          | Tune models using incident labels and source-specific seasonality.                    |
| 101 | **Agent Evaluator (extended by Fn 126, Fn 127)** | Score agent outputs and trajectories against objective rubrics and test sets. | Run automated checks, model graders and deterministic tests; score every function on decision telemetry (Recommendation Hit Rate, Rejection-All Rate, distinctness, evidence coverage); consume eval sets generated by Fn 127 and ratified by acceptance sampling. No sampled human review of individual outputs. | Eval coverage; defect escape rate; grader agreement with ratifier decisions; regression detection. | Expand eval sets from production failures and rejection codes; recalibrate graders quarterly against downstream outcomes. [v2] |
| 102 | **Prompt and Process Improvement Coach** | Propose safe improvements to prompts, tools and workflows. | Diagnose recurring failures from rejection-code histograms and eval regressions; draft changes; run A/B evals; document trade-offs; emit a `system.prompt_change` card whose payload is the diff and the eval comparison. | Measured quality lift; cost reduction; regression rate; adoption. | No direct self-modification in production: changes pass eval and a ratified card. [v2] |

## 9. Agent platform, knowledge and engineering

| **ID** | **Agent / function**                     | **Mission**                                                                    | **Core tasks**                                                                                               | **Primary KPIs**                                                                        | **Self-improvement method**                                                            |
|--------|------------------------------------------|--------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| 103    | **Agent Platform Architect**             | Design the runtime, orchestration, state, permissions and deployment model.    | Separate deterministic workflow from judgment; define services, queues, storage, APIs and failure modes.     | Reliability; scalability; change lead time; architecture exceptions.                    | Use incident and cost evidence to simplify architecture and reduce unnecessary agents. |
| 104    | **Workflow Automation Engineer**         | Implement deterministic triggers, routing and integrations.                    | Build n8n/Power Automate workflows; add retries, idempotency, approval steps and audit logs.                 | Workflow success; manual hours saved; recovery time; duplicate-action rate.             | Convert repeated manual fixes into tests and robust workflow patterns.                 |
| 105    | **MCP and Integration Engineer**         | Connect Claude agents to approved tools and data using controlled interfaces.  | Develop and maintain MCP servers/connectors; scope permissions; document tools; test schemas.                | Tool-call success; permission incidents; integration coverage; schema break recovery.   | Improve tool descriptions from agent errors and remove unnecessary tool scope.         |
| 106    | **Knowledge Base Librarian**             | Maintain the trusted source of truth for brand, offers, evidence and learning. | Ingest approved documents; tag, version, expire, deduplicate and manage authority levels.                    | Retrieval precision; stale content rate; source coverage; time to find.                 | Use failed retrievals and user feedback to improve taxonomy and chunking.              |
| 107    | **Prompt and Skill Engineer**            | Create reusable, versioned agent instructions and output schemas.              | Define role, context, steps, tools, constraints, examples, rubric and error handling.                        | Task success; prompt regression rate; reuse; token efficiency.                          | Run evals before release and maintain changelog with rollback.                         |
| 108    | **Context and Memory Manager**           | Provide each task with the right context while preventing leakage and bloat.   | Select memories; summarize histories; enforce tenant/client boundaries; manage retention.                    | Context relevance; leakage incidents; token overhead; retrieval accuracy.               | Analyze missing and excessive context cases; tune selection rules.                     |
| 109    | **Model Router and Cost Optimizer**      | Use the least expensive model and tool path that meets the quality bar.        | Route simple extraction, drafting, analysis and high-risk review appropriately; cache and batch.             | Cost per accepted outcome; quality by route; latency; escalation rate.                  | Run periodic route comparisons and adjust only after eval evidence.                    |
| 110    | **Observability and Logging Agent**      | Make every important agent decision traceable.                                 | Capture prompts, tool calls, sources, versions, approvals, costs, errors and outcomes with privacy controls. | Trace coverage; mean time to diagnose; logging cost; sensitive-data exposure.           | Use investigations to add missing spans and reduce noisy logs.                         |
| 111    | **Security and Access Control Agent**    | Enforce least privilege, secret handling and environment separation.           | Manage identities, roles, credentials, data scopes, network rules and approval boundaries.                   | Unauthorized access attempts; secret exposure; permission review completion; incidents. | Tighten controls after audits and remove unused access automatically with approval.    |
| 112 | **Test and Evaluation Harness Engineer** | Build repeatable unit, integration, scenario, safety and regression tests. | Maintain the harness; gold datasets are generated by Fn 127 from production failures, decision pairs, rubric mutation and adversarial (round-21 injection) patterns, and ratified as *sets* by 5–10-case sampling — no human authors cases. | Test coverage; escaped defects; release confidence; time to regression detection. | Add every significant production failure to the harness automatically via Fn 125. [v2] |


## 10. Ratification and earn-in functions [v2]

IDs 113–127 are **provisional**: final IDs are assigned from `docs/function-register.md` in the same commit that lands this document. Each function's prompt, output schema and manifest exist in `cmos-autonomy-extension/functions/`. Every function here emits an OptionCard (§C1) or a record consumed by one; none writes to `docs/permission-register.yaml`.

| **ID** | **Agent / function** | **Mission** | **Core tasks** | **Primary KPIs** | **Self-improvement method** |
|---|---|---|---|---|---|
| 113 | **Expertise Corpus Miner** | Replace SME interviews: mine what Canvas people have already said and written. | Nightly scan of Fireflies (SP-001), proposals, project docs, published posts, internal threads; extract expertise atoms (opinion, framework, example, number, caveat, objection, phrasing) with speaker, source, confidentiality tag; dedupe by meaning. | Atoms per source; reuse rate downstream; corpus freshness; coverage alarm (7 days of zero delta). | Retire atoms never reused; tune extraction from Fn 115 novel-stance rate. |
| 114 | **Executive Voice Model** | Versioned profile of how a leader actually writes and what he has actually argued. | Weekly rebuild: voice traits with verbatim exemplars; positions ledger (observed/ratified/contradicted/retired) with evidence; drift check — blocks its own release above threshold and raises a `system.prompt_change` card. | Drift score; ghostwriter Recommendation Hit Rate; exemplar coverage. | Ratified positions enter the ledger; contradicted ones are flagged, never silently overwritten. |
| 115 | **Position Proposer** | Replace "Pieter supplies his opinion": three evidenced candidate stances per founder piece. | `content.founder_position` card; options differ on a declared axis (contrarian/consensus, economics/technical, CFO/IT); each cites corpus atoms or is labelled novel; recommendation with rationale. | Hit Rate (pause < 40%); novel-stance share; downstream engagement of chosen stance. | Learn from rejection codes; a thin corpus on a topic is a Fn 113 signal, not a licence to invent. |
| 116 | **Options Composer** | Universal adapter: turn any single draft into a compliant OptionCard. | Wrap the six Wednesday drafting handlers; generate two alternates on one axis each; run Brand Steward, Fact Check and Legal Triage per option; drop failures; set kind/tier/level/default from policy; declare register rows. | Distinctness pass rate; evidence coverage; first-pass card validity. | Rejection code `options_not_distinct` feeds Fn 102 directly. |
| 117 | **Approval Inbox Router** | Replace the marketing operator and "chase approvals" (Fn 6 implementation). | 07:15 daily: apply standing permissions, apply earned timeouts, rank by expected value, cap at budget, render one Teams digest, escalate non-negotiables realtime. | Budget adherence; queue age; expired-unresolved rate. | Persistent overflow triggers a Fn 118 proposal or a volume cut. |
| 118 | **Standing-Permission Learner** | Shrink approval load over time; proposes only. | Weekly: groups with ≥ 20 decisions, Hit Rate ≥ 0.85, zero rejected_all → draft a StandingPermission (§C2) as a `system.standing_permission` card (grant / narrower / decline). Seeds SP-001–004. Never touches a non-negotiable kind. | Permissions active; approvals retired per month; suspensions. | Suspended permissions on any guardrail breach; review_by ≤ 90 days. |
| 119 | **Client Permission Agent** | Make client consent a one-click decision; never writes the register. | On Fn 26/47 trigger: check register (read-only); draft request in leader's voice; card with named-case / logo+quote / anonymised-only; on choice, send via Outlook; a human updates the register on reply. | Permission coverage; time from milestone to usable proof; anonymised-path share. | Learn which asks clients grant; prefer anonymised where the combination test passes. |
| 120 | **Sales Outcome Inferencer** | Replace "sales provides acceptance reasons and outcome data". | Daily: infer accept/reject/stage/reason from CRM, calendar, Fireflies, email with evidence; one batched `sales.outcome_confirm` card (confirm all / confirm except ticked / reset); `sales.win_loss` cards on close. Inferred values tagged until confirmed; never reported as north-star. | Inferred-vs-confirmed agreement; confirmation latency; coverage. | Corrections retrain inference thresholds. |
| 121 | **Visual Asset Composer** | Replace designers and the creative reviewer for routine assets. | Compose three on-template variants from the locked library (lockups, device mockups, anonymised Power BI screenshots); deterministic checks (palette, clear-space, type size, contrast, alt text, OCR identifiability); `content.visual_variant` card; Canva Bulk Create CSV where a template exists. No generative imagery. | First-pass check pass rate; Hit Rate; production time. | Failed checks become template constraints. |
| 122 | **Foundation Drafter** | Replace Phase 0 human authoring. | Once, then quarterly: ICP list (three cuts), brand constitution (consolidated from positioning docs), metric definitions (with the two ratification metrics), quarterly objectives (three scenarios from Fn 38), approver map — each as a `foundation.*` card citing the project doc it was built from. | Cards chosen; time to activate options loop. | Quarterly refit from won/lost cohorts. |
| 123 | **Video Capture Kit** | Reduce the irreducible on-camera input to two minutes a week. | Dormant until D1 is ratified. Then: `content.video_topic` card (three topics, 90-s script, teleprompter text, shot note); calendar hold; on upload, captions, clips, derivatives, one `content.publish` card. Never a synthetic avatar or voice. | Recordings per month; derivative yield; retention. | Retire if D1 resolves to carousels. |
| 124 | **Legal Triage** | Replace "human legal review for ambiguous cases". | GREEN/AMBER/RED classification of every option payload before a card is emitted; AMBER card with drafted softening; RED to outside counsel only. Tier and rule logged on the card. | Calibration vs ratifier decisions; RED share; counsel turnaround. | Recalibrate from decisions on AMBER cards. |
| 125 | **Incident Autopilot** | Replace human incident coordination (Fn 8 implementation). | Contain (pause lane, suspend permissions, snapshot); diagnose and write the reproducing eval; `crisis.correction` and `incident.control_change` cards; reactivate after choice + passing eval; post-mortem filed to the project. | MTTD; MTTC; repeat rate. | Every incident is a demotion event under §G2. |
| 126 | **Decision-Quality Evaluator** | Replace human edits and sampled review as the learning signal. | Nightly per-function: Recommendation Hit Rate, Rejection-All Rate, distinctness, evidence coverage, timeout share, rejection-code histogram; route codes to Fn 102/114/48; monthly `system.autonomy_level_change` cards per §G2; four-lens scorecard to Power BI; quarterly hindsight calibration of recommendations. | Metric coverage; promotion/demotion accuracy in hindsight. | If the recommended option underperforms the chosen alternative in ≥ 30% of sampled cases, fix the recommender, not the ratifier. |
| 128 | **Source Lifecycle Manager** | Make signal sources a governed decision with a lifecycle; absorbs the build-discovered `17-source-scout`. | Weekly `source.promote` card per under-served scan profile (three probed candidates, evidence: reachability, freshness, allowlist, 7-day signal yield, duplicate rate); chosen URL lands via PR, never hand edit; nightly yield scoring per source; monthly `source.retire` card with replacement; SP-005 auto-approves on-allowlist high-yield candidates once earned. Built after Stage 0's hand-seed, not instead of it. | Profiles with >= 3 live sources; dead-letter share from `scan_profile_not_configured`; yield per source; Hit Rate on source cards. | Retired sources and rejected candidates tune the probe scoring. |
| 127 | **Eval Generator** | Remove the ~380-task eval-authoring bottleneck. | Generate cases from production failures, chosen-vs-rejected pairs, rubric mutation (one dimension at a time) and adversarial injection patterns; `system.prompt_change` card (full set / 20-case sample / hold); humans ratify by grading 5–10 sampled verdicts, ≥ 0.8 agreement or regenerate. No real client names even in negatives. | Cases generated; set acceptance rate; escaped defects after activation. | Disagreements seed the next generation. |

## 11. Continuous Discovery [v3]

**Principle.** A signal source is a decision with a lifecycle — discovered, probed, ratified, scored, retired — never a line in a config file. The whole chapter exists because the daily loop is only as good as its sources, and in September 2026 nine of its twelve scan profiles had none.

### 11.1 Reach — what the system may read

| Channel | Role | Governance |
|---|---|---|
| Claude native web research | Breadth-first discovery and probing inside the allowlist | Existing `mcp-web`; allowlist per 11.4 |
| Semrush MCP | Competitor, organic, keyword, backlink and traffic reports daily; the highest-authority discovery input for competitors and category language | Existing connector; report quota tracked by Fn 129 |
| **One discovery API** (search/answer class — Tavily, Perplexity or SerpAPI; pick one in PR 5c, record in `docs/accepted-risks.md`) | Query-driven discovery across the open web for every signal class | Read-only; daily cost cap in ZAR; kill switch; results are *candidates*, never evidence until probed |
| **One crawler** (Apify or Crawl4AI class; pick one) | Fetching and change-snapshotting of ratified sources and of candidate pages during probe | robots.txt and ToS respected by construction; rate-limited per domain; no authenticated pages; no personal-data harvesting; PII scrub on ingest |

The v1 exclusion of these tools is amended, not deleted: exactly one of each, behind Fn 129, cost-capped, with the exclusion's original reasons (ToS risk, maintenance burden) converted into enforced controls. Adding a second API or crawler is a `system.prompt_change`-tier decision.

**Every fetched or scraped byte is untrusted data.** It is never an instruction. The round-21 injection arrived through text a session read; scraped content is the same vector at scale. Fn 129 strips instruction-shaped content, tags provenance, and the Fn 112 harness carries adversarial cases for it.

### 11.2 Cadence and depth — per signal class

Discovery runs **daily for every class** (ruling). Depth varies so the daily run stays inside cost and the digest stays inside the approval budget.

| Signal class (v1 §2 functions) | Daily discovery query set | Probe window | Card batching |
|---|---|---|---|
| Competitors (10–14, 25) | Semrush competitor/organic deltas; discovery API on named competitors + category terms; crawler snapshots of ratified competitor pages | 3 days | One `source.promote` card per class per day, top 3 candidates |
| Microsoft / Fabric / Power BI ecosystem (16) | Discovery API on release notes, partner programme, roadmap terms; crawler on ratified MS feeds | 3 days | Same |
| Adjacent technology (17), industry trends (18), regulation (19) | Discovery API on class term-sets; Semrush keyword deltas | 7 days | Same |
| Tenders (20), events (21), partners (22) | Discovery API on portals/listings; crawler on ratified portals | 7 days | Same |
| Reputation / community (71, 75) | Discovery API brand + competitor mentions; review sites | 1 day | Realtime only if severity ≥ high (Fn 75 path); else digest |

A *candidate* becomes a *source* only via a chosen `source.promote` option or SP-005 auto-approval. A *source* stays a source only while its nightly yield holds.

### 11.3 Functions

| **ID** | **Agent / function** | **Mission** | **Core tasks** | **Primary KPIs** | **Self-improvement method** |
|---|---|---|---|---|---|
| 128 | **Source Discovery & Lifecycle Manager** (rewritten discovery-first; absorbs build-discovered `17-source-scout`) | Discover, probe, ratify, score and retire signal sources daily. | Daily per class: run the 11.2 query set; dedupe candidates against live sources and the retired list; probe (reachability, freshness, robots/ToS, allowlist status, signals yielded in window, duplicate rate, forecast yield); emit one `source.promote` card per class per day (3 candidates, declared axis, evidence = probe results, recommendation); chosen or SP-005-approved URL lands on the scan profile via the existing PR path. Nightly: yield row per live source (signals → cards → chosen; cost per chosen card; failures). Monthly: `source.retire` card per under-performing source with replacement on the same card; no default. Hand-seeded Stage 0 sources are `provisional` and retire unless re-ratified within 30 days. | Live sources per profile (≥ 3); time-to-detection (target ≤ 24 h, blueprint starter); novelty score (≥ 25%); yield per source; `scan_profile_not_configured` = 0; Hit Rate on source cards. | Rejected candidates and retired sources retrain the probe scoring; class query-sets are versioned and A/B'd via Fn 127 evals. |
| 129 | **Web Reach Governor** | Own the discovery API, the crawler and the egress allowlist so that daily, open-web discovery is lawful, bounded and cannot become an instruction channel. | Enforce per-domain rate limits, robots.txt/ToS, no authenticated or paywalled pages, PII scrub; daily ZAR cost cap per tool with kill switch; provenance tag on every fetched item; instruction-shaped content stripped and logged. Evaluate the **allowlist rule** (11.4) for every off-allowlist candidate: rule-pass → auto-widen under SP-006 and log; rule-fail → `source.allowlist` card (realtime, no default). Monthly allowlist review card: domains with zero yield in 60 days proposed for removal. | Allowlist size and churn; auto-allow precision (share later retired or reverted); ToS/robots incidents = 0; injection cases caught; cost vs cap. | Every reverted auto-allow tightens the rule; every incident adds a harness case (Fn 112). |

### 11.4 Allowlist rule — auto-widen for reputable domains (ruling)

Deterministic, versioned in `policies/allowlist-rule.yaml`, evaluated by Fn 129, enacted under standing permission **SP-006**:

- **Pass (auto-allow, logged, revocable):** domain has been resolvable ≥ 12 months (RDAP/WHOIS age); serves `robots.txt` that permits the paths probed; no `noai`/`noarchive` directive; HTTPS with valid certificate; not on any deny list in `policies/allowlist-deny.yaml`; not a domain in `docs/permission-register.yaml` (client domains are never crawled for marketing intelligence); not a social platform's authenticated surface; not a personal-data-heavy category (job boards, people directories, forums requiring login); probe yielded ≥ 1 signal with < 20% duplicates.
- **Fail → `source.allowlist` card** at the security tier: realtime, never defaults, never covered by any standing permission; card carries the rule verdict per criterion so the ratifier sees *why* it failed.
- **Hard exclusions, never auto-allowed and never carded:** client domains; competitor *login* surfaces; anything the crawler's ToS check flags as prohibited.
- **Reversibility:** every auto-allowed domain carries `allowed_by: SP-006`, `allowed_at`, `review_by` (60 days). Reverting is one line; Fn 129's monthly review card proposes the reverts.

### 11.5 Budgets — keeping daily discovery inside the approval budget

Daily discovery across all classes generates up to 8 candidate cards a day before anything else. The approval budget is 6. So:

- Cards resolved by **SP-005** (on-allowlist, forecast yield above floor) and **SP-006** (allowlist rule pass) never reach the digest; they appear as one summary line.
- At most **2 discovery cards per day** enter the digest, ranked by forecast yield; the rest queue and expire after 5 days (a queued candidate is re-discovered tomorrow if it is still relevant, so nothing is lost).
- A separate **discovery spend budget** (ZAR/day, split API vs crawler vs model) sits in `policies/discovery-budget.yaml`; Fn 129 stops the run at cap and reports.
- If discovery cards are auto-resolved at < 70% for 14 days, Fn 118 must propose a tighter SP-005/006 or Fn 128 must cut the query set — the digest never grows.

### 11.6 Card kinds, seeds, earn-in

- Kinds added to the contract: `source.promote` (L2, upstream, digest), `source.retire` (L2, no default ever), `source.allowlist` (security tier, realtime, no default, no standing permission).
- Seeds: **SP-005** auto-approve recommended `source.promote` when on-allowlist and forecast yield ≥ floor; **SP-006** auto-widen allowlist on 11.4 rule pass. Both proposed by Fn 118 in the first week after PR 5c; both carry `suspend_if` on any ToS/robots incident or injection catch.
- Earn-in: Fn 128 and 129 are upstream (`mine`); start L2, ceiling L4 per §G2. Neither ever publishes or claims.

### 11.7 What would embarrass the brand

Crawling a client's site for marketing intelligence (blocked by 11.4 hard exclusion, tested); a scraped competitor page carrying planted instructions that reach a drafting prompt (Fn 129 strip + harness case); a discovered "statistic" cited as proof without a resolvable evidence ref (§C3 already rejects it — discovery output is *candidate*, never evidence, until probed and stored as an atom); the allowlist growing past what anyone reviews (60-day `review_by` and the monthly card).

**MEASUREMENT SYSTEM**

# KPI hierarchy and scorecards

| **Do not optimise vanity metrics** Impressions, followers, clicks and raw leads are useful only when they help explain movement in qualified target-account engagement, accepted opportunities, pipeline velocity, win rate, revenue or gross profit. |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

| **Level**           | **Metric family**                                                                                                             | **Definition / measurement**                                                                                            |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| North star          | ICP Qualified Pipeline Efficiency                                                                                             | Σ (opportunity value × probability × expected gross margin × agreed marketing influence weight) ÷ total marketing cost. |
| Commercial outcomes | Marketing-sourced pipeline; marketing-influenced gross profit; win rate; deal size; sales-cycle length; expansion pipeline    | Measured in CRM with opportunity/contact/account linkage and agreed sourcing/influence rules.                           |
| Account outcomes    | Engaged target accounts; engaged buying groups; stakeholder coverage; tier progression                                        | Account-level model combining known contacts, anonymous company signals where lawful, content and sales activity.       |
| Demand outcomes     | Sales-accepted leads; meetings; opportunity creation; qualified conversion rate                                               | Require explicit acceptance/rejection reason from sales.                                                                |
| Trust and proof     | Case-study usage; executive content engagement; branded search; direct traffic; testimonials; partner/press mentions          | Use trend and cohort diagnostics; never pretend a single metric measures trust perfectly.                               |
| Content outcomes    | Qualified organic visits; saves/shares; meaningful comments; content-assisted opportunities; sales reuse                      | Tag every asset to audience, problem, stage, format, author and campaign.                                               |
| Efficiency          | Cost per accepted asset; cost per engaged account; CAC; model/tool cost; human hours saved                                    | Include internal production cost and AI/vendor cost.                                                                    |
| Guardrails          | Spam complaints; unsubscribe; privacy incidents; unsupported claims; off-brand publication; broken links; excessive frequency | Any critical guardrail breach can pause automation regardless of performance.                                           |

**AGENT SCORE**

## How every agent measures itself

| **Weight** | **Component**           | **How to calculate**                                                                                                       |
|------------|-------------------------|----------------------------------------------------------------------------------------------------------------------------|
| 30%        | Outcome contribution    | Did the output contribute to the task’s chosen business or learning outcome? Use direct attribution only where defensible. |
| 20%        | Quality rubric          | Accuracy, relevance, completeness, originality, clarity and fit for audience.                                              |
| 15%        | Acceptance              | Recommendation Hit Rate (chosen == recommended) and Rejection-All Rate from option-card decisions. Human edit distance is retired: humans choose, they do not edit. [v2] |
| 10%        | Evidence and compliance | Source quality, citation/permission coverage, brand and policy adherence.                                                  |
| 10%        | Reliability and SLA     | Task completion, latency, tool success, retry and failure rate.                                                            |
| 10%        | Cost efficiency         | Model, tool and human-review cost per accepted outcome.                                                                    |
| 5%         | Learning behaviour      | Did the agent capture feedback, update eval cases and produce a tested improvement proposal?                               |

**Core self-evaluation metrics:** Accepted Output Rate = accepted outputs ÷ total outputs; Evidence Coverage = verifiable claims with *resolvable* approved evidence ÷ all verifiable claims; **Recommendation Hit Rate** = decisions where the chosen option was the recommended one ÷ chosen decisions; **Rejection-All Rate** = rejected_all ÷ all decisions; Cost per Accepted Output = model + tool + review cost ÷ accepted outputs; Learning Adoption Rate = validated improvements deployed ÷ improvements proposed. [v2: Human Edit Rate retired]
outputs ÷ total outputs; Evidence Coverage = externally verifiable
claims with approved evidence ÷ all verifiable claims; Human Edit Rate =
materially changed sentences ÷ total sentences; Cost per Accepted Output
= model + tool + review cost ÷ accepted outputs; Learning Adoption Rate
= validated improvements deployed ÷ improvements proposed.

**MANAGEMENT VIEW**

## The four-lens monthly scorecard

The weighted score above is the Agent Evaluator’s detailed instrument.
For monthly management review, every agent reports against four simple
lenses, each summarised as improving, flat or declining with one
supporting number:

| **Lens**   | **Question**                                | **Example measures**                                                               |
|------------|---------------------------------------------|------------------------------------------------------------------------------------|
| Throughput | How much did it produce?                    | Accepted outputs; briefs delivered; assets shipped on time.                        |
| Quality    | How usable and on-brand was it?             | First-pass acceptance rate; human edit rate; guardrail breaches.                   |
| Impact     | Did it move a commercial or account metric? | Content-assisted opportunities; engaged target accounts; meetings influenced.      |
| Learning   | Did it get measurably better?               | Validated improvements deployed; regression-test additions; error recurrence rate. |

## Starter targets for the first two quarters

These are the initial, deliberately conservative targets; the
Measurement Architect recalibrates them quarterly from actuals.

| **Metric**                                                                                   | **Starting target**                                           |
|----------------------------------------------------------------------------------------------|---------------------------------------------------------------|
| Signal Quality — share of intelligence alerts leading to a logged strategic action           | Above 30%.                                                    |
| Time-to-Detection — priority competitor or ecosystem move to internal alert                  | Within 24 hours; within hours for named priority competitors. |
| Novelty Score — share of intelligence insights not available in an obvious first-page search | Above 25%.                                                    |
| First-pass acceptance — Recommendation Hit Rate on option cards                              | 50% initially; ≥ 60% by end of quarter two; Rejection-All ≤ 15%. [v2] |
| Published unsupported claims                                                                 | Zero. Any breach pauses the publishing lane pending review.   |
| Publishing reliability — approved assets published on schedule                               | Above 95%.                                                    |

**CONTROLLED IMPROVEMENT**

## The agent self-improvement protocol

1.  Log the task brief, context, prompt/skill version, tools, sources,
    output, approvals, edits, cost and downstream outcome.

2.  Classify failure: wrong objective, missing context, weak source,
    reasoning defect, tool defect, format defect, policy defect,
    coordination defect or measurement defect.

3.  Create or update an evaluation case that reproduces the failure.
    Significant production failures must become permanent regression
    tests.

4.  Propose the smallest change to prompt, skill, tool description,
    workflow or data—not a broad autonomous rewrite.

5.  Run the old and proposed versions against the relevant eval suite
    and compare quality, safety, latency and cost.

6.  Require approval for production changes, release with a version and
    rollback path, and monitor the new version in production.

**DATA FOUNDATION**

# The shared marketing object model

| **Object**             | **Minimum fields**                                                                                  |
|------------------------|-----------------------------------------------------------------------------------------------------|
| Source                 | URL/file/system, publisher, date, authority, permissions, last checked, hash.                       |
| Signal                 | Observed change or pattern with entity, timestamp, evidence, confidence and materiality.            |
| Insight                | Interpretation of one or more signals, affected audience, implication and uncertainty.              |
| Opportunity / threat   | Commercial hypothesis, expected value, urgency, owner, dependencies and score.                      |
| Audience / persona     | Industry, role, job, pain, trigger, proof need, objection, channel and exclusions.                  |
| Account / buying group | Fit tier, stakeholders, systems, signals, engagement, opportunity and owner.                        |
| Offer                  | Problem, outcome, scope, proof, price/packaging, CTA, eligibility and delivery owner.               |
| Campaign               | Objective, audience, insight, offer, journey, channels, budget, dates, owners and measurement plan. |
| Asset                  | Master/derivative relation, topic, format, audience, stage, claims, sources, approvals and rights.  |
| Touchpoint             | Account/contact, channel, asset, event, time, campaign and response.                                |
| Experiment             | Hypothesis, variants, primary metric, guardrails, sample, result and decision.                      |
| Learning               | Validated finding, scope, evidence, confidence, action, owner and expiry/review date.               |
| Agent run              | Agent/version, brief, context, tools, output, scores, human edits, cost and failure class.          |

**GOVERNANCE**

# Autonomy levels and mandatory gates

| **Level**                     | **Authority**                                                         | **Examples**                                                     |
|-------------------------------|-----------------------------------------------------------------------|------------------------------------------------------------------|
| Level 0 — Suggest             | Research, analyse and recommend only. No external action.             | New agents; high-risk domains; strategic decisions.              |
| Level 1 — Options [v2]        | Present 2–3 ranked, evidenced, materially distinct options with a recommendation and a declared timeout behaviour. External action only after a chosen decision. | Public content, founder positions, new claims, visual variants, ICP lists. |
| Level 2 — Execute approved    | Carry out a pre-approved plan inside fixed rules.                     | Scheduling approved posts, CRM enrichment, reporting, reminders. |
| Level 3 — Bounded optimise    | Adjust within explicit spend, frequency, audience and message limits. | Bid/pacing changes, nurture selection, test allocation.          |
| Level 4 — Autonomous low risk | Operate independently with monitoring and rollback.                   | Internal tagging, deduplication, dashboards, anomaly checks.     |

**Trust-weighting rule — specified as the earn-in mechanism [v2, §G2].** v1 described this in prose; v2 specifies it and it is enforced in data. Levels are authoritative in `autonomy.yaml`, keyed by (function_id, action_class), failing closed and test-enforced. That file changes in exactly two ways, each one reviewed line in a PR whose payload is a `system.autonomy_level_change` card from Fn 126:

| Rule | Condition |
|---|---|
| Action classes | *Upstream*: produce, analyse, evaluate, compose_options, mine, score. *Downstream*: publish, claim, spend, engage_external, contact_client. |
| Starting level, new function | Upstream 2; downstream 1. Ceiling without a card: upstream 4, downstream 2. |
| Promote 1→2 | 30-day window, ≥ 40 runs, both-gate pass ≥ 70%, Recommendation Hit Rate ≥ 60%, **zero** `fabricated-proof-point` events, zero material failures. |
| Promote 2→3 | 60-day window, ≥ 80 runs, gate pass ≥ 85%, Hit Rate ≥ 75%, Rejection-All ≤ 10%, zero fabrication, zero material failures; downstream additionally ≥ 20 published items via the governed path with zero corrections. |
| Promote 3→4 | 90 days, ≥ 150 runs, gate pass ≥ 95%, Hit Rate ≥ 85%, zero guardrail breaches. Upstream only. |
| Demote | Immediately on any material failure (drop one level, pause until control-change card chosen); fabrication rate > 5% of runs in 14 days (drop to L1); gate pass < 50% over 20 runs (drop one); Hit Rate < 40% over 20 decisions (drop one); Rejection-All > 50% over 15 decisions (pause). |
| Timeout defaults | Disabled globally. Earned per function after holding L2 criteria for two consecutive windows; **upstream only — downstream never defaults**. Until earned, every card carries `default_on_timeout: null` and unanswered cards expire unresolved. |
| Regain | The promote table applies from the new level. No shortcut. |

**Approval budget [v2].** Six cards per working day, delivered as one 07:30 SAST digest; non-negotiables and incidents are realtime and exempt. Overflow is queued by expected value; persistent overflow is a signal to retire a class of approvals (Fn 118) or cut volume — never to enlarge the digest. This is the control that makes "humans only ratify" survivable.

**NON-NEGOTIABLE CONTROLS**

## Actions requiring a ratified decision — unchanged in substance; now typed card kinds that never default, are always realtime, and can never be covered by a standing permission [v2]

• Publishing from Canvas company accounts or any employee/executive
personal profile during the initial operating phase.

• New positioning, product claims, pricing, guarantees, comparative
claims or quantified client outcomes.

• Use of client names, logos, screenshots, data, meeting content or
testimonials.

• Paid-media strategy changes or spend above a defined daily/campaign
threshold.

• Legal, regulatory, security, employment, financial or reputationally
sensitive statements.

• Changes to production prompts, tools, permissions, knowledge authority
or autonomy level.

• Crisis responses, corrections, takedowns and direct engagement with
hostile or high-profile accounts.

**CANVAS GO-TO-MARKET**

# Recommended content and campaign portfolio

| **Portfolio pillar**                | **Examples**                                                                                                               |
|-------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Executive decision intelligence     | CFO/CIO/COO viewpoints on moving from dashboards to governed, decision-ready business context.                             |
| ERP and application modernisation   | Vertical pages and guides for Dynamics, SAP, NetSuite, Powerfleet and other systems already supported by Canvas.           |
| Microsoft Fabric and Power BI value | Migration/readiness diagnostics, reference architectures, governance explainers and cost/ROI models.                       |
| AI grounded in trusted data         | Explain why AI assistants need governed semantic models, business rules, permissions and evidence before enterprise use.   |
| Finance and operations playbooks    | Order-to-cash, working capital, margin, inventory, route profitability, fleet utilisation and executive scorecards.        |
| Proof library                       | Before/after metrics, anonymised mini-cases, architecture patterns, implementation timelines, FAQs and objection handling. |
| Founder/expert video series         | 60–120 second answers, teardown videos, whiteboard explanations, lessons from projects and reactions to industry change.   |
| Benchmark and calculator assets     | Data/AI maturity assessment, Power BI/Fabric business case calculator, fleet/route ROI tools and industry benchmarks.      |
| Partner campaigns                   | Microsoft and application-vendor co-marketing, webinars, implementation guides and referral playbooks.                     |
| Named-account campaigns             | Research-backed, role-specific engagement plans for priority enterprise accounts and buying groups.                        |

**POST AND ADVERTISING EXPERIMENTS**

## A disciplined test backlog

| **Experiment**              | **Hypothesis / design**                                                                                                     |
|-----------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| Founder POV video           | One strong opinion, one real example, one implication; compare against text-only version.                                   |
| Executive carousel          | A 7–10 page framework such as “Why your AI project fails before the model is selected”.                                     |
| Data teardown               | Anonymised dashboard, process or architecture teardown with what is wrong and how to improve it.                            |
| Benchmark post              | Publish one proprietary or curated benchmark with a practical interpretation and downloadable detail.                       |
| Before/after proof          | Show old operating condition, intervention and measurable business change with evidence.                                    |
| Diagnostic ad               | Promote a high-value assessment rather than a generic “book a demo” message.                                                |
| Role-specific ad set        | Run CFO, CIO and COO variants with distinct problem, proof and CTA.                                                         |
| Partner-authority ad        | Co-brand with a credible ecosystem partner or promote expert collaboration.                                                 |
| Retargeting proof sequence  | Serve case, architecture, objection and diagnostic assets in a controlled order.                                            |
| Vertical landing page       | Create a dedicated problem/industry page with relevant proof and account-specific CTA.                                      |
| Webinar-to-content flywheel | One expert event generates clips, article, carousel, FAQ, nurture and sales follow-up.                                      |
| Comparison page             | Fair, evidence-based comparison of build vs buy, generic BI vs governed platform, or alternative implementation approaches. |

**DELIBERATE EXCLUSIONS**

# Deliberate design exclusions

Several approaches commonly recommended for AI marketing teams are
deliberately excluded from this design. Documenting them prevents the
engine from re-acquiring them by default.

| **Excluded approach**                                                | **Reason**                                                                                                                                                                                                                                                                  |
|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| CrewAI, LangChain, Flowise or Langflow as an orchestration framework | The stack is standardised on the Claude Agent SDK, sub-agents, skills and MCP. A second framework fragments the build, doubles maintenance and adds no capability Canvas needs.                                                                                             |
| Make or Zapier for workflow automation                               | n8n Community Edition or Power Automate are the selected automation layer; Power Automate aligns with the Microsoft estate, and Make’s free tier (about 1,000 operations/month) is too small for the loops described here.                                                  |
| Pinecone, Chroma or Mem0 vector-memory layers                        | Premature infrastructure at this scale. The Knowledge Base Librarian (106), project knowledge and the Fabric/warehouse layer cover memory needs; add vector search only when retrieval demonstrably fails.                                                                  |
| Midjourney, DALL-E, Ideogram or Flux image generation                | Direct brand risk. Canvas’s proof-led aesthetic depends on real Power BI dashboards, the device-mockup library and licensed client logos. AI-generated imagery would undermine the exact credibility the content trades on. Canva/Figma templates remain the design system. |
| TikTok, Reels and CapCut-style short-video formats                   | Off-ICP. Canvas sells to CFOs, IT and operations leaders in South African construction, engineering, mining and logistics — reachable on LinkedIn, email, webinars and search. Monitor these formats through the Canvas-ify protocol for structural inspiration only.       |
| ROAS, CPL and CPA as primary KPIs before paid spend exists           | Paid media functions (78–81) stay dormant with their KPIs until budget is approved; activating cost-per-lead targets without spend invites vanity reporting.                                                                                                                |
| A full retention / NPS / churn agent inside marketing                | Client success sits with the delivery team. Marketing owns only the advocacy-harvesting slice (function 26), which converts delivery success into approved proof.                                                                                                           |
| HubSpot, Klaviyo or Mailchimp as core stack                          | Dynamics 365 remains the strategic CRM fit; HubSpot Free is prototype-only. Email operates through the existing Microsoft/Google estate until a dedicated platform is justified by list size.                                                                               |
| Volume goals such as “50 MQLs per month”                             | Wrong objective for a high-value considered purchase with long cycles. ICP Qualified Pipeline Efficiency is the north star; a small number of well-qualified executive conversations outweighs lead volume.                                                                 |
| A system that publishes, spends or claims without a ratified option [v2 rewording] | The v1 exclusion targeted removing *accountability*; v2 keeps accountability human and moves *authorship* to the system. Humans ratify options; they do not author content, evals, foundations or outcome data. Downstream autonomy grows only through §G2 earn-in. |
| Perplexity, Tavily, SerpAPI, Apify or Crawl4AI scraping stacks — **amended in v3** | v1 excluded these outright. v3 permits **exactly one discovery API and exactly one crawler**, behind Fn 129 Web Reach Governor: read-only, cost-capped, ToS/robots-compliant by construction, PII-scrubbed, treated as untrusted data. The exclusion's reasons became controls. Reason for the change: 9/12 empty scan profiles and a silent discovery trigger showed Claude web + Semrush alone did not populate the daily loop. Adding a second of either is a `system.prompt_change`-tier decision. |
| PostHog or Plausible analytics                                       | GA4, Search Console and Microsoft Clarity are already selected, free, and Clarity aligns with the Microsoft estate.                                                                                                                                                         |

**TECHNOLOGY STACK**

# Recommended build and free/low-cost tools

| **Layer**               | **Recommendation**                                               | **Use**                                                                                                                                                                                                                                                                                         |
|-------------------------|------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Agent runtime           | Claude Agent SDK                                                 | Production agents in Python/TypeScript with tool use, state and controlled loops. Use Claude Code primarily to build and maintain the system.                                                                                                                                                   |
| Specialists             | Claude sub-agents / reusable skills                              | Represent the 112 functions as typed specialists invoked by the Orchestrator.                                                                                                                                                                                                                   |
| Tool access             | MCP                                                              | Connect approved data and actions through scoped, documented interfaces.                                                                                                                                                                                                                        |
| Workflow automation     | n8n Community Edition or Microsoft Power Automate                | Use deterministic automation for triggers, approvals, retries, routing and publishing. n8n Community is self-hostable; Power Automate aligns to Canvas’s Microsoft estate.                                                                                                                      |
| Knowledge and approvals | SharePoint / Teams / OneDrive                                    | Canonical approved content, proof, permissions, review and collaboration.                                                                                                                                                                                                                       |
| Data platform           | Microsoft Fabric / Azure data platform                           | Unify web, channel, CRM, campaign, account and cost data with governance.                                                                                                                                                                                                                       |
| Measurement             | Power BI                                                         | Executive, campaign, content, account and agent-performance dashboards.                                                                                                                                                                                                                         |
| CRM                     | Dynamics 365 where available; HubSpot Free for a prototype only  | Dynamics is the strategic fit; HubSpot’s free CRM can support a small proof of concept where no CRM is available.                                                                                                                                                                               |
| Website analytics       | Google Analytics + Search Console + Microsoft Clarity            | Journey and conversion measurement, organic search performance, heatmaps, recordings and behaviour diagnosis.                                                                                                                                                                                   |
| Social scheduling       | Buffer Free for a small pilot; Metricool Free/MCP for evaluation | Buffer currently supports up to three connected channels on Free; Metricool offers MCP access even on Free. Verify limits before committing.                                                                                                                                                    |
| Design                  | Figma Starter and/or Canva Free                                  | Templates, diagrams, social assets and collaborative design; use Canva Bulk Create (agents output a structured CSV of approved copy variants, Canva bulk-generates on-template assets, pairing with the existing Buffer bulk-upload workflow); move to paid brand controls only when justified. |
| Exploration dashboards  | Looker Studio                                                    | Useful for quick external-data prototypes, although Canvas should operationalise core measurement in Power BI.                                                                                                                                                                                  |
| Version control         | GitHub                                                           | Store prompts, skills, schemas, workflows, eval sets and deployment code with review and rollback.                                                                                                                                                                                              |

**FREE TOOLS**

## What to implement immediately

| **Tool**                   | **Immediate value**                                                                                       | **Primary owner**                           |
|----------------------------|-----------------------------------------------------------------------------------------------------------|---------------------------------------------|
| Google Search Console      | Search queries, impressions, clicks, position, indexation and technical alerts.                           | SEO/Search Analyst; Content Optimizer.      |
| Google Analytics           | Free customer-journey and marketing measurement foundation.                                               | Funnel Analyst; Revenue Attribution.        |
| Microsoft Clarity          | Free heatmaps, recordings and behavioural diagnostics.                                                    | Landing Page UX/CRO; Anomaly investigation. |
| Google Trends              | Search interest and language patterns for content planning.                                               | Industry Trend Analyst; Editorial Planner.  |
| Buffer Free                | Pilot scheduling for up to three connected channels under current Free limits.                            | Publishing Agent.                           |
| Metricool Free / MCP       | Evaluate social analytics and natural-language access through MCP; API automation requires a higher plan. | Social Analyst; MCP Engineer.               |
| HubSpot Free CRM           | Prototype contact, deal and task management where Dynamics is not yet connected.                          | Lead Routing; Revenue Attribution.          |
| Figma Starter / Canva Free | Create templates and design assets at low cost.                                                           | Creative squad.                             |
| Looker Studio              | Quick shareable dashboards for non-core prototypes.                                                       | Analytics prototypes.                       |
| n8n Community Edition      | Self-hosted workflow automation for internal use, subject to licence and security review.                 | Workflow Automation Engineer.               |

**IMPLEMENTATION**

# A practical 24-week roadmap

| **Phase**                             | **Timing**  | **Deliverable**                                                                                                                                                                                      |
|---------------------------------------|-------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Phase 0: Foundation [v2]              | Weeks 1–2   | Fn 122 drafts objectives, ICP list, brand constitution, metric definitions and approver map as option cards from existing project docs; Fn 118 seeds SP-001–004; Fn 126 builds the eval baseline from August gate history. Nothing is human-authored. Options inbox service and Fn 116/117 land first — they change the shape of every existing gate without adding a content agent. |
| Phase 1: Intelligence MVP             | Weeks 3–6   | Deploy Orchestrator, Brand Steward, Evidence Guardian, Market Intelligence, Competitor Monitor, Voice-of-Customer, Research Brief and Evaluator. Produce daily signals and weekly opportunity brief. |
| Phase 2: Content engine               | Weeks 7–10  | Add SME harvesting, founder ghostwriting, long-form, LinkedIn, repurposing, creative brief, publishing and editorial workflows. Launch proof-led editorial calendar.                                 |
| Phase 3: Demand and account engine    | Weeks 11–16 | Add ICP/account scoring, campaign strategy, lead magnets, landing-page CRO, nurture, sales handoff, ABM and CRM integration.                                                                         |
| Phase 4: Measurement and optimisation | Weeks 17–20 | Build Fabric/Power BI marketing model; add attribution, content/funnel analytics, experimentation and anomaly detection.                                                                             |
| Phase 5: Earned autonomy [v2]         | Weeks 21–24 | Promotions happen only via §G2 cards with the numbers attached. Model routing, observability and cost controls. Public claims, spend and reputation decisions stay at ≤ L2 regardless of numbers without a card. |

| **Build-order warning** Do not stand up many agents simultaneously. Unproven agents amplify each other’s errors and drown review capacity in unreviewable volume. Each phase must reach stable quality — measured by acceptance rate and guardrail record — before the next begins. Video and motion production (58–60) remain deliberately late-phase, after the text, measurement and improvement loops are proven. |
|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

**FIRST 30 DAYS**

## Concrete deliverables

• A versioned Canvas brand and messaging constitution, including
approved positioning, tone, proof and prohibited claims.

• A 25–40 account ICP target list with role maps, trigger signals and
account scores.

• A competitor universe and daily change-monitoring feed with
materiality scoring.

• A voice-of-customer library built from approved proposals, meeting
notes and sales objections.

• A 90-day content/campaign portfolio with three proof-led themes and
one buyable offer per theme.

• Zero founder or expert interviews: Fn 113 refreshes the corpus nightly and Fn 114 rebuilds
the voice model weekly; one optional two-minute recording per week only if D1 is ratified. [v2]

• A minimum evaluation suite covering factuality, brand, audience relevance, evidence,
confidentiality and formatting — generated by Fn 127 and ratified by sampling, not authored. [v2]

• A Power BI MVP showing content, channel, account, lead, opportunity,
cost and agent-quality metrics.

**OPERATING RHYTHM**

# Daily, weekly, monthly and quarterly cadence

| **Frequency** | **Operating activity**                                                                                                            |
|---------------|-----------------------------------------------------------------------------------------------------------------------------------|
| Continuous    | Ingest approved signals; monitor critical anomalies, competitor changes and reputation; execute approved schedules.               |
| Daily         | Prioritised intelligence brief; account trigger alerts; production queue; publishing pre-flight; sales handoff review.            |
| Weekly        | Campaign and editorial planning; experiment decisions; pipeline/account review; agent-quality review; learning release.           |
| Monthly       | Vertical/offer performance; budget reallocation; content refresh; win/loss synthesis; partner and event plan.                     |
| Quarterly     | ICP and positioning review; proposition/package decisions; autonomy review; architecture/cost review; strategic benchmark report. |

## The standard operating week

| **Day**   | **Focus**                                                                                                         |
|-----------|-------------------------------------------------------------------------------------------------------------------|
| Monday    | Intelligence: weekend signals consolidated; competitor and ecosystem brief; opportunity/threat cards prioritised. |
| Tuesday   | Strategy: weekly plan set; briefs issued; experiment decisions; account plays selected.                           |
| Wednesday | Production: drafting, design and repurposing against briefs; SME input captured.                                  |
| Thursday  | QA and publishing: brand, evidence and compliance review; approvals cleared; queue scheduled.                     |
| Friday    | Analytics and retro: weekly performance versus benchmarks; agent scorecards; learning release and prompt updates. |

This shape is a default, not a straitjacket — reactive opportunities and
the daily loops continue throughout.

**HUMAN TEAM**

# The people the agent system still needs

| **Human role [v2]**           | **Irreplaceable responsibility**                                                                     |
|-------------------------------|------------------------------------------------------------------------------------------------------|
| Ratifier (Pieter)             | Chooses options; holds the seven non-negotiables; ratifies standing permissions, level changes, objectives. Never authors. |
| External consent-givers       | Clients (names, logos, testimonials via Fn 119); employees (one-time standing consent for personal-profile posts, SP-003). |
| Outside counsel               | RED-tier legal only (Fn 124). |
| Data/agent engineer           | Builds and maintains the runtime, integrations, observability and harness — already how CMOS is built. |
| On-camera presence            | Only if D1 is ratified: two minutes a week from a generated script (Fn 123). |

| **Recommended human operating model [v2]** No marketing operator, no SME interviews, no sales data entry, no creative reviewer. One ratifier reads one digest a day. The system reports on itself (Fn 117, 125, 126); it does not get chased. Sales, SME and creative roles are replaced by Fns 113–127; executive judgment is exercised by choosing, not writing. |
|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

**RISK REGISTER**

# Primary failure modes and controls

| **Risk**                          | **Consequence**                                          | **Control**                                                                               |
|-----------------------------------|----------------------------------------------------------|-------------------------------------------------------------------------------------------|
| Generic AI content flood          | Low trust, low differentiation, brand dilution.          | Require unique SME evidence, proof and editorial quality score before publishing.         |
| Agent sprawl                      | Duplicate work, excessive cost and conflicting outputs.  | 15-agent daily core; specialists invoked as skills; one Orchestrator and shared state.    |
| False or stale claims             | Reputational and legal harm.                             | Evidence Guardian, source freshness, independent fact check and claim risk tiers.         |
| Attribution theatre               | Optimising channels on misleading last-click data.       | Account journeys, CRM evidence, experiments, holdouts and multiple attribution views.     |
| Sales/marketing disconnect        | Leads ignored; poor feedback; misleading marketing KPIs. | Sales acceptance reasons, SLA agent, weekly account/pipeline review.                      |
| Confidentiality leakage           | Client harm and contractual breach.                      | Approved-source boundaries, tenant isolation, redaction, permissions and access controls. |
| Autonomous spend/publishing error | Financial or reputational damage.                        | Bounded budgets, pre-flight checks, approvals, kill switch and rollback.                  |
| Self-modifying prompts            | Silent quality or policy regression.                     | Version control, eval gate, approval and rollback; no direct production self-edit.        |
| Tool/platform lock-in             | High switching cost and brittle integrations.            | Typed data model, MCP/tool abstraction, exportable logs and versioned prompts/workflows.  |

**TEMPLATES**

# Standard agent brief and output contract

| **Field**              | **Required content**                                                       |
|------------------------|----------------------------------------------------------------------------|
| Objective              | The decision or outcome this task must enable.                             |
| Audience / account     | Who the output is for; relevant role, industry, stage and exclusions.      |
| Context                | Approved background, prior decisions, related assets and campaign.         |
| Inputs and sources     | Required systems, evidence hierarchy, freshness threshold and permissions. |
| Task steps             | Expected reasoning/workflow and required tools.                            |
| Output schema          | Typed fields, format, length, variants and confidence.                     |
| Quality rubric         | Accuracy, relevance, originality, clarity, evidence, brand and compliance. |
| KPIs                   | Predicted leading metric, commercial outcome and guardrails.               |
| Autonomy and approvals | Actions allowed, prohibited, spend limit and named approver.               |
| Logging                | Prompt/skill version, sources, costs, decisions and downstream outcome.    |

**CAMPAIGN CARD**

## Minimum campaign definition

| **Component**        | **Definition**                                                                              |
|----------------------|---------------------------------------------------------------------------------------------|
| Commercial objective | Example: create five sales-accepted opportunities in logistics accounts with 200+ vehicles. |
| Audience             | Named accounts, roles, pains, trigger and disqualifiers.                                    |
| Insight / tension    | What has changed or what the audience misunderstands.                                       |
| Offer                | Diagnostic, benchmark, workshop, calculator, pilot or platform package.                     |
| Proof                | Case, data, SME, architecture, partner or demo evidence.                                    |
| Journey              | Awareness → consideration → proof → action → sales follow-up.                               |
| Assets and channels  | Master asset, derivatives, LinkedIn, search, email, event, partner, paid.                   |
| Experiment           | Hypothesis, variant, primary metric, guardrail and stopping rule.                           |
| Measurement          | Account, lead, opportunity, revenue and cost linkage.                                       |
| Decision rule        | Scale, iterate or stop criteria and review date.                                            |

**RECOMMENDATION**

# The best starting configuration for Canvas

Start by building the intelligence-to-thought-leadership loop and the
measurement foundation. This gives Canvas immediate value: a better view
of competitors and emerging services, a constant stream of validated
content ideas, expert-led LinkedIn and website assets, and a structured
record of what actually creates target-account engagement.

Do not begin with autonomous posting or paid-media optimisation — §0 makes that case
better than any principle can. Begin at Options and Execute-approved autonomy, collect
decision telemetry (which option, which rejection code), and let functions earn promotion
under §G2. The version of autonomy that works here is a data change, not a design change:
one reviewed line in `autonomy.yaml`, reversible, driven by the numbers. [v2]

The first flagship output should be a recurring Canvas Executive Data &
AI Decision Brief: a monthly, evidence-led point of view on enterprise
data, Microsoft Fabric/Power BI, governed AI, finance/operations
analytics and selected vertical trends. Each issue becomes founder
posts, short video, an article, a carousel, an email, a webinar topic
and sales talking points. That single source can power the continuous
engine while preserving expertise and consistency.

**APPENDICES [v2]**

# Appendix A — Change log v1 → v2

Rule: v1 (`cf0dbfb77438…`) is never edited. One line per amended row, with the reason. Anything not listed is unchanged.

| Row / section | v1 | v2 | Why |
|---|---|---|---|
| Header, provenance, §0 | — | Added sha256 pin, operating principle, live baseline | Design must live in version control and be checkable; PR #147 |
| Design decisions — Autonomy principle | Draft/analyse autonomously; gate publish/spend/claim | Agents author, humans ratify; earn-in per function | Pieter's ruling 4 Sep: no human creates content |
| Design decisions — Agent count | 112 | 127 (IDs 113–128 provisional) | New ratification/earn-in functions |
| Fn 3 Brand Steward | Learn from approved edits; named owner | Learn from decisions; rule diffs as cards | H16 |
| Fn 5 Legal | Human legal review for ambiguous cases | Three-tier triage via Fn 124; RED to counsel only | H15 |
| Fn 6 Approval Router | Chase approvals | Budgeted digest; silence is an answer; Fn 117 | H13, H28 |
| Fn 8 Incident | Notify owners; coordinate | Autopilot via Fn 125; only the public correction is a card | H17 |
| Fn 19 Regulatory | Require legal review | Route through Fn 124 | H15 |
| Fn 24 Win/Loss | Interviews | Inferred via Fn 120 | H6 |
| Fn 40 SME Interviewer | Conduct interviews | Superseded by Fn 113 | H1 — the corpus is the interview |
| Fn 43 Ghostwriter | Genuine expert input | Drafts the chosen position from Fn 115; evidence or "new stance" label | H2 — ratification, not fabrication |
| Fn 47 Case Study | Human-initiated; secure approvals | Fn 26 trigger; Fn 119 permission card | H18, H7 |
| Fn 87 Sales Handoff | Track acceptance | Inferred by Fn 120; one-tap confirm | H8 |
| Fn 101 Evaluator | Sampled human review | Decision telemetry (Fn 126) + generated evals (Fn 127) | H14; ~380-eval backlog |
| Fn 102 Coach | Human edits as diagnosis | Rejection-code histograms; card payload = diff + eval comparison | H14 |
| Fn 112 Harness | Human-authored gold datasets | Generated by Fn 127, ratified by sampling | Upstream autonomy — the real bottleneck |
| §10 new functions 113–127 | — | Added | Replace every human authoring role in the register (Appendix B) |
| Agent score — Acceptance | Human edit distance | Recommendation Hit Rate; Rejection-All Rate | Humans choose, they do not edit |
| Starter targets — first-pass acceptance | Assets approved without edits | Hit Rate ≥ 60% by Q2; Rejection-All ≤ 15% | Same |
| Governance — Level 1 | Draft | Options | A single draft invites editing |
| Governance — trust-weighting | Prose | §G2 earn-in table; enforced in `autonomy.yaml` | §0: 25% gate pass, 226 fabrications, 0 publishes — autonomy by evidence, not fiat |
| Governance — timeout defaults | — | Disabled globally; earned; downstream never | Same baseline; defaulting would auto-approve fabrication |
| Governance — approval budget | — | 6 cards/day, one digest | Makes ratification survivable |
| Non-negotiables | Seven actions | Unchanged; typed card kinds, never default, never permissioned | Downstream is not loosened anywhere in v2 |
| Deliberate exclusions — "fully autonomous team" | Excluded | Reworded to "publishes, spends or claims without a ratified option" | Accountability stays; authorship moves |
| Roadmap Phase 0 / Phase 5 | Human-confirmed foundations; bounded autonomy | Fn 122 drafts as cards; earned autonomy | H11, H12 |
| First 30 days — capture process, eval suite | One interview per expert per month; authored suite | Zero interviews; generated suite | H1, H14 |
| Human team | Seven roles | Ratifier, external consent-givers, outside counsel, engineer, (on-camera if D1) | §Appendix B |
| Recommendation section | Begin at Draft/Execute-approved | Begin at Options/Execute-approved; promote by §G2 | §0 |
| Fn 17-source-scout (build-discovered, no v1 row) | Sources promoted by hand editing `source-candidates.yaml`; no quality loop | Fn 128: promote/retire as option cards through the digest; nightly yield scoring; SP-005 | Pieter's ruling 4 Sep: source approvals come through Teams like everything else and must improve continuously. Live: 11/12 profiles empty (v2 figure; corrected to 9/12 in v3), 408/493 daily tasks dead-lettered, discovery trigger silent. |
| Evidence references (§C3) | Free text | Must resolve to a real corpus atom, vault asset or decision record, else the card is rejected | `fabricated-proof-point` 226/30d after three prompt fixes |

## Change log v2 → v3

| Row / section | v2 | v3 | Why |
|---|---|---|---|
| Header, provenance | v2 pinned beside v1 | v3 beside v2; delta-only; rulings recorded | Same discipline as v2 |
| Design decisions | — | Discovery principle row added | Sources discovered, not configured |
| §11 Continuous Discovery | — | New chapter: reach, cadence per class, Fn 128 rewritten discovery-first, Fn 129 Web Reach Governor, allowlist rule, budgets, kinds/seeds/earn-in | Pieter's rulings 4 Sep; live evidence of empty profiles |
| Fn 128 | Gap-driven weekly probe, allowlist-bound (v2 amendment draft) | Daily discovery across all classes; probe; ratify; score; retire | Ruling: daily everywhere |
| Fn 129 | — | New | One API + one crawler + auto-widening allowlist need an owner with a kill switch |
| Deliberate exclusions — scraping stacks | Excluded | One discovery API + one crawler permitted under Fn 129 | Ruling; exclusion reasons became controls |
| Appendix B | 31 rows | 32 rows (H32 allowlist widening) | Engineer-by-PR was a hidden human act |
| Appendix C | C1–C4 | + `source.*` kinds; `source.allowlist` at security tier | 11.6 |
| Appendix D | PR 5b | PR 5b (Fn 128 + loop) and PR 5c (Fn 129 + API/crawler + allowlist rule + SP-005/006) after PR 5 | Both need the digest; 5c needs 5b's candidates to govern |
| Appendix E | — | Delta rows for audit v2 and Road to 219 | v3 is not re-audited; it is reconciled |
| Approval budget | 6/day | Unchanged; discovery capped at 2 digest cards/day; SP-005/006 absorb the rest | Daily discovery would otherwise consume the budget |

# Appendix B — Human-input register

Every place v1 depended on a human, classified. REPLACE: agent authors, human picks. CONVERT: already an approval, reshaped to a card. IRREDUCIBLE: external human or physical presence; the agent drafts everything around it. 19 / 8 / 3.

| Row | Class | v1 location | Human act in v1 | v2 replacement | Card kind |
|---|---|---|---|---|---|
| H1 | REPLACE | Fn 40; insight-to-content loop; Wed "SME input"; first-30 "one interview per expert" | SME interviews | Fn 113 | record |
| H2 | REPLACE | Fn 43; core #10; Decision Brief | Pieter supplies opinions | Fn 114 + 115 | content.founder_position |
| H3 | IRREDUCIBLE | Fn 58, 49; founder video series | Person on camera | Fn 123 kit; decision D1 | content.video_topic |
| H4 | IRREDUCIBLE | Fn 73, 49; webinar flywheel | Live speakers | Fn 123 / recorded | content.video_topic |
| H5 | IRREDUCIBLE | Fn 68 | Employee consent, personal profile | SP-003 standing consent; non-response = skip | advocacy.employee_post |
| H6 | REPLACE | Fn 24 | Win/loss interviews | Fn 120 | sales.win_loss |
| H7 | IRREDUCIBLE | Fn 26, 47, 64; non-negotiable 3 | Client consent | Fn 119 drafts and tracks; register read-only | client.permission_request |
| H8 | REPLACE | Demand outcomes; Fn 87; sales leader | Sales types acceptance reasons | Fn 120 | sales.outcome_confirm |
| H9 | REPLACE | Fn 67 | Expert reviews replies | Fn 116 | content.reply |
| H10 | REPLACE | Fn 55, 56, 62; creative reviewer | Designers, visual review | Fn 121 | content.visual_variant |
| H11 | REPLACE | Phase 0 | Humans author foundations | Fn 122 | foundation.* |
| H12 | REPLACE | First-30-days deliverables | Authored deliverables | Fn 122, 126 | foundation.* |
| H13 | REPLACE | Marketing operator | Runs calendar, coordinates | Fn 117 + Fn 1 | foundation.calendar_slate |
| H14 | REPLACE | Agent score; Fn 3, 43, 101, 112; learning layer | Edits / sampled review as signal | Fn 126, 127 | record |
| H15 | CONVERT | Fn 5, 19 | Legal review | Fn 124 | legal.amber / legal.sensitive_statement |
| H16 | REPLACE | Fn 3 named owner | Brand rule authorship | Fn 3 via Fn 116 | foundation.brand_rule |
| H17 | REPLACE | Fn 8 | Incident coordination | Fn 125 | crisis.correction, incident.control_change |
| H18 | REPLACE | CMOS Fn 47 cadence | Someone starts a case study | Fn 26 → 119 | client.permission_request |
| H19 | REPLACE | Fn 15, 20, 22 | Leadership accept/reject | Fn 116 | opportunity.* |
| H20 | REPLACE | Fn 74 | Spokesperson quotes | Fn 114 | content.founder_position |
| H21 | CONVERT | Fn 7, 80; non-negotiable 4 | Spend thresholds | quarterly card | spend.* |
| H22 | REPLACE | Fn 90 | Metric owners | Fn 122 | foundation.metric_definition |
| H23 | REPLACE | Signal layer; Fn 23 | Approve each transcript | SP-001 | system.standing_permission |
| H24 | CONVERT | Seven non-negotiables | Approvals | typed realtime cards, no default | non_negotiable kinds |
| H25 | CONVERT | Fn 102; step 6 | Prompt change approval | card + eval diff | system.prompt_change |
| H26 | CONVERT | Trust-weighting | Level changes | Fn 126 under §G2 | system.autonomy_level_change |
| H27 | CONVERT | Fn 1 | Strategic objectives | Fn 122 / 38 | foundation.objectives |
| H28 | REPLACE | Fn 6 chase | Approvers chased | Fn 117 | digest |
| H29 | CONVERT | Non-negotiable 1, personal profiles | Per-post consent | SP-003 | system.standing_permission |
| H30 | CONVERT | Exclusion "fully autonomous team" | Design stance | reworded | doc |
| H31 | REPLACE | Build-discovered `17-source-scout`; `source-candidates.yaml`; scan profiles | Sources promoted by hand (file edit + PR); no quality loop; 9 of 12 profiles empty; discovery trigger silent | Fn 128 (discovery-first): daily `source.promote` cards, nightly yield, monthly `source.retire`; SP-005 | source.promote / source.retire |
| H32 | REPLACE | `MCP_WEB_LIVE_MODE` allowlist in `main.bicep` | An engineer widens egress by Bicep PR for every new domain | Fn 129 + `policies/allowlist-rule.yaml` + SP-006 auto-widen; `source.allowlist` card only for rule failures | source.allowlist |

# Appendix C — Contracts (summary; full JSON Schema in `cmos-autonomy-extension/contracts/`)

**C1 OptionCard.** `card_id`, `kind` (typed enum, 30 kinds), `autonomy_level` 0–4, `risk_tier` low/medium/high/non_negotiable, `title`, `decision_question` (answerable by picking), 2–3 `options` each with `option_id` A–C, `label`, `summary`, `payload_ref` (vault), `evidence_refs`, `predicted_outcome`, `risks`, `distinctness_axis`; `recommended_option_id` (mandatory); `evidence_refs` (≥ 1); `novel_stance` flag (rendered on the card); `produced_by` (function, prompt version, model, cost); `expires_at`; `default_on_timeout` (null unless earned; always null for L0–1 and non-negotiables); `budget_class` digest/realtime; `register_rows`; `lineage.agent_run_id` (a real `agent_runs` row — PR #105).

**C2 ApprovalDecision.** `outcome` chosen / rejected_all / deferred / timeout_default / expired_unresolved; `chosen_option_id`; `was_recommended` (derived → Hit Rate); `rejection_code` from a fixed 12-value picker (no free text as the primary signal); `decided_by` (UPN or `system:timeout_default` / `system:standing_permission:<id>`); Key Vault signature with replay rejection as `gate_decisions` already does. One decision per card.

**C3 Evidence resolution.** Every `evidence_refs` entry with `source_type ≠ none` must resolve, via the vault's atom/asset/decision index, at card-build time. Unresolvable → `CardError`, card never reaches a human. This is the structural answer to `fabricated-proof-point`: a citation that cannot be resolved is a fabrication by construction, and three options per card would otherwise triple the surface.

**C4 StandingPermission.** `scope` (card kinds, functions, channels, source types), `rule` (`effect`, deterministic `condition` evaluated in a builtin-free namespace, `hard_exclusions` forced to include every non-negotiable kind), `review_by` ≤ 90 days, `evidence` (decisions observed, Hit Rate), `suspend_if`. Proposed by Fn 118; enacted only through a card. Seeds: SP-001 internal Fireflies transcripts as approved sources; SP-002 LinkedIn company-page scheduling of a chosen `content.publish` option at L2 (retires non-negotiable 1 for that channel once granted); SP-003 per-person personal-profile consent; SP-004 GREEN legal tier auto-pass.

# Appendix D — Build plan for Claude Code (ordered; each item one PR; each independently revertible)

| # | PR | Scope | Done when |
|---|---|---|---|
| 0 | `docs: blueprint v2 + register` | This file to `docs/blueprint/agentic-marketing-engine-v2.md`; assign final IDs 113–127 in `docs/function-register.md`; update `function_id` in the fifteen prompt front-matters and manifests; `scripts/validate.py` green | Validator passes against the real register |
| 1 | `vault: option_cards, approval_decisions, standing_permissions` | DDL from `services/options_inbox/store.py`; FK to `agent_runs`; migration test | `migration-test` green |
| 2 | `options_inbox service` | `cards.py`, `policy.py`, `teams_render.py`, `store.py` + 14 tests into `services/`; resolver wired to the vault atom index | Tests green; a card with a bad ref is rejected in a live dry run |
| 3 | `gatekeeper-approval: /decide + reject-all` | Three `Action.OpenUrl` buttons + `Input.ChoiceSet`; signing/replay as today | A three-option card round-trips in `OS Approvals` |
| 4 | `autonomy.yaml: action classes + earn-in test` | Add `action_class` dimension; starting levels per §G2; test that asserts no downstream line > 2 and no `default_on_timeout` anywhere | `validate-loops` and the new test green |
| 5 | `Fn 116 + Fn 117 + options-approval-loop.yaml` | Wrap the six Wednesday handlers; digest at 07:15; per-item gating | First digest lands with ≤ 6 cards; a chosen option schedules via the existing handler in dry-run |
| 6 | `Fn 126 Decision-Quality Evaluator` | Nightly scoring; Power BI rows; monthly level-change cards | First scorecard row per function |
| 7 | `Fn 127 Eval Generator` | Sets from August gate history; sampling cards | First ratified set activates for Fn 48 |
| 8 | `Fn 113 + Fn 114 + expertise-harvest-loop.yaml` | SP-001 card first; nightly mine; weekly voice rebuild with drift gate | Corpus delta > 0 for 7 consecutive nights; voice profile v1 |
| 9 | `Fn 115 + Fn 43 rewire` | Positions card precedes ghostwrite | Hit Rate measurable on founder cards |
| 10 | `Fn 118 + seeds SP-002/003/004` | Weekly proposer | First permission granted; approval count drops |
| 11 | `Fn 120, 124, 125` | Sales inference, legal triage, incident autopilot | Batched confirm card; AMBER card; a forced guardrail breach pauses the lane and emits both cards |
| 12 | `Fn 119, 121, 122, foundation-bootstrap-loop.yaml` | Permission, visuals, foundations | Foundation cards chosen; options loop enabled by the bootstrap condition |
| 5b | `Fn 128 + source-lifecycle-loop` | After Stage 0's hand-seed (tagged provisional) and after PR 5. Daily discovery per class via Claude web + Semrush only until 5c; `source.promote` / `source.retire` cards; nightly yield rows. Absorb `17-source-scout` (map or retire its ID). | First `source.promote` card in the digest; a chosen source lands on a profile via PR; yield rows nightly; provisional sources re-ratified or retired within 30 days |
| 5c | `Fn 129 + discovery API + crawler + allowlist rule + SP-005/006` | Pick one API and one crawler, record in `docs/accepted-risks.md`; `policies/allowlist-rule.yaml`, `allowlist-deny.yaml`, `discovery-budget.yaml`; Fn 129 governor with kill switch; harness cases for scraped-content injection; SP-005/006 cards from Fn 118. | Off-allowlist candidate auto-allowed by rule and logged with `review_by`; a rule-fail raises a `source.allowlist` card; cost cap stops a run in a forced test; an injected scraped page is stripped and logged |
| 13 | `Fn 123` | Only after D1 is ratified | — |

Decisions the plan needs from the ratifier before PR 5: **D1** founder video (record 2 min/week · synthetic · drop — recommendation: record; synthetic contradicts the blueprint's own credibility argument); **D2** approval budget (6/day recommended); **D3** resolved by §0 — defaults disabled until earned; **D4** the exclusion rewording in this document.

Errands (no judgment): confirm Fireflies exposes participant emails (SP-001 condition); export LinkedIn post history into the corpus; confirm Teams Adaptive Card 1.5 renders `Input.ChoiceSet` inside a container with three `OpenUrl` actions; the four open August items (UpdraftPlus backup, PAT revocation, POPIA DPA list, financial-services client count) are unaffected.

# Appendix E — Delta rows for audit v2 and Road to 219 [v3]

Apply these to `docs/audit/canvas-marketing-os-audit-v2.html`, `road-to-219-v2.html` and `audit-v2-recount.md`; do not re-run either document.

| Document | Change |
|---|---|
| Audit §1 scorecard | Function register 127 → 129 (Fn 128, 129: Scaffolded once PR 5b/5c open, Deferred until then); loops 10 → 11 (`source-lifecycle-loop`); contracts 4 → 5 (allowlist rule as C5); standing-permission seeds 4 → 6 (SP-005, SP-006); new class "Discovery policies" = 3 (`allowlist-rule.yaml`, `allowlist-deny.yaml`, `discovery-budget.yaml`); tech-stack integrations 13 → 15 (one discovery API, one crawler), both Deferred. Denominator 219 → **228**; show the reconciliation. |
| Audit §3 deviation register | `17-source-scout` row: v3 absorbs it into Fn 128; status becomes *superseded* once PR 5b lands. |
| Audit §4 human-input scorecard | Add H31 (still exists: yes, hand-promoted; wiring: Deferred → Scaffolded at PR 5b) and H32 (still exists: yes, Bicep PR; wiring: Deferred → Scaffolded at PR 5c). Rail stat denominator /32. |
| Road to 219 → Road to 228 | Insert steps 5b and 5c after step 9 (App D PR 5). Stage 0 done-when adds "hand-seeded sources tagged provisional". §4 unlock "scan-profile source approval" becomes "one-time ratification of the provisional seed; thereafter `source.promote` cards". New unlock: "choice of discovery API and crawler" (Pieter's ruling permits them; the specific vendor is an errand, recorded in accepted-risks). New risk rows: allowlist creep (control: 60-day review_by + monthly card); scraped-content injection (control: Fn 129 strip + harness); discovery flooding the digest (control: 2-card cap + SP-005/006). Schedule: +2 PRs, +1–2 weeks in the card-mechanism stage. |
| Recount | Arithmetic for the rows above. |

**SOURCES**

# Selected research and product references

1.  [Canvas Intelligence — company website and service
    positioning](https://canvasintelligence.com/)

2.  [Canvas Intelligence — products and
    pricing](https://canvasintelligence.com/products-pricing/)

3.  [Anthropic — Building effective
    agents](https://www.anthropic.com/engineering/building-effective-agents)

4.  [Anthropic — Demystifying evals for AI
    agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

5.  [Anthropic — Claude Agent SDK
    overview](https://code.claude.com/docs/en/agent-sdk/overview)

6.  [Anthropic — Claude Code
    sub-agents](https://code.claude.com/docs/en/sub-agents)

7.  [Anthropic — Claude Code and
    MCP](https://code.claude.com/docs/en/mcp)

8.  [McKinsey — The surprising economics of B2B growth (2026 Global B2B
    Pulse)](https://www.mckinsey.com/capabilities/growth-marketing-and-sales/our-insights/the-surprising-economics-of-b2b-growth-the-new-survival-threshold-and-what-it-takes-to-thrive)

9.  [LinkedIn — 2025 B2B Marketing Benchmark: trust, video and
    influence](https://www.linkedin.com/business/marketing/blog/marketing-collective/2025-b2b-marketing-benchmar-the-video-influence-effect-starts-with-trust)

10. [LinkedIn — AI visibility and professional trust in
    2026](https://www.linkedin.com/business/marketing/blog/content-marketing/how-to-leverage-linkedin-for-ai-visibility-in-2026)

11. [Google Search Central — helpful, reliable, people-first
    content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)

12. [Google Search Central — guidance on generative AI
    content](https://developers.google.com/search/docs/fundamentals/using-gen-ai-content)

13. [Google Search Console —
    overview](https://search.google.com/search-console/about)

14. [Google Analytics —
    overview](https://marketingplatform.google.com/about/analytics/)

15. [Microsoft Clarity — product and
    pricing](https://clarity.microsoft.com/pricing)

16. [Buffer — plan features and Free plan
    limits](https://support.buffer.com/article/595-features-available-on-each-buffer-plan)

17. [Metricool — plans, API and MCP
    access](https://help.metricool.com/plans-add-ons-and-api-access-explained-xux1u)

18. [HubSpot — free CRM
    overview](https://www.hubspot.com/products/crm/what-is)

19. [n8n — plans and Community Edition](https://n8n.io/pricing/)

20. [Figma — plans and Starter tier](https://www.figma.com/pricing/)

21. [Canva — Canva Free](https://www.canva.com/free/)

22. [Looker Studio — overview](https://lookerstudio.google.com/about)

Note: This blueprint is a strategic and technical operating design, not
legal advice. Tool plans, APIs and platform capabilities should be
revalidated during implementation.

Page
