"""SCOPE-005 (zero Postgres in console) + auth header parsing unit test."""

from __future__ import annotations

import base64
import json

from app.auth import principal_from_headers


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
