# Skill description — Email/Newsletter Writer (function 46)

- **Purpose**: Writes the long-form owned-channel newsletter/email digest of
  the week's approved proof points, built from the `docs/positioning.md`
  messaging house. Closes on the roof line "Your Data. Delivered." with
  exactly one call to action.

- **When to invoke**: To turn the week's approved proof points into a single
  newsletter/email issue; to run a weekly digest series; to convert a set of
  disconnected proof points into one coherent CFO-facing email.

- **When NOT to invoke**: To decide whether a client may be named — that is
  a direct read of `docs/permission-register.yaml` (default deny). To write
  a short-form social post — that is function 42 or function 39. To publish
  the newsletter — publishing is a separate, gated step under
  `publish.blog_article`, never performed by this function itself.

- **Inputs**: see `schema.json` — `pillar`, `proof_points`, `campaign`,
  optional `client_reference`.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup, and the read-only safety suite.
  This function drafts only; the Wednesday drafting stage runs under
  `draft.social_post`, and the Friday publish-to-owned-channel stage reuses
  `publish.blog_article` — the closest existing autonomy.yaml analogue,
  since no email-specific gate-check identifier exists.

- **Evaluation**: see `evals/` — 5 golden tasks covering the baseline
  newsletter, the roof-line/CTA rule, verbatim pillar naming, the CFO
  voice-of-customer quote, and South African English hygiene.

- **Guardrails**: No client name unless CLEARED. No link shortener. No US
  spelling. One CTA. Never claims POPIA or full data-residency compliance —
  only that this pipeline routes through the documented Gatekeeper
  gate-check.
