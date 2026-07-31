# Skill description - Competitive Response Strategist (function 25-competitive-response-strategist)

- **Purpose**: Consumes the opportunity/threat cards produced by functions
  10, 11, 12, 13, 16 and the six `18-0N-vertical-intel-*` scanners, and turns
  them into a single ranked, severity-scored response plan. Two named
  playbook templates - `RIB BI+ move` and `BuildSmart-native-BI move` - cover
  the construction vertical's most common competitor-BI-product pattern;
  everything else gets a general pillar-led response.

- **When to invoke**: As the `competitive-response-strategize` node in
  `services/orchestrator/loops/daily-signal-loop.yaml`, downstream of
  `dedupe-signal-cards` and upstream of `morning-brief-rollup`; whenever a
  seller needs to know which of today's competitive signals to act on first.

- **When NOT to invoke**: To discover or monitor a competitor move itself -
  that is functions 10 and 11's job. To decide whether a client may be named
  (use function 02, Brand Steward QA). To write the brief copy itself (use
  function 42 or the brief-rollup step) - this function ranks and plans, it
  does not publish.

- **Inputs**: see `schema.json` - `cards` (upstream opportunity/threat
  cards), optional `topic`, optional `horizon_days`, optional
  `client_reference`.

- **Tools available**: see `tools.yaml` - read-only web/news retrieval,
  read-only URL re-verification, and read-only Vault signal lookup. This
  function mutates nothing and publishes nothing.

- **Evaluation**: see `evals/` - golden tasks covering response-plan count
  and shape, source attribution, domain diversity, the no-client-naming
  rule, severity/taxonomy/playbook-template tagging, and a seeded golden
  task simulating an RIB BI+ / BuildSmart-native BI competitor announcement.

- **Guardrails**: Client names are never emitted; competitor and vendor
  names are always allowed. Evidence grade is carried forward honestly, never
  inflated. `severity` and `playbook_template` are always from their fixed
  sets. Output is a single JSON object, validated against `schema.json`
  before being handed to the brief-rollup step.

---

Reference: the messaging house and CFO voice-of-customer language this
function's response items are checked against lives in docs/positioning.md,
the Tier-2 strategy source of truth published internally alongside
canvasintelligence.com.
