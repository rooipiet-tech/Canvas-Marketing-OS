# 13 — AI Agent Catalogue

*All 23 function-definition packages. "Wired" means the `task_type` has a
real entry in `DISPATCH_TABLE`; "referenced" means a loop YAML names it but
it falls through to `legacy_task_pass_through` and does nothing.*

---

## Status summary

| Status | Count | Packages |
|---|---|---|
| **Wired and running** | 3 | 02, 09, 42 |
| **Referenced by a loop, not wired** | 20 | 10, 11, 12, 13, 16, 18-01…18-06, 25, 26, 39, 41, 43, 45, 46, 47, 52 |
| **No package (deterministic code)** | 2 | `brief.compose`, `request-approval` |

All 23 packages pass `validate_package.py`, carry ≥5 golden eval tasks, and
pass `eval_harness.py` against the mocked gateway. The gap is purely
runtime wiring — see `09-technical-debt.md` TD-01.

---

## A · Governance agent

### Function 02 — Brand Steward QA ★ WIRED
| | |
|---|---|
| **Role** | "You are the Brand Steward. You do not write marketing copy — you judge it." |
| **Model** | `claude-sonnet` |
| **Invoked from** | `qa-review` (daily loop, twice: brief-QA and proof-circuit content-QA) |
| **Output** | `{pass: bool, violations: [code], notes: string}` |
| **Authority** | **Blocking. `pass:false` → task FAILED / reason `qa_blocked`, `advance_dependents` never called.** |

Six checks, in order, each with its own violation code:

| # | Code | Rule |
|---|---|---|
| 1 | `uncleared-client-reference` | Default deny. Absence from the register blocks identically to explicit UNCLEARED |
| 2 | `link-shortener` | bit.ly, lnkd.in, tinyurl, ow.ly, buff.ly — hide destinations and break UTM attribution |
| 3 | `sa-english-spelling` | US variants: productize, behavior, organization, optimize, analyze, center, color |
| 4 | `missing-cta` | Exactly one CTA. **Exempt when `channel == "internal-brief"`** |
| 5 | `url-utm` | Full `https://www.canvasintelligence.com/...` with 3 UTM params. **Same exemption** |
| 6 | `unsupported-claim` | Superlatives with nothing attached. "the leading platform" fails; "one of the platforms behind 40+ business units consolidated across 14+ ERP systems" passes |

**Design notes worth preserving:**
- *"Never rewrite the draft"* — a QA function that edits its own input cannot
  be trusted to judge it. `tools.yaml` is 100% read-only for this reason.
- *"No partial pass."* `pass` is true only when `violations` is empty.
- *"Never resolve an uncleared client name by removing it yourself — that is
  the writer's decision to make with the verdict in hand."*
- The `channel` parameter changes which rules apply. `dispatch.py` passes
  `"internal-brief"` for brief-QA and `"linkedin"` for content-QA. **A prompt
  that reads a runtime discriminator and adjusts its own rule set** is
  policy parameterisation, not just templating.
- Function 02's `permission_check.py` is **loaded by reference** into the
  orchestrator via `importlib` (a digit-prefixed directory can't be
  dotted-imported — learning L-0039) so the deterministic uncleared-client
  check is the *same code*, never a fork. Its verdict is **merged with**,
  not substituted for, the LLM's own.

**Live gap:** `client_references` is always `[]` at the qa-review call site,
so the deterministic check currently always passes trivially (TD-28).

---

## B · Intelligence agents

### Function 09 — Market Intelligence Director ★ WIRED
| | |
|---|---|
| **Model** | `claude-haiku` (extraction tier) |
| **Invoked from** | `ingest-signals` (daily loop, first node) |
| **Tools** | `web_search` (declared, **not implemented**), `fetch_url` (live), `vault_signal_lookup` (declared) |

Output: `{topic, horizon_days, summary, signals[{headline, so_what, source_url, pillar, confidence}]}`

Eight hard rules, and each one is an epistemics rule rather than a formatting rule:

1. 3–8 signals
2. every signal carries an `https://` `source_url` — *"a signal you cannot
   attribute to a retrievable source is not a signal — drop it"*
3. ≥2 distinct domains — *"three headlines from one vendor blog is one
   signal, not three"*
4. exactly one pillar per signal, from the five verbatim names
5. `confidence: low` for a single unverified source, a vendor's claim about
   itself, or anything outside the horizon — *"do not round thin evidence up
   to medium"*
6. the summary repeats the horizon number so a reader knows the window
7. **never name a client** — *"describe organisations generically ('a listed
   logistics group')"*
8. South African English

Method: *"Work source-first, not conclusion-first. Retrieve, then attribute,
then interpret."*

**This is the best-written prompt in the repository.** Rules 2, 3 and 5 are
anti-hallucination controls expressed as editorial standards.

### Functions 10 · 11 · 12 · 13 · 16 — Competitive intelligence — REFERENCED
| Fn | Name | Loop task_type |
|---|---|---|
| 10 | Competitor Discovery Scanner | `competitor-discovery-scan` |
| 11 | Competitor Change Monitor | `competitor-change-monitor` |
| 12 | Competitive Positioning Analyst | `competitive-positioning-analysis` |
| 13 | Competitor Content Performance Scout | `competitor-content-performance-scout` |
| 16 | Microsoft Fabric Ecosystem Scout | `fabric-ecosystem-scout` |

All five share an eval shape: card-count-and-shape, source-attribution,
domain-diversity, no-client-naming, tagging-in-set. Functions 11 and 13 add a
sixth: **no-named-individual** — a stricter privacy rule than the others,
appropriate for monitoring competitors' people.

### Functions 18-01 … 18-06 — Vertical intelligence — REFERENCED
Six independent packages, **never one monolith**, sharing
`functions/_shared/vertical-intelligence-method.md`:

| Fn | Vertical | Proof basis (`positioning.md` §4) |
|---|---|---|
| 18-01 | Logistics & Fleet/Telematics | Imperial, Hestony/Powerfleet |
| 18-02 | Mining & Industrial | ArcelorMittal, Weir, Rotork |
| 18-03 | Manufacturing | **none — deliberately proof-light** |
| 18-04 | Construction & BuildSmart | BuildSmart |
| 18-05 | FMCG & Beverage | Delta |
| 18-06 | Financial Services | a Fabric client |

**18-03 is the most interesting package in the repository.** `positioning.md`
§4 names five vertical proof areas and manufacturing is not one of them, so
18-03's prompt has a "Proof-light default" section, its evals default
`evidence_grade` to `light` and never `strong`, and it carries a sixth eval
task (`task-06-proof-light-default.json`) enforcing exactly that.

**The agent is configured to be less confident where the company has less
evidence.** That is epistemic humility encoded as a testable behaviour, and
it is rare.

### Function 25 — Competitive Response Strategist — REFERENCED
Consumes the deduped cards from all 11 scanners and produces a
severity-ranked response plan, naming playbook templates ("RIB BI+ move",
"BuildSmart-native-BI move"). Six evals including a seeded golden
(`task-06-rib-bi-buildsmart-seeded-golden.json`).

---

## C · Content agents

### Function 42 — LinkedIn Post Writer ★ WIRED
| | |
|---|---|
| **Model** | `claude-sonnet` |
| **Invoked from** | `draft-content` (S8 proof circuit only, permanently dry-run) |
| **Output** | `{post, pillar, cta_url}` |

Seven hard rules: proof over platitude (with an explicit list of the
superlatives function 02 will reject, and a worked pass/fail example), client
names gated, roof line "Your Data. Delivered." on its own line, exactly one
CTA with three UTM params and no shortener, SA English, 90–220 words with ≤3
hashtags, name at least one pillar verbatim.

**The prompt embeds `positioning.md`'s messaging house as a table** — five
pillars, five messages, five lead proofs — and the CFO's voice-of-customer
language verbatim from the pre-meeting survey ("different number for the same
question", "More than 3 days", "No more Excel accounting"). The instruction is
explicit: *"Mirror their own words — do not paraphrase them into consultant
language."*

**The prompt-02-awareness pattern:** function 42's prompt tells the model
what function 02 will reject and why. **The generator is aware of the
critic.** That is a two-agent design expressed entirely in prompt content.

**`F-PROMPT-OUTPUT-CONTRACT` (round 18c)** — this prompt originally had no
"return a single JSON object" instruction at all, unlike its siblings 02 and
09. Every production `draft-content` call failed to parse. The repo-wide
sweep found 6 of 23 packages affected, and the fix added a **validator rule**
so a 7th can never slip through.

### Function 41 — Research Brief Writer — REFERENCED
The Tuesday node every Wednesday drafting function works from. Evals include
`cfo-quote-included` and `missing-source-citation`.

### Function 39 — Insight-to-Story Editor — REFERENCED
Turns a raw insight into a narrative closing on the roof line.

### Function 43 — Executive Ghostwriter — REFERENCED
First-person opinion in a named executive's voice. **Its second eval is
`task-02-fabricated-opinion-blocked.json`** — the agent must refuse to invent
an opinion the executive did not actually state. Deliberately excluded from
the Friday auto-schedule step.

### Function 45 — Carousel Post Writer — REFERENCED
Multi-slide carousel **plus its Canva Bulk Create CSV manifest**. Eval 02
validates the CSV manifest shape.

### Function 46 — Newsletter Writer — REFERENCED
Long-form owned-channel digest. Publishing reuses `publish.blog_article`
because no email-specific autonomy identifier exists.

### Function 47 — Case Study Writer — REFERENCED
Situation/approach/result structure. Names a client **only if CLEARED** —
nothing is. Ships its own `permission_check.py`. **Intentionally excluded
from the Friday auto-schedule**, published on a human-driven cadence once a
client is cleared.

### Function 52 — Content Repurposer — REFERENCED
One long-form asset → 2–3 shorter derivatives. Eval 05 asserts the output
count matches `target_formats`.

### Function 26 — Client Advocacy Harvester — REFERENCED
Turns an approved Fireflies transcript excerpt into a testimonial intake
record, gated on a **local consent-register fixture** *and* the permission
register. Its six evals include `revoked-consent-blocks` and
`absent-from-register-blocks-identically`. Ships its own `permission_check.py`.
The Fireflies integration itself does not exist.

---

## D · Deterministic (non-LLM) handlers

### `brief.compose` — `draft_brief_handler`
No package, no LLM call, no cost. `_render_brief()` turns function 09's
structured output into two markdown documents (full + "Executive Edition",
top-3 signals). Cites sources **by domain only**, never the bare URL —
*"a raw external citation link is neither a Canvas CTA link nor one that
should carry Canvas's own utm_* parameters"*, so function 02 never judges an
internal citation against customer-facing link rules.

**A deterministic handler between two LLM handlers is a good pattern.** It is
free, reproducible byte-for-byte, and cannot hallucinate.

### `request-approval` — `request_approval_handler`
No LLM. Calls `/gate-check` once and **completes as soon as it responds** —
never polls, never waits for the human. Stores `agent_run_id`, `function_id`
and `content_hash` in its `result_ref` so `run_state.py` can look up the real
decision later.

---

## E · Agent design patterns worth naming

| Pattern | Where | Enterprise name |
|---|---|---|
| Generator aware of critic | fn 42's prompt lists fn 02's rejection rules | Adversarial prompt co-design |
| Critic cannot edit | fn 02 `tools.yaml` is read-only | Separation of duties |
| Deterministic step between LLM steps | `_render_brief` | Hybrid symbolic-neural pipeline |
| Prompt drives its own eval oracle | `tool_check.py` derives output from `prompt.md` | Behaviour-coupled regression testing |
| Broken-copy regression fixture | `fixtures/regression/42-...-broken/` | Mutation testing for prompts |
| Confidence calibrated to evidence | fn 18-03 proof-light default | Epistemic humility as a testable property |
| Runtime discriminator changes rule set | fn 02's `channel` parameter | Policy parameterisation |
| Shared method document | `_shared/vertical-intelligence-method.md` | Prompt DRY |
| Code reused by reference, never forked | `permission_check.py` via importlib | Single source of truth |
| Skill file states when NOT to invoke | every `skill.md` | Negative capability specification |

The `skill.md` convention deserves special mention. Every package documents
**"When NOT to invoke"**. Function 02's is exemplary:

> *"To write or rewrite copy (it returns verdicts only, by design — a QA
> function that edits its own input cannot be trusted to judge it). To source
> market claims (function 09). **As an advisory 'second opinion' that a caller
> may override: a `pass: false` verdict is blocking.**"*

Most agent catalogues document capabilities. Documenting *anti*-capabilities
and *authority* is what makes an agent safe to compose.

---

## F · Agent estate gaps

| Gap | Impact |
|---|---|
| **20 of 23 agents never execute** | The advertised estate is 8× the delivered estate |
| **No agent evaluates another's output at runtime** | Function 02 does — but only for the 3 wired agents |
| **No agent has multi-turn / tool-use capability** | Every call is single-shot |
| **`web_search` declared but not implemented** | Function 09's intelligence is limited to 4 static URLs |
| **No agent reads its own history** | No agent sees prior runs, prior verdicts, or prior performance |
| **No agent-to-agent negotiation or debate** | Communication is `result_ref` pointers only |
| **`registry_version` never authoritatively populated** | Cannot answer "which prompt version produced this asset?" |
| **No cost-per-agent budget differentiation** | `budgets.yaml.agents` is `{}`; every function shares the $20/day default |
