# Skill description — Source Scout (function 17-source-scout)

- **Purpose**: Proposes candidate sources for one scan profile, from its
  own knowledge, without fetching anything. Scanner profiles ship without
  source urls because nobody has written down where to read each sector
  (functions/_shared/scan-profiles.yaml's header carries the current
  count); this function turns a profile's topic and watchlist prose into a
  list of addresses worth testing. Profiles that watch a named competitor
  set rather than a sector are also given the competitor register, since
  neither their topic nor their watchlist names a competitor.

- **When to invoke**: On the weekly source-discovery cadence, for any
  profile that has no sources or too few; when a profile's watchlist names
  publications in prose that nobody has turned into addresses yet.

- **When NOT to invoke**: To decide whether a source is any good — that is
  the probe's job (measured) and a human's (approved). To fetch anything;
  this function has no retrieval tools at all. To add a source to a scan
  profile or to any allow-list; it proposes, it never promotes.

- **Inputs**: see `schema.json` — `profile_id`, `topic`, optional
  `watchlist_note`, `existing_urls`, `existing_candidates`, `competitors`.
  `competitors` carries `name` and `kind` only, from
  `functions/_shared/competitor-register.yaml`, and is sent only for
  profiles marked `needs_competitor_register` in `scan-profiles.yaml`. The
  register holds no urls on purpose: a newsroom address is a hypothesis
  until the probe measures it, like every other candidate.

- **Tools available**: see `tools.yaml` — none. This function reasons from
  its input and its own knowledge, which is exactly why its output is
  treated as a hypothesis and probed before use.

- **Evaluation**: see `evals/` — 6 golden tasks covering candidate count
  and shape, https-only addresses, honest confidence on reconstructed URL
  patterns, publisher diversity, never re-proposing a source the caller
  already has, and never inventing a newsroom for a competitor entry that
  names a category rather than an organisation.

- **Guardrails**: A proposal is never an assertion that a URL resolves.
  Confidence is about whether the address exists and is what it claims,
  never about how good a source would be, and a guessed feed path is
  always `low`. Personal social profiles are never proposed, and neither
  are LinkedIn company pages: the scan fetches unauthenticated, so such a
  candidate cannot pass its probe and would spend a reviewer's attention
  on a result nobody can act on. A `category` competitor gets no proposal
  at all. Output is a single JSON object validated against `schema.json`
  before it reaches the candidate register.

---

Reference: the messaging house and CFO voice-of-customer language the scans these sources feed are checked against lives in docs/positioning.md, the Tier-2 strategy source of truth published internally alongside canvasintelligence.com.
