# Gate Token contract — v2

**Status: contract defined, not yet issued or verified by any service.** This
is the first stage of TD-14's v2 contract window
(`docs/architecture/09-technical-debt.md` TD-14): schema and spec only.
Gatekeeper (issuer) and Publisher (verifier) still build/verify **v1** tokens
exclusively — cutting them over to v2 is a separate, later change (see
"Sequencing" below). `contracts/gate-token/schema.json` and
`contracts/gate-token/spec.md` (v1) are byte-for-byte unchanged by this
window and remain frozen exactly as published, per CLAUDE.md hard rule 1.

This document assumes the reader has read v1's `spec.md`, in particular its
"Required claims" section (unchanged in spirit — `exp`, `jti`,
`gate_decision_id` are still required and still mean exactly what they meant
in v1) and its "Addendum" and "Forward plan" sections, which this document
fulfils.

## What changed from v1

`function_id` and `content_hash` are now **first-class, required top-level
claims** (`schema.json`'s `required` array), not values packed inside the
optional `resource` string as canonical JSON. This removes the debt TD-14
named directly: a v1 verifier had to parse `resource`, re-serialise it with
the same canonical parameters (sorted keys, no whitespace), and reject the
token unless the result was byte-identical to what arrived — logic that had
to be hand-duplicated, byte-identically, in both Gatekeeper
(`services/gatekeeper/app/tokens.py`) and Publisher
(`services/publisher/app/verifier.py`). TD-08 already consolidated that
duplicate logic into one shared implementation
(`services/governance-lib/governance_lib/resource_claim.py`) — v2 goes
further and removes the need for the canonical-JSON packing/parsing
mechanism entirely: `function_id` and `content_hash` are plain top-level
JSON Schema string claims, validated by the schema itself (type + the
`content_hash` regex pattern), with no re-serialisation step required to
trust them.

A v2 verifier MUST still recompute `content_hash` over the raw bytes it is
about to act on rather than trusting the claim value at face value — that
recompute-and-compare step is a security property of the hash binding
itself, independent of which contract version carries the claim, and is
unchanged from v1.

## `resource` is deprecated, not removed

`resource` remains present in `schema.json` v2 as an **optional** string, for
two reasons:

1. **Migration bridge.** A verifier transitioning from v1 to v2 tokens (see
   "Sequencing") may need a window where it still accepts `resource` on a
   still-live v1 token issued moments before cutover, without treating a
   valid v2 token's *absence* of packed data in `resource` as an error.
2. **Free-form resource-binding hint.** v1's original, more general use of
   `resource` — an optional extra hint narrowing what `gate_decision_id`
   authorizes (e.g. a `campaign_id` or `asset_id`) — is retained. Nothing
   about promoting `function_id`/`content_hash` to top-level claims requires
   removing that general-purpose field.

A v2 issuer MUST NOT rely on `resource` to carry `function_id`/`content_hash`
— both are required top-level claims now. A v2 verifier MUST NOT parse
`resource` as canonical JSON or treat it as authoritative for either value;
doing so would silently resurrect the exact byte-equality-parsing debt this
window exists to remove.

## Required claims (v2)

Per `v2/schema.json`'s `required` array:

- **`exp`, `jti`, `gate_decision_id`** — unchanged from v1 (see v1 spec.md).
- **`function_id`** — which governed capability the token authorizes. Was
  optional-via-`resource` in v1; required and top-level in v2.
- **`content_hash`** — the exact bytes the token authorizes publishing.
  Required top-level string matching `^[0-9a-f]{64}$` (a SHA-256 hex
  digest). Was optional-via-`resource` in v1; required and top-level in v2.

A verifier that accepts a v2 token missing any of `exp`, `jti`,
`gate_decision_id`, `function_id`, or `content_hash`, that accepts
`alg: none`, or that fails to reject algorithm-confusion, is non-compliant
with this contract. The signing-algorithm allowlist and `alg`-pinning rules
in v1's spec.md apply unchanged to v2 tokens.

## Sequencing (out of scope for this change)

This PR defines the v2 contract only. Cutting Gatekeeper and Publisher over
to issue/verify v2 tokens — including deciding whether both accept v1 and
v2 simultaneously during a rollout window, keyed off `iss` or a token
version marker, or cut over atomically in one deploy — is the next stage of
this window (TD-14's "consuming-service changes" PR). Nothing in this PR
changes `services/gatekeeper/app/tokens.py` or
`services/publisher/app/verifier.py`.

## Forward-compatibility: `tenant_id` (coordination with TD-05)

`docs/architecture/20-multi-tenancy-decision-memo.md` §2.4 ("What a v2
contract window covers, and what it doesn't need to") explicitly directs a
future `tenant_id` claim into **this same v2 window**, rather than opening a
v3, once TD-05 Part 2's schema-per-tenant migration is dispatched by a
business-owner sign-off (TD-05 Part 1 is not yet approved as of this
change — no tenant work is started here).

This schema is deliberately shaped so that addition is a clean, purely
additive edit to this same file, not a reopening of the window:

1. Add `tenant_id` to `v2/schema.json`'s `properties` as an **optional**
   string claim (not `required` yet). This is additive: every token issued
   before that change remains valid, since `additionalProperties: false`
   only forbids *unknown* properties, not the *absence* of an optional one.
2. Refresh `contracts/.frozen-v2.sha256` via
   `python scripts/validate_contracts.py --write-baseline`, the same
   intentional-refresh mechanism `contracts/vault-schema/schema.sql` already
   used (v1) when `opportunity_cards.pillar`/`so_what`/`source_url`/
   `confidence` were added as nullable columns — an additive, non-breaking
   change to a hash-guarded file is expected to refresh its own baseline,
   not fork a new version namespace.
3. Once every issuer populates `tenant_id` on every token (TD-05 Part 2's
   own migration completes), promote it from `properties`-only to
   `required`, and refresh the v2 baseline again. This is the same
   two-step "additive-then-required" pattern this document already uses for
   `function_id`/`content_hash` graduating out of v1's `resource` packing,
   just run once more, one claim at a time.

**Whoever picks up TD-05 Part 2: extend `contracts/gate-token/v2/` in
place. Do not create a `contracts/gate-token/v3/` for `tenant_id` alone** —
per the multi-tenancy memo, that is duplicate work this window already
planned for.
