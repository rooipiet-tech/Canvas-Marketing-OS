"""mcp-canva dispatch — template-locked creation, bulk CSV, export (AC-16).

Every design-creation tool in this module REQUIRES a template/brand
-template id — no function here permits blank/free-form design creation.
Dual-mode gate: CANVA_CLIENT_ID + CANVA_CLIENT_SECRET (real Key Vault
secret names: canva-client-id, canva-client-secret — per environment
facts, not docs/credentials-runbook.md's stale canva-client-secret-only
name).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp_common import resolve_secret

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

CANVA_API_URL = os.environ.get("CANVA_API_URL", "https://api.canva.com/rest/v1")


def _live_mode() -> bool:
    client_id = resolve_secret("CANVA_CLIENT_ID", "canva-client-id")
    client_secret = resolve_secret("CANVA_CLIENT_SECRET", "canva-client-secret")
    return bool(client_id and client_secret)


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _access_token() -> str | None:
    # Canva Connect API is OAuth2+PKCE; a resolved access token is
    # obtained via a refresh-token exchange (see scripts/oauth_consent.py
    # and canva-refresh-token, which does not exist yet). CANVA_ACCESS_TOKEN
    # is the already-exchanged token, supplied by whatever process performs
    # that exchange — this module never performs the OAuth dance itself.
    return os.environ.get("CANVA_ACCESS_TOKEN")


def _api_request(
    method: str, path: str, *, json_body: dict | None = None, http_client=None
) -> dict:
    import httpx

    client = http_client if http_client is not None else httpx
    headers = {"Authorization": f"Bearer {_access_token()}"}
    response = client.request(
        method, f"{CANVA_API_URL}{path}", json=json_body, headers=headers, timeout=15.0
    )
    return response.json()


# ---------------------------------------------------------------------
# create_design_from_template — template_id is required (AC-16); no
# free-form/blank design creation path exists in this module.
# ---------------------------------------------------------------------


def create_design_from_template(arguments: dict, *, http_client=None) -> dict:
    template_id = arguments.get("template_id")
    if not template_id:
        raise ValueError("template_id is required")
    if not _live_mode():
        fixture = _load_fixture("create_design_from_template")
        return {"source": "fixture", **fixture}
    data = _api_request(
        "POST",
        "/designs",
        json_body={"design_type": {"type": "preset", "name": "custom"}, "asset_id": template_id},
        http_client=http_client,
    )
    return {"source": "live", "result": data}


# ---------------------------------------------------------------------
# bulk_create_from_csv — also template-locked: template_id required.
# ---------------------------------------------------------------------


def bulk_create_from_csv(arguments: dict, *, http_client=None) -> dict:
    template_id = arguments.get("template_id")
    rows = arguments.get("rows", [])
    if not template_id:
        raise ValueError("template_id is required")
    if not _live_mode():
        fixture = _load_fixture("bulk_create_from_csv")
        return {"source": "fixture", **fixture}
    data = _api_request(
        "POST",
        "/autofills",
        json_body={"brand_template_id": template_id, "data": rows},
        http_client=http_client,
    )
    return {"source": "live", "result": data}


# ---------------------------------------------------------------------
# smoke_read_check — NOT exposed as an MCP tool (not in tools.yaml / the
# MCPServer TOOLS list). Used only by smoke_test.py (AC-6), which requires
# a documented read-only (GET-shaped list/read) operation for its single
# live-mode call — never a create/bulk-create/export-write call, and none
# of this module's 3 exposed tools is read-only-shaped. Calls Canva's
# documented "list designs" endpoint (GET /v1/designs).
# ---------------------------------------------------------------------


def smoke_read_check(*, http_client=None) -> dict:
    if not _live_mode():
        return {"source": "fixture", "designs": []}
    data = _api_request("GET", "/designs", http_client=http_client)
    return {"source": "live", "result": data}


# ---------------------------------------------------------------------
# export_design — read/export of an already-created design, not a
# creation tool; no template id required.
# ---------------------------------------------------------------------


def export_design(arguments: dict, *, http_client=None) -> dict:
    design_id = arguments.get("design_id")
    if not design_id:
        raise ValueError("design_id is required")
    if not _live_mode():
        fixture = _load_fixture("export_design")
        return {"source": "fixture", **fixture}
    data = _api_request(
        "POST",
        f"/designs/{design_id}/export",
        json_body={"format": {"type": "png"}},
        http_client=http_client,
    )
    return {"source": "live", "result": data}
