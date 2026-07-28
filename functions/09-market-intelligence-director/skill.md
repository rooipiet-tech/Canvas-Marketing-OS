# Skill description — Market Intelligence Director (function 09)

- **Purpose**: Scans a named market topic over a bounded time horizon and
  returns structured, source-attributed signals — each one tagged to a
  Canvas competency pillar and carrying an explicit confidence level. It is
  the evidence supplier for the rest of the Marketing OS: downstream
  content functions are required to cite a signal rather than assert.

- **When to invoke**: Before any content generation that needs a market
  claim; on a scheduled cadence to refresh the signal pool (e.g. Microsoft
  Fabric ecosystem movement, multi-ERP consolidation tenders, CFO reporting
  pain research); when a seller asks "what changed in this space this
  month?".

- **When NOT to invoke**: To write marketing copy of any kind (use function
  42). To judge whether output is publishable or whether a client may be
  named (use function 02, Brand Steward QA). To produce a claim without a
  retrievable source — this function has no path to emit an unattributed
  signal and should not be prompted around that rule.

- **Inputs**: see `schema.json` — `topic`, `horizon_days`, `sources`,
  optional `thin_evidence`.

- **Tools available**: see `tools.yaml` — read-only web/news retrieval and
  read-only Vault signal lookup. This function mutates nothing.

- **Evaluation**: see `evals/` — 5 golden tasks covering signal count,
  source attribution, domain diversity, pillar tagging, confidence
  honesty on thin evidence, and the no-client-naming rule.

- **Guardrails**: Client names are never emitted. Confidence is never
  rounded up. Output is a single JSON object, validated against
  `schema.json` before it is handed to any downstream function.
