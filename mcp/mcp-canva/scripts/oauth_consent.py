#!/usr/bin/env python3
"""Canva OAuth2 + PKCE consent helper (standalone, one-time-use).

Canva's Connect API uses OAuth2 with PKCE. The canva-refresh-token Key
Vault secret does not exist yet — this script is how a human operator
performs the one-time authorization-code + PKCE exchange to obtain it.
Its absence never breaks anything else in this build: mcp-canva runs
happily in fixture mode with no CANVA_ACCESS_TOKEN/refresh token present
at all (AC-4/AC-7).

Usage:
    python scripts/oauth_consent.py --help
    python scripts/oauth_consent.py \\
        --client-id <canva-client-id value> \\
        --client-secret <canva-client-secret value> \\
        --scopes design:content:read design:content:write

Redirect URI is fixed to http://127.0.0.1:8484/oauth/redirect (must match
the redirect URI registered on the Canva developer app, per environment
facts). This script:
  1. Generates a PKCE code_verifier/code_challenge pair.
  2. Prints the authorization URL for the operator to open in a browser.
  3. Runs a local HTTP server on 127.0.0.1:8484 to catch the redirect and
     capture the `code` query parameter.
  4. Exchanges the code for tokens (client credentials + code_verifier).
  5. Prints the resulting refresh_token — the operator then loads it into
     Key Vault as canva-refresh-token via the existing gated in-VNet
     secret-loading path (see .compound learning L-0012's recommendation),
     never via this script directly (this script never touches Key Vault).

This script makes no network call at all until an operator explicitly
runs it with real credentials and completes the browser consent step —
running it with --help never attempts anything requiring
canva-refresh-token or any other pre-existing secret.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import secrets
import sys
import threading
import urllib.parse
from dataclasses import dataclass

REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8484
REDIRECT_PATH = "/oauth/redirect"
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"

AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

DEFAULT_SCOPES = ["design:content:read", "design:content:write"]


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@dataclass
class _CapturedCode:
    code: str | None = None
    error: str | None = None


def _build_authorize_url(client_id: str, scopes: list[str], state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _run_redirect_server(expected_state: str, captured: _CapturedCode) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != REDIRECT_PATH:
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(parsed.query)
            state = query.get("state", [None])[0]
            if state != expected_state:
                captured.error = "state mismatch - possible CSRF, aborting"
            else:
                captured.code = query.get("code", [None])[0]
                captured.error = query.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Canva consent captured. You may close this tab.")

        def log_message(self, *args, **kwargs) -> None:  # silence default logging
            pass

    server = http.server.HTTPServer((REDIRECT_HOST, REDIRECT_PORT), Handler)
    server.timeout = 300
    server.handle_request()


def _exchange_code_for_tokens(
    client_id: str, client_secret: str, code: str, code_verifier: str
) -> dict:
    import httpx

    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--client-id", help="canva-client-id secret value.")
    parser.add_argument("--client-secret", help="canva-client-secret secret value.")
    parser.add_argument(
        "--scopes",
        nargs="+",
        default=DEFAULT_SCOPES,
        help=f"OAuth scopes to request (default: {' '.join(DEFAULT_SCOPES)}).",
    )
    args = parser.parse_args(argv)

    if not args.client_id or not args.client_secret:
        parser.error("--client-id and --client-secret are required to run the OAuth flow")

    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    url = _build_authorize_url(args.client_id, args.scopes, state, challenge)
    print("Open this URL in a browser to authorize mcp-canva:")
    print(f"  {url}")
    print(f"Waiting for the redirect on {REDIRECT_URI} (5 minute timeout)...")

    captured = _CapturedCode()
    server_thread = threading.Thread(target=_run_redirect_server, args=(state, captured))
    server_thread.start()
    server_thread.join()

    if captured.error:
        print(f"authorization failed: {captured.error}", file=sys.stderr)
        return 1
    if not captured.code:
        print("no authorization code received (timed out?)", file=sys.stderr)
        return 1

    tokens = _exchange_code_for_tokens(args.client_id, args.client_secret, captured.code, verifier)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("token exchange succeeded but no refresh_token was returned", file=sys.stderr)
        return 1

    print("Success. Store this value as the canva-refresh-token Key Vault secret")
    print("(via the gated in-VNet secret-loading path — never paste it into a shell")
    print("history or commit it):")
    print(refresh_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
