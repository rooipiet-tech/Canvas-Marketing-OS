# Skill description — Brand Steward QA

- **Purpose**: QA gate for public-facing Canvas Intelligence marketing
  copy. Reviews positioning fidelity against `docs/positioning.md`
  (advisory) and enforces a blocking permission rule: any named client
  or sales-deck-sourced claim must have a confirmed permission-register
  entry before it can ship. This function never rewrites copy — it
  returns pass/fail plus findings.
- **When to invoke**: Before any draft from `functions/042/` (or any
  other copy-producing function) is published or sent externally.
  Treat as a mandatory gate, not an optional review.
- **When NOT to invoke**: Internal-only drafts not headed for
  publication; content that names no client and makes no sales-deck-
  sourced claim (still worth a positioning-fidelity pass, but the
  blocking rule has nothing to check).
- **Inputs**: see `schema.json`
- **Tools available**: see `tools.yaml`
- **Evaluation**: see `evals/` — includes a case where an uncleared
  client name must block.
