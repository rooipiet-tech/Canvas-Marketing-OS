# Gate Token contract — v1

Gate tokens are short-lived JWTs issued by the gate-decision service that
authorize a specific downstream action (e.g. "publish this asset",
"execute this campaign step") against a specific, already-recorded
`gate_decisions` row. See `schema.json` for the required claim set.

## Allowed signing algorithms

Gate tokens MUST be signed using one of the following asymmetric
algorithms only:

- `RS256`
- `ES256`
- `EdDSA`
- `PS256`

No other algorithm is permitted for issuing or verifying a gate token.

## `alg: none` and algorithm-confusion are rejected

Verifiers MUST explicitly pin the expected algorithm (one of the four
above) when validating a gate token and MUST **reject** any token whose
header sets `alg` to `none` — an unsigned token must never be accepted,
regardless of what the payload claims. Verifiers MUST also **reject**
algorithm-confusion attempts (e.g. a token whose header claims an HMAC
algorithm like `HS256` when an RSA/EC public key is configured, or any
mismatch between the verifier's configured algorithm and the token's
`alg` header). Signing keys are asymmetric and issuer-held; verifiers
only ever hold the public key, so an attacker cannot forge a valid
signature even if they control the `alg` header value.

## Required claims (bounded validity, replay prevention, resource binding)

Per `schema.json`'s `required` array, every gate token must carry:

- **`exp`** — a bounded-validity claim. Tokens are short-lived; a resource
  server MUST reject any token where `exp` has passed.
- **`jti`** — a single-use/replay-prevention claim. Resource servers MUST
  track consumed `jti` values for at least the token's validity window
  and reject a token whose `jti` has already been seen.
- **`gate_decision_id`** — a resource/decision-binding claim. This ties
  the token to the exact `gate_decisions` row it authorizes, so a valid
  token for one decision/resource cannot be replayed against a different
  resource.

A verifier that accepts a token missing any of `exp`, `jti`, or
`gate_decision_id`, or that accepts `alg: none`, or that fails to reject
algorithm-confusion, is non-compliant with this contract.
