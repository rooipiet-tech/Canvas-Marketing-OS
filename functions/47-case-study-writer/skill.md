# Skill description — Case Study Writer (function 47)

- **Purpose**: Turns a proof point into a public case-study draft in a
  situation/approach/result structure, built around exactly one metric,
  closing on the roof line "Your Data. Delivered.". Names a client only if
  `docs/permission-register.yaml` shows CLEARED status — nothing is CLEARED
  today, so every case study ships client-free in practice.

- **When to invoke**: To turn an approved result (a metric, an architecture
  fact, a delivered artefact) into a public case-study draft; to write a
  case study series across the five messaging pillars.

- **When NOT to invoke**: To decide whether a client may be named in
  isolation — that is a direct read of `docs/permission-register.yaml`
  (default deny) via this package's own `permission_check.check_clearance`.
  To write a short-form social post — that is function 42 or function 39.
  To publish the case study — publishing is a separate, gated step.

- **Inputs**: see `schema.json` — `pillar`, `situation`, `approach`,
  `result`, `campaign`, optional `client_reference`.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup (backed by this package's own
  `permission_check.py`, copied from function 02's pattern), and the
  read-only safety suite. This function publishes nothing.

- **Evaluation**: see `evals/` — 6 golden tasks covering the baseline case
  study, an UNCLEARED client name blocked from being surfaced (exercised
  through the real `permission_check.check_clearance` default-deny path,
  not a hard-coded expectation), the roof-line/CTA rule, verbatim pillar
  naming, South African English hygiene, and the CFO voice-of-customer
  quote.

- **Guardrails**: No client name unless CLEARED — absence from the register
  blocks identically to an explicit UNCLEARED entry. Exactly one metric, never
  fabricated. No link shortener. No US spelling. One CTA. Never claims
  POPIA or full data-residency compliance — only that this pipeline routes
  through the documented Gatekeeper gate-check.
