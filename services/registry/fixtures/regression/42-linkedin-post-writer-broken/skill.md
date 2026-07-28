# Skill description — LinkedIn Post Writer (function 42)

- **Purpose**: Writes a single LinkedIn post for the office of the CFO,
  built from the `docs/positioning.md` messaging house: the roof line
  "Your Data. Delivered.", the five competency pillars, and the CFO
  voice-of-customer language from the pre-meeting survey. Every post
  carries exactly one proof point and exactly one call to action.

- **When to invoke**: To turn an approved proof point (a signal from
  function 09, a case-study metric, an architecture fact) into a published
  LinkedIn post; to run a pillar-led content series; to convert a platitude
  post into a proof post.

- **When NOT to invoke**: To decide whether a client may be named — that is
  function 02, Brand Steward QA, reading `docs/permission-register.yaml`.
  To source a market claim — that is function 09. To write long-form web
  copy or a case study page. To write anything at all without a proof
  point: this function has no path to a compliant post from an assertion
  alone.

- **Inputs**: see `schema.json` — `pillar`, `proof_point`, `campaign`,
  optional `audience_note`.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup, and the read-only safety suite.
  This function publishes nothing; scheduling is a separate, gated step.

- **Evaluation**: see `evals/` — 5 golden tasks covering the roof line,
  verbatim pillar naming, CFO voice-of-customer opening, the UTM/no-shortener
  link rule, South African English, and the client-naming block.

- **Guardrails**: No client name unless CLEARED. No link shortener. No US
  spelling. One CTA. Output is handed to function 02 for QA before any
  human sees it as publishable.
