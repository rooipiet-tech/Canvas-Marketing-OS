"""analytics_ingest.google_auth — shared service-account token minting for
the two Google connectors (GA4 Data API, Search Console Search Analytics API).

Written 7 Aug 2026 (F-GOOGLE-LIVE-CLIENTS) alongside ga4_client's and
search_console_client's live paths. Both connectors authenticate the same
way -- a Google service-account JSON key, exchanged for a short-lived OAuth2
access token, sent as a bearer token on a plain REST call -- so the exchange
lives here once rather than twice.

DEPENDENCY CHOICE, deliberate: this uses `google-auth` (the small credential
library) plus the repo's existing httpx, NOT `google-analytics-data` or
`google-api-python-client`. Those SDKs pull in grpc/protobuf and a large
transitive tree for what is, in both cases, a single POST returning JSON.
Keeping the call surface as raw REST also means the request bodies below are
readable against Google's own REST reference without an SDK translation
layer in between.

SECRET SHAPE: GA4_SERVICE_ACCOUNT_KEY / SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY
each hold the FULL service-account JSON key file contents as a single string
(the file Google Cloud downloads when you create a key). Two distinct
secrets, never the coarse google-oauth-client-secret -- AC-25's separation
requirement is preserved exactly as the fixture-era modules established it.
A base64-wrapped value is also accepted, since Key Vault round-trips through
CLI tooling that sometimes encodes multi-line JSON.

FAILURE POSTURE mirrors credentials.resolve_secret: this module raises
GoogleAuthError on a genuinely malformed key or a refused token exchange
(that is a real misconfiguration a nightly run should surface), but it is
never reached at all when the secret is absent -- the callers gate on
is_*_live_mode() first and fall through to fixtures (AC-27).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

# Token lifetime is Google's default (3600s). Nightly runs are single-shot
# and short, so there is no cache here on purpose -- a cache would add
# expiry-handling complexity for a process that mints at most two tokens
# in its whole lifetime.


class GoogleAuthError(RuntimeError):
    """A service-account key was present but unusable, or the token
    exchange was refused. Distinct from 'no secret configured', which is
    handled by the callers' is_*_live_mode() gate and never reaches here."""


def parse_service_account_key(raw: str) -> dict[str, Any]:
    """Parse a service-account key that may be raw JSON or base64-wrapped JSON."""
    candidate = raw.strip()
    if not candidate:
        raise GoogleAuthError("service-account key is empty")

    if not candidate.startswith("{"):
        try:
            candidate = base64.b64decode(candidate, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise GoogleAuthError(
                "service-account key is neither JSON nor valid base64-encoded JSON"
            ) from exc

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise GoogleAuthError("service-account key is not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise GoogleAuthError("service-account key JSON is not an object")

    missing = [f for f in ("client_email", "private_key", "token_uri") if not parsed.get(f)]
    if missing:
        raise GoogleAuthError(
            f"service-account key is missing required field(s): {', '.join(missing)}"
        )
    return parsed


def access_token(raw_key: str, scope: str) -> str:
    """Exchange a service-account key for a short-lived OAuth2 access token.

    google-auth is imported INSIDE the function, mirroring
    credentials.resolve_secret's lazy azure-identity import: fixture-mode
    runs (CI, and any deploy with no Google secret configured) must never
    pay the import cost or fail on a missing optional dependency.
    """
    info = parse_service_account_key(raw_key)

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise GoogleAuthError(
            "google-auth is not installed; it is required for live Google connectors"
        ) from exc

    try:
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[scope])
        credentials.refresh(Request())
    except Exception as exc:  # noqa: BLE001 - surface any refusal identically
        raise GoogleAuthError(f"service-account token exchange failed: {exc}") from exc

    token = credentials.token
    if not token:
        raise GoogleAuthError("service-account token exchange returned no token")
    return str(token)
