# 17 — Enterprise Vocabulary & Frameworks

*Phase 4. For every major thing you have built, the language enterprise
software vendors and management consultants use for it — so you can walk
into a CIO, CFO or investor conversation and name your own architecture in
their words.*

Each entry follows the same shape:
**What you built → "This is an example of…" → "The enterprise concept is
called…" → "McKinsey would call this…" → "Gartner would classify this
as…" → "This maps to…"**

---

## 1. `services/gatekeeper/policy/autonomy.yaml` — levels 0–4

**This is an example of** externalised authorisation — the decision about
*what is allowed* is separated from the code that *does the thing*.

**The enterprise concept is called** a **Policy Decision Point (PDP)** and a
**Policy Enforcement Point (PEP)**. Your PDP is `/gate-check`; your PEPs are
the orchestrator's `request-approval` handler and the Publisher. This is the
XACML architecture, and it's the same shape as AWS IAM (policy evaluated
centrally, enforced at the resource) and Open Policy Agent.

**McKinsey would call this** a **Delegation of Authority (DoA) matrix**, or
in an operating-model review, the *decision rights framework*. Their standard
tool is RAPID® (Recommend, Agree, Perform, Input, Decide). Your levels map
almost exactly: level 4 = the agent Decides; levels 1–2 = the agent
Recommends and a human Decides; level 0 = the decision right does not exist.
**You have machine-executable decision rights, which is what every operating-
model redesign wants and none can enforce.**

**Gartner would classify this as** **AI TRiSM** (AI Trust, Risk and Security
Management), specifically the *AI governance* and *runtime enforcement*
pillars. Also relevant: their "Guardian Agents" framing (2025).

**This maps to** ISO/IEC 42001 (AI management systems) §8.3 operational
control; the NIST AI RMF **GOVERN** function; the EU AI Act Art. 14 (human
oversight); COBIT's EDM (Evaluate/Direct/Monitor).

**Say this in a meeting:** *"Our autonomy policy is a machine-enforced
delegation-of-authority matrix. It's a policy decision point with fail-closed
defaults, and every evaluation writes an immutable decision row."*

---

## 2. `contracts/` — ten hash-frozen files, read at runtime

**This is an example of** contract-first design with a **breaking-change
guard**.

**The enterprise concept is called** **Consumer-Driven Contract Testing** and
**Interface Versioning Governance**. Your `.frozen-v1.sha256` baseline is a
*schema registry with compatibility enforcement* — the same role Confluent
Schema Registry plays for Kafka.

The unusual part: `model-gateway/completion.py` reads the `CompletionRequest`
schema **out of the frozen OpenAPI file at runtime** and validates against
it. That is not documentation-as-code; that is **specification-as-runtime**.

**McKinsey would call this** an *architectural governance mechanism* enabling
**parallel workstream execution** — the mechanism that let a swarm of
independent sessions build against each other without integration hell.

**Gartner would classify this as** **API Governance / Contract-First API
Management**, adjacent to their "Composable Enterprise" thesis (Packaged
Business Capabilities with stable interfaces).

**This maps to** TOGAF's Architecture Contracts (Phase G); Bounded Context
integration patterns in Domain-Driven Design (Published Language +
Conformist); Postel's Law applied deliberately.

**Say this in a meeting:** *"Our interfaces are frozen contracts with a
CI-enforced hash baseline. Services validate against them at runtime, not
just at build time — so a contract drift is a failing request, not a silent
divergence."*

---

## 3. The gate token — RS256, `jti`, `exp`, content-hash-bound

**This is an example of** a **capability token** (also: bearer capability,
object capability) — the token *is* the authority, scoped to exactly one
action on exactly one object.

**The enterprise concept is called** **Proof-of-Possession / Sender-
Constrained tokens** (RFC 8705, DPoP), plus **transaction authorisation** —
the pattern behind PSD2 Strong Customer Authentication's "dynamic linking"
requirement, where the authorisation code must be cryptographically bound to
the *specific amount and payee*, so approving one payment cannot authorise
another.

**Your `content_hash` binding is dynamic linking for AI actions.** That is a
precise, powerful analogy, and it comes from banking regulation — which means
a regulated buyer already understands it.

**McKinsey would call this** a **four-eyes principle** (or *maker-checker*)
with **non-repudiation**.

**Gartner would classify this as** fine-grained authorisation / **Externalised
Authorisation Management (EAM)** — and increasingly under their "agentic AI
identity" research.

**This maps to** NIST SP 800-63 (authentication assurance); the SWIFT CSP
transaction-integrity controls; SOX ITGC change-authorisation controls.

**Say this in a meeting:** *"Approvals use dynamic linking — the same
principle as PSD2. The authorisation token is cryptographically bound to a
hash of the exact content approved, and the boundary service independently
recomputes that hash from the raw bytes. Approve-A-publish-B is impossible."*

---

## 4. `services/model-gateway/redaction.py`

**This is an example of** an **egress data-loss-prevention proxy** with an
audit trail.

**The enterprise concept is called** **DLP (Data Loss Prevention)** at the
egress boundary. In AI-specific language: an **LLM firewall** or **AI
gateway**. Your `content_class` mechanism is a **classification-driven policy
exemption** — the caller declares a class, and a *gateway-side reviewed
mapping* decides what that class relaxes. The caller never names a pattern.

**McKinsey would call this** a **preventive control** in the three-lines-of-
defence model (first line: the control itself; your `gate_decisions` audit is
the second line's evidence; the accepted-risks register is third-line input).

**Gartner would classify this as** AI TRiSM's *"AI runtime inspection and
enforcement"* — the same category as Prompt Security, Lakera, Robust
Intelligence.

**This maps to** POPIA s72 (cross-border transfer); GDPR Ch. V; ISO 27001
A.8.12 (data leakage prevention).

**The vocabulary that will most impress a privacy officer:** your rules file
says outright that it *"is not itself a transfer-lawfulness mechanism ...
Treat it as one control among several, never as the control."* That is
**control-effectiveness honesty**, and it is the difference between a
compliance posture and compliance theatre.

**Say this in a meeting:** *"We run an egress DLP firewall in front of the
model provider. Pattern coverage is deliberately acknowledged as incomplete —
which is exactly why every block writes an immutable audit row. It's a
defence-in-depth control on top of the lawful-basis regime, not a substitute
for it."*

---

## 5. `docs/permission-register.yaml` — default deny, absence ≠ permission

**This is an example of** an **allowlist with a proven-equivalent
default-deny path**.

**The enterprise concept is called** **fail-safe defaults** (Saltzer &
Schroeder, 1975 — principle 3 of 8). The subtle part you got right and most
teams get wrong: *absence* and *explicit denial* must be observationally
identical. Your `_self_test()` asserts exactly that equality.

**McKinsey would call this** a **control by design** rather than a control by
detection — the difference between preventing an error and finding it later.

**Gartner would classify this as** *policy-based content governance*; in a
marketing context, **brand safety controls**.

**This maps to** the principle of least privilege; NIST AC-3 (access
enforcement); ISO 27001 A.5.15.

**Say this in a meeting:** *"Client naming is default-deny with proven
equivalence: a name absent from the register blocks identically to one
explicitly marked uncleared. We have a self-test that asserts those two paths
produce the same outcome, because 'we forgot to add it' must never become
'therefore it's allowed'."*

---

## 6. `costs` rows + `budgets.yaml` + `cost_per_accepted_asset`

**This is an example of** **chargeback/showback** with **graceful
degradation**.

**The enterprise concept is called** **FinOps** — specifically the emerging
**AI FinOps** discipline. Your three-row metering (usd/tokens/ms) is *unit
economics instrumentation*; your unbroken FK chain `costs → agent_runs →
campaigns` is a **cost allocation model**; your soft-breach tier downgrade is
**graceful degradation** or **brownout** (as opposed to a circuit breaker,
which fails hard).

**`cost_per_accepted_asset` is a cost-per-outcome metric, not a cost-per-unit
metric.** That distinction is worth a lot in a CFO conversation: token spend
is an input measure; cost per *accepted* asset is an output measure, and it
is the AI equivalent of **cost per acquisition** or **cost of quality**.

**McKinsey would call this** **zero-based budgeting** applied to AI capacity,
and the metric itself a **productivity ratio** in a labour-substitution
analysis. If they were being expansive: *"the unit economics of synthetic
labour."*

**Gartner would classify this as** **Cloud Financial Management** extended to
AI, and **AI cost governance**.

**This maps to** activity-based costing (ABC); the FinOps Foundation's
Inform → Optimise → Operate phases; ITIL financial management.

**Say this in a meeting:** *"We meter every model call in three units and
attribute it through an unbroken chain to a campaign. Our headline metric is
cost per accepted asset — a cost-per-outcome measure, not cost per token.
Budget breaches degrade the model tier rather than blocking work."*

---

## 7. `services/registry` — signed, reproducible, eval-gated agent packages

**This is an example of** treating prompts as **first-class software
artefacts** with a supply chain.

**The enterprise concept is called** **Model Governance** and **AI Asset
Lifecycle Management**. Your canonical-JSON manifest + detached signature is a
**Software Bill of Materials (SBOM)** for AI assets, and the Ed25519
signature is **artefact provenance/attestation** — the same problem SLSA and
Sigstore solve for container images.

Your prompt-coupled eval oracle (`tool_check.py` derives its output from
`prompt.md`, so deleting a rule fails the test grading it) is **behaviour-
coupled regression testing**, and the broken-copy fixture is **mutation
testing** applied to prompts. Almost nobody does this.

**McKinsey would call this** **model risk management** — and would
immediately reach for **SR 11-7**, the US Federal Reserve's model-risk
guidance: model inventory, model validation, ongoing monitoring. You have all
three primitives.

**Gartner would classify this as** **ModelOps / LLMOps**, adjacent to their
AI TRiSM *model management* pillar.

**This maps to** SR 11-7; ISO/IEC 42001 §8.4 (AI system impact assessment);
SLSA build provenance levels; the EU AI Act's technical documentation
requirements (Annex IV).

**Say this in a meeting:** *"Prompts are versioned, signed, reproducible
artefacts with golden evaluation suites. We have a model inventory, model
validation and change control — the three primitives SR 11-7 asks for,
applied to generative assets."*

---

## 8. `governance.kill_switches` — uncached, <5s, global or per-function

**This is an example of** a **circuit breaker with an operator override** —
though strictly it is a *manual* breaker, not an automatic one.

**The enterprise concept is called** a **kill switch** or **emergency stop
(E-stop)**, borrowed from industrial safety (ISO 13850). In IT terms: a
**feature flag with a guaranteed propagation bound**.

Your design decision — *no cache at any TTL, because "a cache with any TTL
above zero would make the 5s bound a function of cache expiry rather than of
the operator's action"* — is the correct one, and it has a name:
**strong consistency over availability for a safety-critical read** (the CAP
trade-off, made deliberately).

**McKinsey would call this** a **business continuity control** and, in a
risk-appetite discussion, the *tripwire* in a risk-limit framework.

**Gartner would classify this as** *AI incident response*, part of AI TRiSM's
operational pillar.

**This maps to** the EU AI Act Art. 14(4)(e) — the human overseer's ability
to "interrupt the system through a stop button"; NIST AI RMF MANAGE-4.

**Say this in a meeting:** *"We have a hard emergency stop, global or scoped
to a single function, with a guaranteed sub-five-second propagation bound
because it's an uncached read on every decision path. It's the Art. 14 stop
button, implemented rather than described."*

---

## 9. `loops/*.yaml` + `uuid5` decomposition

**This is an example of** **declarative workflow orchestration** with
**deterministic replay**.

**The enterprise concept is called** a **DAG-based workflow engine** (Airflow,
Dagster, Temporal), and your `uuid5(event_id, task_id)` scheme is
**deterministic id derivation**, which enables **idempotent replay** and
**golden-file testing**.

The interaction between services is **choreography** (services react to
events), not **orchestration** (a central conductor calls services). Your
orchestrator is a *decomposer and dispatcher*, not a conductor — a meaningful
distinction in an architecture review.

**McKinsey would call this** **process standardisation** and, in an
operating-model context, *straight-through processing (STP)* — the banking
term for a transaction that completes without manual intervention. Your
level-3/4 functions are STP; your level-1/2 functions are *exception
handling*.

**Gartner would classify this as** **Hyperautomation** — specifically an
*Intelligent Business Process Management Suite (iBPMS)* with AI task workers.

**This maps to** BPMN 2.0 (your loop YAML is a BPMN process definition in
different syntax); the Saga pattern (long-running processes with compensating
actions — though you have no compensations); Event-Driven Architecture.

**Say this in a meeting:** *"Workflows are declarative DAGs with deterministic
task-id derivation, so the same trigger always produces the same run. It's
straight-through processing with explicit exception routing at the publication
gate."*

---

## 10. Vault taxonomy — 6 mandatory, immutable fields

**This is an example of** **mandatory metadata at the point of creation**,
with immutability enforcement.

**The enterprise concept is called** a **data classification scheme** and,
collectively, an **information governance framework**. `retention_class` is a
**records retention schedule**; `consent_status` is **consent state
management**; `evidence_grade` is a **data quality / provenance dimension**.

Making them **immutable post-create** is the key move, and it has a name:
**write-once metadata** or *provenance immutability*. It prevents
reclassification-after-the-fact, which is exactly the failure mode an auditor
looks for.

**McKinsey would call this** **data governance by design** and would place
your six fields in a **data product** framing (Data Mesh: every data product
carries mandatory metadata contracts).

**Gartner would classify this as** **Metadata Management** and **Data
Governance**, adjacent to their *Active Metadata* and *Data Fabric* research.

**This maps to** DAMA-DMBOK's Data Governance and Metadata Management
knowledge areas; ISO 15489 (records management); the Data Mesh data-product
contract.

**Say this in a meeting:** *"Every object carries six mandatory, immutable
governance fields set at creation — vertical, function, campaign, evidence
grade, consent status and retention class. Retention and evidentiary weight
are properties of the record itself, not of a policy someone applies later."*

---

## 11. `.compound/learnings/` — 79 classified learnings

**This is an example of** **organisational knowledge capture** with
**reinforcement signals** (your "strengthened/recurred N times" annotations).

**The enterprise concept is called** **Knowledge Management** (Nonaka &
Takeuchi's SECI model — you are systematically converting *tacit* incident
knowledge into *explicit* codified rules). More narrowly:
**blameless post-incident review** with a permanent, searchable corpus.

The recurrence counters are the sophisticated part: a learning marked "3rd
recurrence" is telling you the *control is not working*, not that the lesson
is popular. That is **control-effectiveness monitoring**.

**McKinsey would call this** a **learning organisation** (Senge), and
operationally a **continuous improvement (kaizen) loop** with a *lessons-
learned register*. In a Capability Maturity framing this is **CMMI Level 5 —
Optimising** behaviour: quantitative feedback used to systematically improve
the process itself.

**Gartner would classify this as** *Knowledge Graph for Engineering* or
DevOps *institutional memory*, and they would be under-selling it.

**This maps to** ITIL Problem Management (known error database); ISO 9001
§10 (improvement); the Toyota Production System's *yokoten* (horizontal
deployment of learning).

**Say this in a meeting:** *"We run a compound learning system — 79 classified
engineering learnings with recurrence tracking. A learning marked as a third
recurrence tells us a control isn't working, which is a different signal from
a lesson being useful. Later specifications cite prior learnings as
acceptance criteria."*

---

## 12. The worktree-per-session build model

**This is an example of** **isolated, parallelised delivery** with
**contract-mediated integration**.

**The enterprise concept is called** **trunk-based development with
short-lived branches**, plus **Team Topologies**' *stream-aligned teams* and
*enabling constraints*. Your frozen contracts are the **X-as-a-Service
interaction mode** between sessions, and your "first-to-land wins" conflict
rule is a **coordination protocol**.

**McKinsey would call this** an **agile operating model at scale** —
specifically, *loosely-coupled, tightly-aligned* squads with clear interfaces
and minimal coordination overhead. The frozen contracts are what removes the
"integration tax".

**Gartner would classify this as** *Platform Engineering* — the contracts and
CI checks are your **golden path**.

**This maps to** Conway's Law (deliberately inverted — you designed the
interfaces first and let the work organise around them); Team Topologies'
cognitive-load management; the Inverse Conway Manoeuvre.

**Say this in a meeting:** *"We run an inverse Conway manoeuvre — frozen
contracts define the seams, and isolated parallel sessions build against them.
Integration cost is near zero because the interfaces can't drift; CI enforces
a hash baseline."*

---

## 13. Content-addressed asset storage

**This is an example of** **content addressing** with deduplication and
reference counting.

**The enterprise concept is called** **Content-Addressable Storage (CAS)** —
the same principle behind Git objects, IPFS, and Docker layers. Combined with
your hash-bound gate token, it gives you **immutable, verifiable artefact
identity**.

**Gartner would classify this as** part of *Digital Asset Management* with
integrity assurance.

**This maps to** WORM (Write Once Read Many) storage for compliance; SEC Rule
17a-4 for records that must be non-rewriteable.

---

## 14. The dual-surface console (HTML + JSON from one service layer)

**This is an example of** **API-first design** with progressive enhancement.

**The enterprise concept is called** **Headless architecture** or
**API-first**, plus **content negotiation** (HTTP `Accept`). In the AI
context, your framing is better: **agent-native**.

**Gartner would classify this as** *Composable / MACH architecture*
(Microservices, API-first, Cloud-native, Headless).

**Why it matters strategically:** when every human surface has a machine
equivalent, an agent can operate the platform. That is a prerequisite for the
learning loop in `10-product-roadmap.md` R2 — the feedback service will read
the console's JSON, not scrape its HTML.

---

## 15. `qa_blocked` — a business verdict is not a failure

**This is an example of** distinguishing **expected exceptions** from
**system faults**.

**The enterprise concept is called** the difference between a **business
exception** and a **technical exception** — a distinction that shows up in
every serious BPM and integration architecture, and that most systems get
wrong by mapping both onto "error".

**McKinsey would call this** *first-pass yield* vs *defect rate* in a Six
Sigma frame. A QA block is a *rework loop*, not a defect in the process.

**Why this is genuinely rare:** your smoke test counts a legitimate
`QA_BLOCKED` verdict as **proof of life**, because reaching a real verdict
proves the whole chain worked. That is an unusually mature definition of
"green", and it comes from understanding that a system whose safety controls
never fire is indistinguishable from one whose safety controls are broken.

**Say this in a meeting:** *"We separate business verdicts from technical
failures at the state-machine level. A QA block has its own transition reason
and its own terminal state — and our end-to-end smoke test treats a
legitimate block as a pass, because a control that never fires is a control
you can't prove works."*

---

## 16. Quick-reference translation table

| You built | Enterprise term | Consultant term | Gartner category |
|---|---|---|---|
| `autonomy.yaml` | Policy Decision Point | Delegation of Authority matrix | AI TRiSM — governance |
| Gate token + hash binding | Sender-constrained capability token / dynamic linking | Four-eyes with non-repudiation | Externalised Authorisation |
| Redaction firewall | Egress DLP / LLM firewall | Preventive control, 1st line of defence | AI TRiSM — runtime enforcement |
| Permission register | Fail-safe defaults | Control by design | Brand safety controls |
| Costs + budgets | Chargeback + graceful degradation | Zero-based budgeting; productivity ratio | AI FinOps |
| Registry + evals | AI SBOM + model validation | Model risk management (SR 11-7) | ModelOps / LLMOps |
| Kill switch | Emergency stop | Business continuity tripwire | AI incident response |
| Loop YAML DAG | Declarative workflow orchestration | Straight-through processing | Hyperautomation / iBPMS |
| 6-field taxonomy | Data classification + retention schedule | Data governance by design | Metadata Management |
| `gate_decisions` | Immutable audit log | Evidence for 2nd line of defence | Continuous Controls Monitoring |
| Frozen contracts | Schema registry with compatibility guard | Architectural governance for parallel workstreams | API Governance |
| `.compound/learnings` | Known-error database | Learning organisation / CMMI L5 | Institutional memory |
| Worktree-per-session | Trunk-based dev + contract-mediated integration | Loosely-coupled, tightly-aligned squads | Platform Engineering |
| Content-addressed blobs | Content-Addressable Storage | — | DAM with integrity assurance |
| Dual-surface console | API-first / headless | — | Composable / MACH |
| `qa_blocked` | Business vs technical exception | First-pass yield vs defect rate | — |
| Proof circuit | Canary transaction with poison pill | Production verification testing | Synthetic monitoring |
| Cascade dead-letter | Fail-fast on unsatisfiable precondition | — | — |
| `write_audit_isolated` | Out-of-band audit durability | — | — |

---

## 17. The three sentences to memorise

**For a CIO:**
> *"It's an externalised authorisation layer for AI agents. Policy decision
> point, fail-closed defaults, sender-constrained capability tokens with
> dynamic content binding, independent verification at the egress boundary,
> and an immutable decision ledger that records refusals as first-class
> events."*

**For a CFO:**
> *"Every model call is metered in three units and attributed through an
> unbroken chain to a campaign. Budgets are per-function with graceful
> degradation rather than hard stops. Our headline metric is cost per
> accepted asset — a cost-per-outcome measure, not cost per token."*

**For an investor:**
> *"Ninety-two percent of the code is governance. We built a control plane
> for AI agents and proved it on the hardest use case we had — public,
> irreversible, brand-critical marketing content, through a US model
> provider, under South African privacy law. The marketing platform is the
> exhibit. The control plane is the product."*
