"""AC-06/AC-08 — canonical-JSON `resource` claim, shared logic.

The frozen-schema / JWT round-trip is exercised end-to-end by
services/gatekeeper/tests/test_token_issuance.py and
services/publisher/tests/test_verify_alg_pinning.py, both of which now go
through this module via each service's own thin wrapper. This suite
covers canonicalize_resource_claim/parse_resource_claim directly.
"""

from __future__ import annotations

import json

import pytest

from governance_lib.resource_claim import (
    CANONICAL_JSON_SEPARATORS,
    canonicalize_resource_claim,
    parse_resource_claim,
)

CONTENT_HASH = "b" * 64
FUNCTION_ID = "publish.social_post"


def test_canonicalize_is_sorted_no_whitespace() -> None:
    resource = canonicalize_resource_claim(content_hash=CONTENT_HASH, function_id=FUNCTION_ID)
    expected = '{"content_hash":"' + CONTENT_HASH + '","function_id":"' + FUNCTION_ID + '"}'
    assert resource == expected
    assert " " not in resource

    # Argument order at the call site cannot change the bytes.
    reordered = canonicalize_resource_claim(function_id=FUNCTION_ID, content_hash=CONTENT_HASH)
    assert reordered == resource


def test_parse_round_trips_a_canonical_claim() -> None:
    resource = canonicalize_resource_claim(content_hash=CONTENT_HASH, function_id=FUNCTION_ID)
    assert parse_resource_claim(resource) == {
        "content_hash": CONTENT_HASH,
        "function_id": FUNCTION_ID,
    }


def test_parse_rejects_non_canonical_whitespace() -> None:
    parsed = {"content_hash": CONTENT_HASH, "function_id": FUNCTION_ID}
    non_canonical = json.dumps(parsed, sort_keys=True)  # default separators add spaces
    with pytest.raises(ValueError, match="canonical"):
        parse_resource_claim(non_canonical)


def test_parse_rejects_reordered_keys() -> None:
    reordered = '{"function_id":"' + FUNCTION_ID + '","content_hash":"' + CONTENT_HASH + '"}'
    with pytest.raises(ValueError, match="canonical"):
        parse_resource_claim(reordered)


def test_parse_rejects_wrong_shape() -> None:
    wrong_shape = json.dumps({"content_hash": CONTENT_HASH}, separators=CANONICAL_JSON_SEPARATORS)
    with pytest.raises(ValueError, match="exactly content_hash and function_id"):
        parse_resource_claim(wrong_shape)


def test_parse_rejects_non_object() -> None:
    not_an_object = json.dumps(["not", "an", "object"], separators=CANONICAL_JSON_SEPARATORS)
    with pytest.raises(ValueError, match="JSON object"):
        parse_resource_claim(not_an_object)


def test_parse_propagates_json_decode_error() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_resource_claim("not json at all")
