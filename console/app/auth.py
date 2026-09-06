"""Authenticated-principal extraction from Container Apps Easy Auth, plus
the RISK-003 code-level auth backstop every console route depends on
(`require_principal`).

The console sits behind Container Apps' built-in authentication
(Microsoft.App/containerApps/authConfigs with an Entra ID identity
provider and globalValidation.unauthenticatedClientAction='Return401' —
see infra/modules/console/console-app.bicep). The platform terminates the
login and injects the validated identity as request headers:

    X-MS-CLIENT-PRINCIPAL-ID    stable object id of the signed-in user
    X-MS-CLIENT-PRINCIPAL-NAME  UPN / display name
    X-MS-CLIENT-PRINCIPAL-IDP   identity provider ("aad")
    X-MS-CLIENT-PRINCIPAL       base64 JSON of the full claims blob

This module is a fresh reimplementation for the console (this session
observed, read-only, session/s4-governance's structurally identical
gatekeeper auth.py as a non-authoritative shape reference — no code is
copied, no import/dependency on that uncommitted service exists here).
The recorded operator identity is derived from THESE HEADERS ON THIS
REQUEST only, matching GOAL-004's requirement that the kill-switch audit
record the operator identity read from Easy Auth headers.

TD-04 / SEC-2 (console authenticates but does not authorise): a security
group membership requirement, not an app-role claim. Every Container Apps
`authConfigs` schema this repo could find a property reference for (2022-
03-01 through the 2024-10-02-preview this app actually uses) exposes
exactly two authorization primitives for the Azure AD provider —
`defaultAuthorizationPolicy.allowedApplications`/`.allowedPrincipals`
(`identities`, `groups`) and `jwtClaimChecks.allowedClientApplications`/
`.allowedGroups` — and no `roles` field anywhere. Microsoft's own docs
confirm this is by design, not an omission: "The Container Apps
authentication layer doesn't perform the [role] validation steps... In the
target Container Apps code, validate that expected roles are now present
in the token" (learn.microsoft.com/azure/container-apps/
authentication-entra#configure-client-apps-to-access-your-container-app).
So the enforceable primitive at the IaC layer is a **security-group**
claim, not an app-role claim — `consoleAuth`'s `validation.
defaultAuthorizationPolicy.allowedPrincipals.groups` in console-app.bicep
now requires `consoleOperatorsGroupId`. `require_principal` below
duplicates that exact same check in code (RISK-003's established
pattern: the same signal, verified again at the app layer, so infra drift
or misconfiguration fails closed instead of silently reopening the gap) —
both sides read the identical `CONSOLE_OPERATORS_GROUP_ID` value, sourced
from the one Bicep parameter, so the two checks cannot drift apart.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from fastapi import HTTPException, Request

from app.config import get_settings

logger = logging.getLogger(__name__)

PRINCIPAL_ID_HEADER = "x-ms-client-principal-id"
PRINCIPAL_NAME_HEADER = "x-ms-client-principal-name"
PRINCIPAL_IDP_HEADER = "x-ms-client-principal-idp"
PRINCIPAL_BLOB_HEADER = "x-ms-client-principal"

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
# Entra ID v2.0 tokens emit the security-group membership claim as "groups";
# the v1.0/ADFS-style claim type is kept as a fallback since Easy Auth's
# claims blob shape is not itself frozen by any contract in this repo.
_GROUP_CLAIM_TYPES = (
    "groups",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The identity Easy Auth validated for THIS request."""

    id: str
    name: str | None = None
    idp: str | None = None
    groups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def decided_by(self) -> str:
        """Value recorded as the operator identity on a kill-switch audit entry."""
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


def _claim_values(blob: dict[str, Any], claim_types: tuple[str, ...]) -> tuple[str, ...]:
    """Every value of a (possibly repeated) claim type, in order, deduped.

    Unlike `_claims_map`, this does not collapse to the first value —
    a multi-valued claim like "groups" carries one entry per membership.
    """
    values: dict[str, None] = {}
    for claim in blob.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        claim_type = claim.get("typ") or claim.get("type")
        value = claim.get("val") or claim.get("value")
        if claim_type in claim_types and isinstance(value, str):
            values.setdefault(value, None)
    return tuple(values)


def principal_from_headers(headers: Mapping[str, str]) -> AuthenticatedPrincipal | None:
    """Return the authenticated principal for this request, or None.

    Headers are read case-insensitively. The dedicated ID/NAME headers win;
    the base64 claims blob is the fallback (some Easy Auth configurations
    inject only the blob). Security-group membership has no dedicated
    header at all — Easy Auth only ever surfaces it inside the blob.
    """
    lowered = {key.lower(): value for key, value in headers.items()}

    principal_id = (lowered.get(PRINCIPAL_ID_HEADER) or "").strip()
    principal_name = (lowered.get(PRINCIPAL_NAME_HEADER) or "").strip() or None
    idp = (lowered.get(PRINCIPAL_IDP_HEADER) or "").strip() or None
    groups: tuple[str, ...] = ()

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
            groups = _claim_values(blob, _GROUP_CLAIM_TYPES)

    if not principal_id:
        return None

    return AuthenticatedPrincipal(id=principal_id, name=principal_name, idp=idp, groups=groups)


def require_principal(request: Request) -> None:
    """RISK-003 code-level backstop, now covering authorization as well as
    authentication.

    The Bicep-wired Easy Auth ingress layer is correct and sufficient on
    its own — every console route is reachable only behind it, and
    `consoleAuth`'s `allowedPrincipals.groups` already requires
    `CONSOLE_OPERATORS_GROUP_ID` membership before a request ever reaches
    this container (see this module's header comment, and TD-04 /
    docs/accepted-risks.md's "console Easy Auth authenticates but does not
    yet authorize by operator"). This function adds the cheap, code-level
    duplicate of that same check so a route still fails closed if infra
    ever drifts or is misconfigured — an unauthenticated request gets 401,
    an authenticated request lacking the required group gets 403, and
    neither ever depends solely on the ingress layer being right.
    """
    principal = principal_from_headers(request.headers)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")

    required_group = get_settings().console_operators_group_id
    if not required_group:
        # Fail closed, not open: a missing config value must never be read
        # as "authorization not required here" (the exact TD-04 failure
        # mode this whole check exists to close). Loud (WARNING), not
        # silent, so a forgotten CONSOLE_OPERATORS_GROUP_ID is diagnosable.
        logger.warning(
            "CONSOLE_OPERATORS_GROUP_ID is not configured — denying principal %s "
            "(fail-closed; see console/app/auth.py::require_principal)",
            principal.id,
        )
        raise HTTPException(status_code=403, detail="console operator authorization not configured")

    if required_group not in principal.groups:
        raise HTTPException(status_code=403, detail="console operator group membership required")
