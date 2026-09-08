"""Canonical-JSON `resource` claim (AC-06, AC-08) — shared by Gatekeeper
(build side, services/gatekeeper/app/tokens.py) and Publisher (verify
side, services/publisher/app/verifier.py).

contracts/gate-token/schema.json is FROZEN v1 with
`"additionalProperties": false`, and its only optional free-form field is
`resource` (a string). function_id and content_hash therefore cannot
become top-level JWT claims: they are packed into `resource` as CANONICAL
JSON —

    json.dumps({"content_hash": ..., "function_id": ...},
               sort_keys=True, separators=(",", ":"))

— i.e. keys sorted, zero whitespace, deterministic byte-for-byte. A v1
verifier re-serialises the parsed claim and requires byte-equality before
trusting the content_hash, so no whitespace/ordering variance can slip a
different string past a hash comparison.

TD-08 EXTRACTION
-----------------
CANONICAL_JSON_SEPARATORS and the parse/validate logic below used to be
hand-duplicated in both services' files, each carrying a comment that the
two "must stay byte-identical". Gatekeeper's and Publisher's own
`parse_resource_claim` wrappers now delegate to `parse_resource_claim`
here, translating the raised `ValueError` into whichever exception shape
their own call sites expect (Publisher's `VerificationError`, in
particular — see verifier.py). This module raises plain `ValueError`
only; it must not depend on either service's own exception types, so it
stays importable by both without a circular or one-sided dependency.

TD-14 v2 CONTRACT WINDOW
------------------------
contracts/gate-token/v2/schema.json promotes function_id/content_hash to
first-class, required top-level claims — Gatekeeper now issues ONLY this
shape (services/gatekeeper/app/tokens.py no longer calls
canonicalize_resource_claim at all). Publisher's verifier, though, must
still accept a v1 token that could legitimately still be in flight across
an independent Gatekeeper/Publisher deploy — see
extract_function_id_and_content_hash() below, the single place that
shape-detects between the two forms. v1's resource-packing functions
above are kept, unchanged, purely as that v1-compatibility fallback; nothing
about them changes for v2.
"""

from __future__ import annotations

import json

# Any change here is a wire-format change and must not diverge between
# Gatekeeper (build side) and Publisher (verify side) — both now import
# this single value rather than each declaring their own.
CANONICAL_JSON_SEPARATORS = (",", ":")

REQUIRED_RESOURCE_CLAIM_KEYS = frozenset({"content_hash", "function_id"})


def canonicalize_resource_claim(*, content_hash: str, function_id: str) -> str:
    """Canonical-JSON `resource` claim: sorted keys, no whitespace."""
    return json.dumps(
        {"content_hash": content_hash, "function_id": function_id},
        sort_keys=True,
        separators=CANONICAL_JSON_SEPARATORS,
    )


def parse_resource_claim(resource: str) -> dict[str, str]:
    """Parse a `resource` claim, rejecting any non-canonical serialisation.

    The claim is re-serialised and compared byte-for-byte with the string
    that arrived, so no whitespace or key-order variation can be used to
    smuggle a different content_hash past a hash comparison. Raises
    ValueError on any violation (not JSON, wrong shape, or non-canonical
    serialisation) — callers needing a different exception type should
    catch ValueError and re-raise their own (see verifier.py).
    """
    parsed = json.loads(resource)
    if not isinstance(parsed, dict):
        raise ValueError("resource claim must be a JSON object")
    if set(parsed) != set(REQUIRED_RESOURCE_CLAIM_KEYS):
        raise ValueError(
            "resource claim must contain exactly content_hash and function_id, "
            f"got {sorted(parsed)}"
        )
    recanonicalised = json.dumps(parsed, sort_keys=True, separators=CANONICAL_JSON_SEPARATORS)
    if recanonicalised != resource:
        raise ValueError("resource claim is not canonical JSON (byte-equality check failed)")
    return parsed


def extract_function_id_and_content_hash(claims: dict) -> dict[str, str]:
    """Return {"content_hash", "function_id"} from a gate-token claim set,
    accepting EITHER contract version (TD-14's v2 contract window):

      - v2 (contracts/gate-token/v2/schema.json): function_id and
        content_hash are first-class top-level claims. Checked first.
      - v1 (contracts/gate-token/schema.json): both are packed into the
        optional `resource` claim as canonical JSON — see
        parse_resource_claim() above.

    A token carrying exactly one of the two v2 top-level claims (not both)
    is treated as malformed and rejected outright, rather than silently
    falling back to the v1 resource form — that combination is not a valid
    shape under either contract version and should never arise from a
    real issuer. Raises ValueError on any violation, mirroring
    parse_resource_claim()'s own contract, so callers needing a different
    exception type can catch ValueError uniformly (see verifier.py).
    """
    has_function_id = "function_id" in claims
    has_content_hash = "content_hash" in claims
    if has_function_id and has_content_hash:
        return {
            "content_hash": claims["content_hash"],
            "function_id": claims["function_id"],
        }
    if has_function_id or has_content_hash:
        raise ValueError(
            "gate token carries only one of the v2 top-level claims "
            "function_id/content_hash — both must be present together, "
            "or neither (falling back to a v1 resource claim)"
        )
    if "resource" in claims:
        return parse_resource_claim(claims["resource"])
    raise ValueError(
        "gate token carries neither v2 top-level function_id/content_hash "
        "claims nor a v1 resource claim"
    )
