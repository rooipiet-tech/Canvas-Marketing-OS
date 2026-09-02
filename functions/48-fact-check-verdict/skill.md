# Skill description — Fact-Check Verdict (function 48)

**REVIEWED AND SIGNED OFF AS SETTLED QA POLICY — 2 Sep 2026, Pieter.**
Written 6 Aug 2026 to unblock weekly-content-loop.yaml's Thursday
fact-check task, and carried a first-draft banner until sign-off. Its
verdicts are now settled policy, with one known limitation reviewed and
deliberately left open rather than closed: narrative fabrication carrying
no number is structurally outside what this check can catch. See
prompt.md's "Known limitations" section and its 2 Sep sign-off note.

- **Purpose**: The second half of Thursday's dual QA gate (alongside
  function 02, Brand Steward). Judges whether every checkable claim in a
  Wednesday draft traces to a real, known fact rather than being invented
  or exaggerated — including, specifically, claims about Canvas
  Intelligence's own business model (CoEaaS vs. BuildSmart), since a
  flagship-BuildSmart claim reaching real content is the exact failure
  this project's 3 Aug 2026 positioning correction exists to prevent.

- **When to invoke**: On every one of the six Wednesday drafts, before
  Friday's Buffer scheduling or newsletter send — called once per draft
  by `qa_review_fact_check_handler` / `_single_draft_qa_review` in
  `services/orchestrator/orchestrator/dispatch.py` (round 34: one review
  task per draft, replacing the old all-or-nothing `_aggregate_qa_review`).

- **When NOT to invoke**: To check brand voice, tone, spelling, CTAs, link
  shape, or client-naming clearance — those are function 02's job and run
  as a separate, independent verdict. To write or rewrite copy (it returns
  verdicts only). As an advisory opinion a caller may override: a
  `pass: false` verdict is blocking, same as function 02.

- **Inputs**: see `schema.json` — `draft_text` only is actually used;
  `client_references` and `channel` are carried for shape symmetry with
  function 02 but not consulted by this function's checks.

- **Tools available**: none — this function has no read-only lookup tool
  of its own. It checks the draft against two closed lists written
  directly into `prompt.md` (the approved proof-point list and the
  business-model facts), not against a live document, because it has no
  access to the original research brief's per-claim `{claim, source}`
  pairs at review time. If `docs/positioning.md` changes, this prompt's
  lists must be updated by hand to match — they will silently drift
  otherwise.

- **Evaluation**: no `evals/` package yet. Unlike function 02, this
  function does not yet have a `tool_check.py` deterministic mock or
  golden tasks — it has not been run against real content and its
  strictness (particularly the "sharpening a real fact upward is still a
  violation" rule) has not been calibrated against actual drafts. Treat
  the first several real Thursday runs as a check on the prompt itself,
  not only on the drafts.

- **Guardrails**: When in doubt, fail. No partial pass. Never rewrites the
  draft or resolves a violation itself.
