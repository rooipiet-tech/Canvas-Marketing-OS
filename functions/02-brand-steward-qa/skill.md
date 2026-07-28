# Skill description — Brand Steward QA (function 02)

- **Purpose**: The publish gate. Judges a marketing draft against the
  client-reference permission register and Canvas's deterministic brand
  rules, returning a machine-readable pass/fail verdict with violation
  codes. It is the only function permitted to decide whether a client may
  be named.

- **When to invoke**: On every draft before it is scheduled, published or
  sent to a client — LinkedIn posts from function 42, case-study copy, web
  copy, deck text. Also on any human-authored draft entering the system.

- **When NOT to invoke**: To write or rewrite copy (it returns verdicts
  only, by design — a QA function that edits its own input cannot be
  trusted to judge it). To source market claims (function 09). As an
  advisory "second opinion" that a caller may override: a `pass: false`
  verdict is blocking.

- **Inputs**: see `schema.json` — `draft_text`, optional
  `client_references`, optional `channel`.

- **Tools available**: see `tools.yaml` — read-only permission-register
  lookup and the read-only safety suite. `services/registry/safety_suite.py`
  is the canonical implementation of the deterministic brand rules; the
  checks in this package's `tool_check.py` are the eval-time stand-in for
  it and must stay code-for-code aligned with it.

- **Evaluation**: see `evals/` — 5 golden tasks. Two of them
  (`bsq-001`, `bsq-002`) are the register tests that matter most: an
  explicitly UNCLEARED name and a name absent from the register entirely
  must both produce `pass: false` with the same
  `uncleared-client-reference` code.

- **Guardrails**: Default deny on client naming. Absence from the register
  is never read as permission. No partial pass.
