# Skill description — Competitor Scout (function 19-competitor-scout)

- **Purpose**: Proposes organisations that belong in
  `functions/_shared/competitor-register.yaml` but are not in it yet, read
  out of the competitive-intelligence cards the scanners have already
  produced. The register was a fixed list: function 10 could card a
  `new-entrant` and nothing read that card back, so a genuinely new
  competitor sat on the morning brief and never reached the register. This
  function closes that loop with a weekly proposal a person approves.

- **When to invoke**: On the weekly source-discovery cadence, over the
  scanner cards from the window. Not worth invoking when the window carries
  no `new-entrant` cards — the orchestrator skips it rather than spending a
  call to be told there is nothing.

- **When NOT to invoke**: To decide whether a competitor matters — that is
  function 12's job, and severity is function 25's. To edit the register or
  any prompt's naming rule; it proposes, it never promotes. To research an
  organisation; it has no retrieval tools and must never be given any.

- **Inputs**: see `schema.json` — `horizon_days`, `known_competitors` (the
  current register, as `name` + `kind`), and `cards` (scanner cards from the
  window, each with at least `headline` and `source_url`).

- **Tools available**: see `tools.yaml` — none. Giving a competitor proposer
  retrieval would let its own suspicion about a firm cause a request to that
  firm, and let the response justify the suspicion.

- **Evaluation**: see `evals/` — 5 golden tasks covering the empty-list case
  (a quiet week must return zero, never padding), traceability of every
  proposal to an input card, never re-proposing a known competitor, never
  proposing the buyer named in a card, and never proposing an individual.

- **Guardrails**: `candidates` has no minimum, deliberately — a floor would
  manufacture competitors. Every candidate carries the verbatim headline and
  `source_url` of the card that names it, so a reviewer can open the
  evidence rather than take the proposal on trust. Buyers, the Microsoft and
  Sage platforms Canvas is productised on, and named individuals are never
  proposed. Clients are never named in any field. Output is a single JSON
  object validated against `schema.json` before it reaches an approval card.

---

Reference: the messaging house and CFO voice-of-customer language the scans these cards come from are checked against lives in docs/positioning.md, the Tier-2 strategy source of truth published internally alongside canvasintelligence.com.
