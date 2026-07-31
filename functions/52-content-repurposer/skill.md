# Skill description — Content Repurposer (function 52)

- **Purpose**: Takes one existing long-form asset (typically function 46's
  newsletter or function 47's case study) and repurposes it into 2-3
  shorter derivative social formats — LinkedIn post, X post, email teaser —
  each roof-lined, pillar-tagged and carrying its own call to action.

- **When to invoke**: After a newsletter or case study is drafted, to spin
  it into shorter social derivatives without re-deriving proof from
  scratch; to stretch one approved long-form asset across a week's worth of
  shorter posts.

- **When NOT to invoke**: To draft the original long-form asset — that is
  function 46 or function 47. To decide whether a client may be named —
  that is a direct read of `docs/permission-register.yaml` (default deny).
  To publish a derivative — publishing is a separate, gated step.

- **Inputs**: see `schema.json` — `source_asset_summary`, `pillar`,
  `campaign`, `target_formats` (1-3 of `linkedin_post`/`x_post`/
  `email_teaser`), optional `client_reference`.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup, and the read-only safety suite.
  This function publishes nothing.

- **Evaluation**: see `evals/` — 6 golden tasks covering the baseline
  repurpose, the roof-line/CTA rule across every derivative, verbatim
  pillar naming, South African English hygiene, the derivative count
  matching the requested `target_formats` exactly, and the client-naming
  block.

- **Guardrails**: Exactly one derivative per requested format, never more or
  fewer. Every derivative closes with the roof line and carries its own
  CTA. No client name unless CLEARED. No link shortener. No US spelling.
