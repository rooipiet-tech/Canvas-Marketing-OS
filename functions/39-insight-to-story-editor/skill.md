# Skill description — Insight-to-Story Editor (function 39)

- **Purpose**: Turns a raw insight — often function 41's research brief —
  into a single narrative story draft built from the `docs/positioning.md`
  messaging house: the roof line "Your Data. Delivered.", the five
  competency pillars, and the CFO voice-of-customer language from the
  pre-meeting survey. Every story carries at most one proof point and
  exactly one call to action.

- **When to invoke**: To turn an approved insight or brief (function 41's
  output, a case-study metric, an architecture fact) into a draft social
  story; to run a pillar-led content series; to convert a platitude insight
  into a proof-led narrative.

- **When NOT to invoke**: To decide whether a client may be named — that is
  a direct read of `docs/permission-register.yaml`. To source a market claim
  — that is upstream signal work. To write long-form web copy or a case
  study page. To fabricate a proof point when the input states plainly that
  none has been documented yet: this function has no path from an
  unsupported assertion to a compliant post.

- **Inputs**: see `schema.json` — `pillar`, `proof_point`, `campaign`,
  optional `audience_note`, optional `client_reference`.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup, and the read-only safety suite.
  This function drafts only; scheduling and publishing are separate, gated
  steps outside this function definition.

- **Evaluation**: see `evals/` — 6 golden tasks covering the roof line,
  verbatim pillar naming, South African English, the UTM/no-shortener link
  rule, a missing-proof-point signal that must not be fabricated, and the
  client-naming guard.

- **Guardrails**: No client name unless CLEARED. No fabricated proof point.
  No link shortener. No US spelling. One CTA. No POPIA or data-residency
  compliance claim — this function only states that it routes through the
  documented Gatekeeper gate-check.
