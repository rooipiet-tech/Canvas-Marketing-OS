"""Authenticated-principal extraction from Container Apps Easy Auth (AC-32).

The approval-action app sits behind Container Apps' built-in
authentication (Microsoft.App/containerApps/authConfigs with an Entra ID
identity provider and unauthenticatedClientAction=Return401 — see
infra/modules/governance/gatekeeper-approval-app.bicep). The platform
terminates the login and injects the validated identity as request
headers:

    X-MS-CLIENT-PRINCIPAL-ID    stable object id of the signed-in user
    X-MS-CLIENT-PRINCIPAL-NAME  UPN / display name
    X-MS-CLIENT-PRINCIPAL-IDP   identity provider ("aad")
    X-MS-CLIENT-PRINCIPAL       base64 JSON of the full claims blob

The recorded approver is derived from THESE HEADERS ON THIS REQUEST and
never from possession of the approval link. Two different principals
clicking two links must record two different approvers, and no identity
may be cached between requests — hence there is no module-level state in
this file and every helper takes the request's headers as an argument.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Mapping

PRINCIPAL_ID_HEADER = "x-ms-client-principal-id"
PRINCIPAL_NAME_HEADER = "x-ms-client-principal-name"
PRINCIPAL_IDP_HEADER = "x-ms-client-principal-idp"
PRINCIPAL_BLOB_HEADER = "x-ms-client-principal"

# Claim types Entra ID uses for the object id / name inside the blob.
_ID_CLAIM_TYPES = (
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
    "oid",
    "sub",
)
_NAME_CLAIM_TYPES = (
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "name",
    "upn",
)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The identity Easy Auth validated for THIS request."""

    id: str
    name: str | None = None
    idp: str | None = None

    @property
    def decided_by(self) -> str:
        """Value written to gate_decisions.decided_by / approval_inbox."""
        if self.name:
            return f"{self.name} ({self.id})"
        return self.id


def _decode_principal_blob(raw: str) -> dict[str, Any] | None:
    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(padded)
    except (binascii.Error, ValueError):
        return None
    try:
        parsed = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _claims_map(blob: dict[str, Any]) -> dict[str, str]:
    claims: dict[str, str] = {}
    for claim in blob.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        claim_type = claim.get("typ") or claim.get("type")
        value = claim.get("val") or claim.get("value")
        if isinstance(claim_type, str) and isinstance(value, str):
            claims.setdefault(claim_type, value)
    return claims


def principal_from_headers(headers: Mapping[str, str]) -> AuthenticatedPrincipal | None:
    """Return the authenticated principal for this request, or None.

    Headers are read case-insensitively. The dedicated ID/NAME headers win;
    the base64 claims blob is the fallback (some Easy Auth configurations
    inject only the blob).
    """
    lowered = {key.lower(): value for key, value in headers.items()}

    principal_id = (lowered.get(PRINCIPAL_ID_HEADER) or "").strip()
    principal_name = (lowered.get(PRINCIPAL_NAME_HEADER) or "").strip() or None
    idp = (lowered.get(PRINCIPAL_IDP_HEADER) or "").strip() or None

    raw_blob = lowered.get(PRINCIPAL_BLOB_HEADER)
    if raw_blob:
        blob = _decode_principal_blob(raw_blob)
        if blob:
            claims = _claims_map(blob)
            if not principal_id:
                for claim_type in _ID_CLAIM_TYPES:
                    if claims.get(claim_type):
                        principal_id = claims[claim_type]
                        break
            if not principal_name:
                for claim_type in _NAME_CLAIM_TYPES:
                    if claims.get(claim_type):
                        principal_name = claims[claim_type]
                        break
            if not idp:
                candidate = blob.get("auth_typ") or blob.get("identityProvider")
                idp = candidate if isinstance(candidate, str) else None

    if not principal_id:
        return None

    return AuthenticatedPrincipal(id=principal_id, name=principal_name, idp=idp)
