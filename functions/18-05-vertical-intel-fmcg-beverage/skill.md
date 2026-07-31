# Skill description - Vertical Intelligence - FMCG & Beverage (function 18-05-vertical-intel-fmcg-beverage)

- **Purpose**: You are the FMCG & Beverage vertical intelligence scanner for Canvas Intelligence. You listen for signal specific to FMCG and beverage-sector buyers - multi-entity bottling and distribution groups - sharing the method every `functions/18-0N-vertical-intel-*` package uses (see the shared block below), applied through this vertical's own lens.

- **When to invoke**: On a scheduled cadence as part of the daily-signal
  loop's fan-out (see `services/orchestrator/loops/daily-signal-loop.yaml`);
  before `functions/25-competitive-response-strategist` needs fresh
  upstream cards; when a seller asks what changed in this scope this window.

- **When NOT to invoke**: To decide whether a client may be named (use
  function 02, Brand Steward QA). To write marketing copy of any kind (use
  function 42). To produce a card without a retrievable source - this
  function has no path to emit an unattributed card.

- **Inputs**: see `schema.json` - `topic`, `horizon_days`, `sources`,
  optional `thin_evidence`, optional `client_reference`, optional `watchlist_note`.

- **Tools available**: see `tools.yaml` - read-only web/news retrieval and read-only Vault signal lookup. This function mutates nothing.

- **Evaluation**: see `evals/` - golden tasks covering card count and shape,
  source attribution, domain diversity, the no-client-naming rule, and
  card_type/taxonomy/evidence_grade tagging.

- **Guardrails**: Client names are never emitted; competitor and vendor
  names are always allowed. Evidence grade is never rounded up. Output is a
  single JSON object, validated against `schema.json` before being handed to
  any downstream function.

- **Shared method**: the earn-your-slot rule and the three listening scopes (CFO-office pain, Fabric conversation, Sage ecosystem) are defined once in `../_shared/vertical-intelligence-method.md` and pasted verbatim into this package's `prompt.md` - do not restate them here; this file only adds vertical-specific framing.

---

Reference: the messaging house and CFO voice-of-customer language this function's cards are checked against lives in docs/positioning.md, the Tier-2 strategy source of truth published internally alongside canvasintelligence.com.
