# Skill description — Source Discovery & Lifecycle Manager (function 128-source-discovery-lifecycle)

- **Purpose**: Runs the source lifecycle — discover, probe, ratify, score, retire — daily per signal
  class, so a scan profile's sources are a continuously-managed decision rather than a line someone
  edited once. Absorbs the identity of the build-discovered `functions/17-source-scout` package (v3
  §11, Appendix A).

- **When to invoke**: Daily, once per signal class in the v3 §11.2 table. Also invoked for the
  monthly retirement pass over live sources whose yield has fallen below floor.

- **When NOT to invoke**: To add a source directly to a scan profile — that only happens through a
  chosen `source.promote` option or an SP-005 auto-approval, never by this function writing the
  profile itself. To crawl or query beyond `web_search` and `vault_signal_lookup` — the discovery API
  and crawler are Fn 129's to govern and land in Appendix D PR 5c, not here.

- **Inputs**: see `output.schema.json`'s implicit companion input — a `signal_class`, the current
  `scan-profiles.yaml` state for that class, and the retired-source list.

- **Tools available**: see `tools.yaml` — `web_search`, `vault_signal_lookup` and
  `permission_register_lookup`, all read-only. No tool here can write a scan profile, an allow-list
  entry, or the permission register.

- **Evaluation**: see `evals/` — 5 golden tasks covering candidate count and card shape, the
  https-only and client-domain-exclusion rules, mandatory recommendation and distinctness axis, the
  `source.retire` replacement-always rule, and provisional-source labelling.

- **Guardrails**: A candidate without a resolvable probe `evidence_ref` never reaches a card
  (structural answer to `fabricated-proof-point`, same mechanism as every other option card in this
  system — §C3). A client domain is excluded before probing, not filtered after. A retirement always
  carries a replacement option on the same card. At most one card per class per day, at most 3
  candidates per card.
