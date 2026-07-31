# Development signing key — NOT A SECRET, NOT FOR PRODUCTION

**`dev-signing-key.priv` in this directory is a committed private key. It is
public by definition, it confers NO security whatsoever, and it must never
be used in production.**

Anyone who can read this repository can read the private key and can
therefore forge a valid signature over any `registry.json`. A signature made
with this key proves only that *some* build ran — it proves nothing about
who ran it or whether the content is trustworthy.

## Why it is committed anyway

The registry artefact must carry a detached signature that CI and any
consumer can verify without network access or credentials. Per
`contracts/gate-token/spec.md`, signing in this system is asymmetric-only
(no `alg: none`, no HMAC/shared secret), so a keypair has to exist. The
production key belongs in Key Vault, but Key Vault's public network access
is Disabled in `cmos-dev` and there is no in-VNet CI runner, so it is not
reachable from this scope.

Committing a clearly-labelled **development** asymmetric keypair is not the
same thing as committing a client secret: there is nothing to leak that is
not already intended to be public, and the code path that consumes it is
identical to the one a real key will use.

## Files

| File | Contents |
|---|---|
| `dev-signing-key.priv` | Ed25519 private key, PEM (PKCS#8), unencrypted. **Non-production.** |
| `dev-signing-key.pub` | Ed25519 public key, PEM (SubjectPublicKeyInfo). |

Public key fingerprint (first 16 hex chars of the raw key): `4f54bda171d8095d`.

## Swapping in a real key (config change, not a code change)

`services/registry/signing.py` resolves keys env-var-first:

```sh
export REGISTRY_SIGNING_KEY_PATH=/run/secrets/registry-signing-key.pem
export REGISTRY_SIGNING_PUBLIC_KEY_PATH=/run/secrets/registry-signing-key.pub
python services/registry/build_registry.py --sign --out dist/
```

- A value beginning with `keyvault://` is recognised and **fails loudly**
  rather than silently falling back to this dev key.
- Whenever the fallback to this dev key is actually used, the tooling prints
  an explicit `WARNING:` line to stderr on every run. Seeing that warning in
  a production pipeline means the artefact is unsigned in every sense that
  matters.

The Key Vault-held production signing key is recorded as a named follow-up in
`docs/accepted-risks.md`.

## Algorithm

Ed25519 only. `signing.py` type-checks the loaded key object and rejects any
non-Ed25519 key; there is no `alg` field to downgrade and no HMAC or
`alg: none` acceptance path anywhere in the verification code.
