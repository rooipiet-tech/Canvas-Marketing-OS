# Skill description — Executive/Founder Ghostwriter (function 43)

- **Purpose**: Ghostwrites first-person opinion content in a named
  executive's voice, built from the `docs/positioning.md` messaging house.
  Every opinion, quote or stance attributed to the executive must trace to a
  `sourced_opinion_or_quote` the caller actually supplied — this function
  never fabricates or invents one.

- **When to invoke**: To turn an executive's already-said quote, deck line
  or transcript excerpt into a first-person opinion post; to run an
  executive-voice content series alongside the pillar-led drafting
  functions.

- **When NOT to invoke**: To attribute an opinion the executive has not
  actually stated — if no `sourced_opinion_or_quote` is supplied, this
  function writes without a personal stance rather than inventing one. To
  decide whether a client may be named — that is a direct read of
  `docs/permission-register.yaml`. To publish anything directly — this
  function is draft-only; a human always signs off on an executive-voice
  piece before it schedules.

- **Inputs**: see `schema.json` — `executive_name`, `pillar`, `campaign`,
  optional `sourced_opinion_or_quote`.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup, and the read-only safety suite.

- **Evaluation**: see `evals/` — 5 golden tasks covering a baseline sourced
  opinion, a fabricated/unsourced-opinion draft that must be blocked with a
  failing verdict, the roof line and CTA rule, verbatim pillar naming, and
  South African English hygiene. The fabricated-opinion task's mechanical
  check is a deterministic keyword-match proxy used for grading only — it is
  not the real enforcement mechanism; that is the weekly loop's Thursday
  fact-check verdict task, an actual judgement step.

- **Guardrails**: Never fabricate an opinion — no source, no personal stance.
  No client name unless CLEARED. No link shortener. No US spelling. One CTA.
  Draft-only: never reaches a publish-class gate-check. No POPIA or
  data-residency compliance claim — this function only states that it
  routes through the documented Gatekeeper gate-check.
