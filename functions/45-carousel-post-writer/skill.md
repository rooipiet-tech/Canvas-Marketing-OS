# Skill description — Carousel/Document Post Writer (function 45)

- **Purpose**: Writes a multi-slide LinkedIn carousel/document post — one
  client-free proof point per slide, closing on the roof line "Your Data.
  Delivered." — and produces the Canva Bulk Create CSV manifest describing
  every slide, so the deck can be built mechanically. Canva generation here
  is always a locally-produced, locally-validated CSV manifest; there is no
  live mcp-canva call anywhere in this package. Since A3 (2 Sep 2026) that
  manifest is no longer the end of the line — the orchestrator's carousel
  handler parses it and calls mcp-canva's `bulk_create_from_csv` once the
  draft is written, so the deck is built from it rather than by hand.

- **When to invoke**: To turn a set of approved proof points into a
  carousel/document post for LinkedIn; to produce the Bulk Create manifest a
  design tool consumes to build the slide deck; to validate an
  already-produced manifest's shape before it is handed downstream.

- **When NOT to invoke**: To decide whether a client may be named — that is
  a direct read of `docs/permission-register.yaml` (default deny). To call a
  live Canva API — no such call exists in this function; every Canva
  interaction here is a local CSV fixture. To write a single-image or
  text-only post — that is function 42 or function 39.

- **Inputs**: see `schema.json` — `pillar`, `proof_points`, `campaign`,
  optional `client_reference`, plus a QA-only `csv_manifest_text` /
  `expected_row_count` pair used solely by the manifest-shape golden eval.

- **Tools available**: see `tools.yaml` — read-only positioning lookup,
  read-only permission-register lookup, and a `permissions: none`
  fixture-only Canva Bulk Create manifest shape validator. No tool here ever
  calls a live Canva API.

- **Evaluation**: see `evals/` — 6 golden tasks covering the baseline
  carousel-with-CSV happy path, a malformed manifest (bad header row or
  mismatched row count) being rejected, the roof-line/CTA rule, verbatim
  pillar naming, South African English hygiene, and the client-naming block.

- **Guardrails**: One proof point per slide, never fabricated. Roof line
  always closes the final slide. No client name unless CLEARED. No link
  shortener. Canva Bulk Create CSV manifest header and row count are checked
  mechanically before the manifest is handed downstream — a shape mismatch
  is a blocking failure, never a warning.
