"""AC-06/AC-08 — canonical-JSON `resource` claim, shared logic.

The frozen-schema / JWT round-trip is exercised end-to-end by
services/gatekeeper/tests/test_token_issuance.py (v2 issuance) and
services/publisher/tests/test_verify_alg_pinning.py (v1 acceptance) +
test_verify_v2_gate_token.py (v2 acceptance + dual-support), all of which
now go through this module via each service's own thin wrapper. This
suite covers canonicalize_resource_claim/parse_resource_claim/
extract_function_id_and_content_hash directly.

TD-14 v2 CONTRACT WINDOW: extract_function_id_and_content_hash is the
shared shape-detection helper Publisher's verifier uses to accept EITHER
a v1 (resource-packed) or v2 (top-level) gate token — see that function's
own docstring and services/publisher/app/verifier.py's module docstring
for why both must be accepted.
"""

from __future__ import annotations

import json

import pytest

from governance_lib.resource_claim import (
    CANONICAL_JSON_SEPARATORS,
    canonicalize_resource_claim,
    extract_function_id_and_content_hash,
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


def test_extract_prefers_v2_top_level_claims() -> None:
    claims = {"function_id": FUNCTION_ID, "content_hash": CONTENT_HASH}
    assert extract_function_id_and_content_hash(claims) == {
        "content_hash": CONTENT_HASH,
        "function_id": FUNCTION_ID,
    }


def test_extract_falls_back_to_v1_resource_claim() -> None:
    resource = canonicalize_resource_claim(content_hash=CONTENT_HASH, function_id=FUNCTION_ID)
    claims = {"resource": resource}
    assert extract_function_id_and_content_hash(claims) == {
        "content_hash": CONTENT_HASH,
        "function_id": FUNCTION_ID,
    }


def test_extract_v2_top_level_wins_over_a_stray_resource_claim() -> None:
    """A real v2 issuer never builds a resource claim at all, but if a
    token somehow carried both, the top-level claims are authoritative —
    never the packed string.
    """
    stray_resource = canonicalize_resource_claim(content_hash="f" * 64, function_id="other.fn")
    claims = {
        "function_id": FUNCTION_ID,
        "content_hash": CONTENT_HASH,
        "resource": stray_resource,
    }
    assert extract_function_id_and_content_hash(claims) == {
        "content_hash": CONTENT_HASH,
        "function_id": FUNCTION_ID,
    }


def test_extract_rejects_only_one_of_the_v2_claims() -> None:
    with pytest.raises(ValueError, match="only one"):
        extract_function_id_and_content_hash({"function_id": FUNCTION_ID})
    with pytest.raises(ValueError, match="only one"):
        extract_function_id_and_content_hash({"content_hash": CONTENT_HASH})


def test_extract_rejects_neither_v1_nor_v2_binding() -> None:
    with pytest.raises(ValueError, match="neither"):
        extract_function_id_and_content_hash({"iss": "cmos-gatekeeper"})


def test_extract_v2_allows_empty_content_hash() -> None:
    """Matches app/tokens.py's content_hash=request.content_hash or "" for
    gate tokens authorizing an action with no content to bind.
    """
    claims = {"function_id": FUNCTION_ID, "content_hash": ""}
    assert extract_function_id_and_content_hash(claims) == {
        "content_hash": "",
        "function_id": FUNCTION_ID,
    }
