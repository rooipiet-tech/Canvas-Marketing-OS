# Skill description — Client Advocacy Harvester (function 26)

- **Purpose**: Turns an approved Fireflies transcript excerpt into a
  client-advocacy/testimonial intake record. It is the single gate a
  proposed quote must clear before it can ever become a draft: a local
  consent-register fixture (modeling `contracts/vault-schema/schema.sql`'s
  `consent_register` table — `data_subject_ref`, `channel`, `purpose`,
  `consented_at`, `revoked_at`) decides whether the quote may be used at
  all, and `docs/permission-register.yaml` decides whether the client behind
  it may ever be named.

- **When to invoke**: When a proposed testimonial or advocacy quote surfaces
  from a call transcript and needs to enter the drafting pipeline; before
  any downstream function (research brief, story editor, case-study writer)
  is handed a quote to work with.

- **When NOT to invoke**: To fetch consent from a live service — no live
  Vault `consent_register` API exists yet; the consent fixture is supplied
  directly in this function's own input. To QA a finished draft for
  publication — that is function 02, Brand Steward QA. To decide client
  clearance in isolation without a proposed quote — that is a direct read
  of `docs/permission-register.yaml`.

- **Inputs**: see `schema.json` — `client_reference`, `proposed_quote`,
  `channel`, `purpose`, optional `pillar`, and (for every eval task) a
  `consent_record` fixture object.

- **Tools available**: see `tools.yaml` — read-only positioning lookup and
  read-only permission-register lookup. No tool here ever calls a live
  Vault API.

- **Evaluation**: see `evals/` — 6 golden tasks covering the happy path
  (active consent, uncleared client written client-free), a revoked-consent
  block, an uncleared-client-in-quote block, a name-absent-from-register
  block treated identically to explicit UNCLEARED, pillar tagging, and South
  African English hygiene.

- **Guardrails**: A `revoked_at` value blocks identically to no consent at
  all, regardless of an earlier `consented_at`. Default deny on client
  naming — absence from the register is never permission. No live Vault
  call. No POPIA or data-residency compliance claim; this function only
  states that it routes through the documented Gatekeeper gate-check.
