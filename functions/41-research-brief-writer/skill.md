# Skill description — Research Brief Writer (function 41)

- **Purpose**: Turns a signal or opportunity card into a structured research
  brief — pillar, vertical, cited proof points, and an audience note — for
  downstream drafting functions to work from. Every proof point in the
  brief carries a source; a claim with no source is never fabricated to
  fill the gap.

- **When to invoke**: When a new signal or opportunity card needs to become
  a brief before any drafting function (story editor, function 39;
  executive ghostwriter, function 43) touches it.

- **When NOT to invoke**: To write publishable copy directly — that is a
  downstream drafting function working from this brief. To decide client
  naming in isolation — that is a direct read of
  `docs/permission-register.yaml`. To invent a proof point when the signal
  supplies none: this function has no path from an assertion alone to a
  cited claim.

- **Inputs**: see `schema.json` — `pillar`, `vertical`, `signal_summary`.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup, and the read-only safety suite.

- **Evaluation**: see `evals/` — 5 golden tasks covering a baseline sourced
  brief, verbatim pillar naming, the CFO voice-of-customer audience note,
  South African English hygiene, and a signal with no cited evidence at all
  (proof_points must be left empty, never fabricated).

- **Guardrails**: Proof over platitude — every proof point cites a source.
  No client name unless CLEARED. No US spelling. No POPIA or
  data-residency compliance claim.
