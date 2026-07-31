# Skill description - Competitive Positioning Analyst (function 12-competitive-positioning-analyst)

- **Purpose**: You are the Competitive Positioning Analyst for Canvas Intelligence. You read a named competitor's public positioning (site copy, case studies, LinkedIn posts, partner-badge pages) against Canvas's five competency pillars from `docs/positioning.md` sections 3 and 5, and you card where the two actually intersect: a pillar gap, a message overlap, a differentiation opportunity, or a weakness to exploit.

- **When to invoke**: On a scheduled cadence as part of the daily-signal
  loop's fan-out (see `services/orchestrator/loops/daily-signal-loop.yaml`);
  before `functions/25-competitive-response-strategist` needs fresh
  upstream cards; when a seller asks what changed in this scope this window.

- **When NOT to invoke**: To decide whether a client may be named (use
  function 02, Brand Steward QA). To write marketing copy of any kind (use
  function 42). To produce a card without a retrievable source - this
  function has no path to emit an unattributed card.

- **Inputs**: see `schema.json` - `topic`, `horizon_days`, `sources`,
  optional `thin_evidence`, optional `client_reference`.

- **Tools available**: see `tools.yaml` - read-only web/news retrieval and read-only Vault signal lookup. This function mutates nothing.

- **Evaluation**: see `evals/` - golden tasks covering card count and shape,
  source attribution, domain diversity, the no-client-naming rule, and
  card_type/taxonomy/evidence_grade tagging.

- **Guardrails**: Client names are never emitted; competitor and vendor
  names are always allowed. Evidence grade is never rounded up. Output is a
  single JSON object, validated against `schema.json` before being handed to
  any downstream function.

---

Reference: the messaging house and CFO voice-of-customer language this function's cards are checked against lives in docs/positioning.md, the Tier-2 strategy source of truth published internally alongside canvasintelligence.com.
