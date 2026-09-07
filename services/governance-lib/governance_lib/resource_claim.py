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

— i.e. keys sorted, zero whitespace, deterministic byte-for-byte. Publisher
re-serialises the parsed claim and requires byte-equality before trusting
the content_hash, so no whitespace/ordering variance can slip a different
string past a hash comparison.

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
