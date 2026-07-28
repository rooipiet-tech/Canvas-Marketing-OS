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

## Addendum — `function_id` and `content_hash` in v1 (additive, non-breaking)

This section is **documentation only**. It adds no claim, changes no
required claim, and leaves `schema.json` and the `$id` version segment
(`/v1/`) byte-for-byte unchanged. It records a convention that fits
entirely inside the existing `resource` string so that the frozen v1
contract does not have to move.

Two values are load-bearing for the publish gate but have no top-level
claim in v1, because `schema.json` sets `"additionalProperties": false`
and cannot gain properties without breaking the freeze:

- **`function_id`** — which governed capability the token authorizes.
- **`content_hash`** — the exact bytes the token authorizes publishing.

For v1, both are carried inside the optional `resource` claim as
**canonical JSON**: object keys sorted, no whitespace, deterministic
byte-for-byte. Concretely, the issuer produces the `resource` string as

```python
json.dumps(
    {"content_hash": ..., "function_id": ...},
    sort_keys=True,
    separators=(",", ":"),
)
```

which yields exactly:

```json
{"content_hash":"<hex sha-256>","function_id":"<function id>"}
```

Canonicalisation is a security property, not a formatting preference. A
verifier MUST parse the `resource` claim, re-serialise it with the same
canonical parameters, and **reject the token** unless the result is
byte-identical to the string that arrived. Without that check, two
different serialisations of the same object (differing whitespace or key
order) would compare unequal — or a semantically different one could
compare equal — in a hash-binding comparison. The verifier MUST also
recompute the content hash over the raw bytes it is about to act on
rather than trusting any caller-supplied or stored hash value.

The approver identity remains resolved server-side via
`gate_decision_id` -> `gate_decisions.decided_by` rather than as a claim,
so a token never carries a human identity it could leak.

### Forward plan

At the **first v2 contract window**, `function_id` and `content_hash`
graduate to first-class top-level claims with their own schema
definitions, and the `resource` packing described here becomes the
deprecated v1 compatibility form. That change requires a new
`/v2/`-namespaced `$id` and a fresh frozen baseline; it is explicitly out
of scope for v1, which stays frozen exactly as published.
