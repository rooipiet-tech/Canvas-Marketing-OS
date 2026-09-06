"""SCOPE-005 (zero Postgres in console) + auth header parsing unit test."""

from __future__ import annotations

import base64
import json

import pytest
from conftest import TEST_OPERATORS_GROUP_ID, principal_headers
from fastapi import HTTPException, Request

from app.auth import principal_from_headers, require_principal


def test_principal_from_dedicated_headers() -> None:
    headers = {
        "X-MS-CLIENT-PRINCIPAL-ID": "abc-123",
        "X-MS-CLIENT-PRINCIPAL-NAME": "operator@example.com",
        "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
    }
    principal = principal_from_headers(headers)
    assert principal is not None
    assert principal.id == "abc-123"
    assert principal.name == "operator@example.com"
    assert principal.idp == "aad"
    assert principal.decided_by == "operator@example.com (abc-123)"
    assert principal.groups == ()


def test_principal_from_claims_blob_fallback() -> None:
    blob = {
        "auth_typ": "aad",
        "claims": [
            {"typ": "oid", "val": "blob-obj-id"},
            {"typ": "preferred_username", "val": "blobuser@example.com"},
        ],
    }
    encoded = base64.b64encode(json.dumps(blob).encode("utf-8")).decode("ascii")
    headers = {"X-MS-CLIENT-PRINCIPAL": encoded}
    principal = principal_from_headers(headers)
    assert principal is not None
    assert principal.id == "blob-obj-id"
    assert principal.name == "blobuser@example.com"
    assert principal.idp == "aad"


def test_no_headers_returns_none() -> None:
    assert principal_from_headers({}) is None


def test_principal_groups_claim_collects_every_multi_valued_entry() -> None:
    """TD-04: unlike id/name/idp, "groups" is a repeated claim — one entry
    per membership — and must not collapse to the first value the way
    `_claims_map`'s single-valued lookups do."""
    blob = {
        "claims": [
            {"typ": "oid", "val": "abc-123"},
            {"typ": "groups", "val": "group-a"},
            {"typ": "groups", "val": "group-b"},
            {"typ": "groups", "val": "group-a"},  # duplicate, must dedupe
        ]
    }
    encoded = base64.b64encode(json.dumps(blob).encode("utf-8")).decode("ascii")
    principal = principal_from_headers({"X-MS-CLIENT-PRINCIPAL": encoded})
    assert principal is not None
    assert principal.groups == ("group-a", "group-b")


def _request_with_headers(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_require_principal_rejects_unauthenticated_with_401() -> None:
    with pytest.raises(HTTPException) as excinfo:
        require_principal(_request_with_headers({}))
    assert excinfo.value.status_code == 401


def test_require_principal_rejects_missing_group_with_403(monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_OPERATORS_GROUP_ID", TEST_OPERATORS_GROUP_ID)
    request = _request_with_headers(principal_headers(groups=("some-other-group",)))
    with pytest.raises(HTTPException) as excinfo:
        require_principal(request)
    assert excinfo.value.status_code == 403


def test_require_principal_rejects_when_unconfigured_with_403(monkeypatch) -> None:
    """TD-04: an unset CONSOLE_OPERATORS_GROUP_ID must fail closed (deny
    every principal), never silently degrade back to authentication-only."""
    monkeypatch.delenv("CONSOLE_OPERATORS_GROUP_ID", raising=False)
    request = _request_with_headers(principal_headers())
    with pytest.raises(HTTPException) as excinfo:
        require_principal(request)
    assert excinfo.value.status_code == 403


def test_require_principal_allows_matching_group(monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_OPERATORS_GROUP_ID", TEST_OPERATORS_GROUP_ID)
    request = _request_with_headers(principal_headers())
    require_principal(request)  # must not raise
