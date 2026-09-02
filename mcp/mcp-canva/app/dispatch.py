"""mcp-canva dispatch — template-locked creation, bulk CSV, export (AC-16).

Every design-creation tool in this module REQUIRES a template/brand
-template id — no function here permits blank/free-form design creation.

DUAL-MODE GATE, CORRECTED 2 Sep 2026 (backlog A3, "wire it").
--------------------------------------------------------------------
Live mode now requires a usable ACCESS TOKEN, not just a client id and
secret. The old gate was `bool(client_id and client_secret)`, and both of
those ARE present in the deployed Container App — infra/main.bicep wires
canva-client-id and canva-client-secret in as Key Vault secretRefs. So
the deployed app believed it was live, while `_access_token()` returned
os.environ["CANVA_ACCESS_TOKEN"], which nothing has ever set. Every live
call would have gone out with the literal header `Authorization: Bearer
None` and come back 401.

That also contradicted the documented behaviour: docs/credentials-runbook.md
says mcp-canva "runs in fixture mode for any call that would require a
live access token" until canva-refresh-token exists. It did not. The code
now does what the runbook always said.

The token itself is now obtained rather than assumed. Canva's Connect API
is OAuth2+PKCE: a human runs scripts/oauth_consent.py once to mint a
refresh token and loads it into Key Vault as canva-refresh-token, and this
module exchanges that refresh token for a short-lived access token. Before
this change nothing performed that exchange, so a correctly-populated
canva-refresh-token would still not have produced a single working call.

OPERATIONAL CAVEAT, STATED RATHER THAN DISCOVERED LATER: Canva rotates
refresh tokens on use, and this module cannot write to Key Vault (secret
loading is the gated in-VNet path, see .compound L-0012). The rotated
token is held in-process for the life of the replica; on restart the
module falls back to the stored canva-refresh-token, which Canva may
already have retired. If live calls start failing with an invalid_grant,
re-run scripts/oauth_consent.py and reload the secret. A persistent
rotation store is the obvious follow-up and is deliberately NOT smuggled
into this change.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from mcp_common import resolve_secret

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

CANVA_API_URL = os.environ.get("CANVA_API_URL", "https://api.canva.com/rest/v1")

# Same endpoint scripts/oauth_consent.py exchanges the authorization code
# against — kept in step with it deliberately.
CANVA_TOKEN_URL = os.environ.get("CANVA_TOKEN_URL", f"{CANVA_API_URL}/oauth/token")

# An autofill job is asynchronous: POST /autofills returns a job, and the
# design id only exists once that job reports success. Bounded polling,
# because an unbounded wait inside an MCP tool call is a hung caller.
AUTOFILL_POLL_ATTEMPTS = 6
AUTOFILL_POLL_INTERVAL_S = 1.0

_TOKEN_LOCK = threading.Lock()
_ROTATED_REFRESH_TOKEN: str | None = None


def _client_credentials() -> tuple[str | None, str | None]:
    return (
        resolve_secret("CANVA_CLIENT_ID", "canva-client-id"),
        resolve_secret("CANVA_CLIENT_SECRET", "canva-client-secret"),
    )


def _refresh_token() -> str | None:
    """The rotated in-process token wins over the stored one — see the
    module docstring's rotation caveat."""
    if _ROTATED_REFRESH_TOKEN:
        return _ROTATED_REFRESH_TOKEN
    return resolve_secret("CANVA_REFRESH_TOKEN", "canva-refresh-token")


def _exchange_refresh_token(http_client=None) -> str | None:
    """refresh_token -> access_token. Returns None on any failure, which
    means the caller falls back to fixture mode rather than issuing a
    request it knows will 401."""
    global _ROTATED_REFRESH_TOKEN

    import httpx

    client_id, client_secret = _client_credentials()
    refresh_token = _refresh_token()
    if not (client_id and client_secret and refresh_token):
        return None

    client = http_client if http_client is not None else httpx
    try:
        response = client.request(
            "POST",
            CANVA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15.0,
        )
        payload = response.json()
    except Exception:  # noqa: BLE001 - a token failure degrades to fixture, never raises
        return None

    access_token = payload.get("access_token")
    if not access_token:
        return None

    rotated = payload.get("refresh_token")
    if rotated and rotated != refresh_token:
        with _TOKEN_LOCK:
            _ROTATED_REFRESH_TOKEN = rotated
    return access_token


def _access_token(http_client=None) -> str | None:
    """An already-exchanged CANVA_ACCESS_TOKEN wins (that is how the smoke
    test and the tests supply one without a network round trip);
    otherwise mint one from the refresh token."""
    direct = os.environ.get("CANVA_ACCESS_TOKEN")
    if direct:
        return direct
    return _exchange_refresh_token(http_client=http_client)


def _live_mode(http_client=None) -> bool:
    """Live ONLY when a usable access token can actually be produced.

    Client id and secret alone are not enough and never were — see the
    module docstring. This is the one gate; every tool below asks it.
    """
    client_id, client_secret = _client_credentials()
    if not (client_id and client_secret):
        return False
    return bool(_access_token(http_client=http_client))


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _api_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    http_client=None,
    access_token: str | None = None,
) -> dict:
    import httpx

    client = http_client if http_client is not None else httpx
    token = access_token if access_token is not None else _access_token(http_client=http_client)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.request(
        method, f"{CANVA_API_URL}{path}", json=json_body, headers=headers, timeout=15.0
    )
    return response.json()


# ---------------------------------------------------------------------
# Canva's autofill data shape
# ---------------------------------------------------------------------
# WHAT WAS WRONG, AND HOW IT WAS ESTABLISHED. bulk_create_from_csv used to
# POST {"brand_template_id": ..., "data": <list of CSV row dicts>}. Canva's
# Connect API does not accept that. `data` is an OBJECT keyed by the brand
# template's own data-field NAMES, and each value is a typed object -
# {"type": "text", "text": "..."} for a text field, {"type": "image",
# "asset_id": "..."} for an image field (video and chart types also exist).
# The template is chosen at the JOB level via brand_template_id, with
# `type` defaulting to "create_from_brand_template". One job produces ONE
# design; there is no bulk endpoint in the Connect API at all - "Bulk
# Create" is a Canva editor feature, not an API one. So N slides means N
# jobs, which is what the loop below does.
#
# EVIDENCE QUALITY, stated plainly because the previous shape was wrong
# precisely through nobody checking: this was established on 2 Sep 2026
# from Canva's own Connect API reference for the create-design-autofill-job
# and get-brand-template-dataset endpoints. www.canva.dev is blocked by
# this environment's egress proxy, so the reference pages were read through
# search-result extracts rather than fetched directly, and no live call has
# ever been made from this repository. The field NAMES are therefore never
# guessed here - _autofill_data() reads them from the template's own
# dataset (GET /brand-templates/{id}/dataset) and matches the CSV columns
# against them, so a template whose fields are named differently than we
# assume still works, and one whose fields cannot be resolved fails loudly
# instead of silently autofilling nothing.

AUTOFILL_TYPE_BRAND_TEMPLATE = "create_from_brand_template"

# The manifest column function 45 emits that is a job-level concern, not a
# per-slide data field. Lifted out of the rows before matching.
MANIFEST_TEMPLATE_COLUMN = "brand_template_id"

# CSV columns whose value names a Canva asset rather than carrying text.
IMAGE_COLUMNS = ("image_ref",)

# KNOWN GAP, STATED RATHER THAN PAPERED OVER (A3, 2 Sep 2026). An image
# data field is autofilled by Canva ASSET ID -- a handle for a file already
# uploaded to Canva. Function 45's manifest puts a filename in image_ref
# ("slide-1.png"), because nothing in this system uploads carousel imagery
# to Canva and no asset-upload path exists. Sending a filename where an
# asset id belongs does not fill an image, it makes Canva reject the whole
# job, which would lose the text slides too. So a value that reads as a
# filename is SKIPPED and named in the result, and the deck autofills its
# text. Closing this properly means an asset-upload step (Canva's asset
# API, scope asset:write) and a manifest that carries the returned ids --
# a separate piece of work, deliberately not smuggled in here.
_FILENAME_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf")


def _looks_like_a_canva_asset_id(value: str) -> bool:
    """A filename is not an asset id. Conservative on purpose: anything
    that is not obviously a local file name is passed through, so a real
    asset id in an unfamiliar format still works."""
    return not value.lower().endswith(_FILENAME_SUFFIXES)


def _dataset_fields(template_id: str, *, http_client=None, access_token=None) -> dict[str, str]:
    """{field name: field type} for a brand template, from Canva.

    Returns {} when the template exposes no dataset or the call fails —
    callers treat that as "cannot resolve field names" and refuse rather
    than posting a data object built from our own column names.
    """
    try:
        payload = _api_request(
            "GET",
            f"/brand-templates/{template_id}/dataset",
            http_client=http_client,
            access_token=access_token,
        )
    except Exception:  # noqa: BLE001 - an unreachable dataset is a refusal, not a crash
        return {}
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        return {}
    fields: dict[str, str] = {}
    for name, spec in dataset.items():
        if isinstance(spec, dict) and isinstance(spec.get("type"), str):
            fields[name] = spec["type"]
    return fields


def _autofill_data(row: dict, fields: dict[str, str]) -> tuple[dict, list[str]]:
    """One CSV row -> (Canva's typed `data` object, skipped field names).

    Only columns the template actually declares are sent. A column the
    template has no field for is dropped rather than invented, because
    Canva rejects the whole job for an unknown field and one stray column
    would otherwise fail every slide.

    Image fields whose value is a filename rather than a Canva asset id
    are skipped and returned in the second element — see the note above
    _looks_like_a_canva_asset_id for why that beats failing the job.
    """
    data: dict[str, dict] = {}
    skipped: list[str] = []
    for name, field_type in fields.items():
        if name not in row:
            continue
        value = row[name]
        if value is None or value == "":
            continue
        if field_type == "image" or name in IMAGE_COLUMNS:
            if not _looks_like_a_canva_asset_id(str(value)):
                skipped.append(name)
                continue
            data[name] = {"type": "image", "asset_id": str(value)}
        else:
            data[name] = {"type": "text", "text": str(value)}
    return data, skipped


def _await_autofill_job(job: dict, *, http_client=None, access_token=None) -> dict:
    """Poll a submitted autofill job to a terminal state.

    Returns the last observed job payload either way — a caller gets the
    design when it succeeded and the failure reason when it did not,
    rather than a timeout with nothing to show.
    """
    import time

    job_id = (job.get("job") or {}).get("id") or job.get("id")
    if not job_id:
        return job
    latest = job
    for _attempt in range(AUTOFILL_POLL_ATTEMPTS):
        status = ((latest.get("job") or {}).get("status")) or latest.get("status")
        if status in ("success", "failed"):
            return latest
        time.sleep(AUTOFILL_POLL_INTERVAL_S)
        try:
            latest = _api_request(
                "GET",
                f"/autofills/{job_id}",
                http_client=http_client,
                access_token=access_token,
            )
        except Exception:  # noqa: BLE001 - report the last good state, never crash
            return latest
    return latest


def _submit_autofill(
    template_id: str, data: dict, *, http_client=None, access_token=None
) -> dict:
    submitted = _api_request(
        "POST",
        "/autofills",
        json_body={
            "type": AUTOFILL_TYPE_BRAND_TEMPLATE,
            "brand_template_id": template_id,
            "data": data,
        },
        http_client=http_client,
        access_token=access_token,
    )
    return _await_autofill_job(submitted, http_client=http_client, access_token=access_token)


# ---------------------------------------------------------------------
# create_design_from_template — template_id is required (AC-16); no
# free-form/blank design creation path exists in this module.
# ---------------------------------------------------------------------


def create_design_from_template(arguments: dict, *, http_client=None) -> dict:
    """One design from one brand template.

    CORRECTED 2 Sep 2026 alongside bulk_create_from_csv. This used to POST
    /designs with {"design_type": {"type": "preset", "name": "custom"},
    "asset_id": template_id}, which creates a blank preset design and
    passes the brand template id in a field meaning "an uploaded asset" —
    it would not have produced a design from the template even if it had
    been authorised. A brand template is instantiated through an autofill
    job, so this is now the single-design case of the same path
    bulk_create_from_csv walks, with whatever data fields the caller
    supplied (none is legitimate: it yields the template's own defaults).
    """
    template_id = arguments.get("template_id")
    if not template_id:
        raise ValueError("template_id is required")
    if not _live_mode(http_client=http_client):
        fixture = _load_fixture("create_design_from_template")
        return {"source": "fixture", **fixture}

    access_token = _access_token(http_client=http_client)
    fields = _dataset_fields(template_id, http_client=http_client, access_token=access_token)
    row = {k: v for k, v in arguments.items() if k not in ("template_id", "title")}
    data, skipped = _autofill_data(row, fields) if fields else ({}, [])
    result = _submit_autofill(
        template_id, data, http_client=http_client, access_token=access_token
    )
    return {"source": "live", "result": result, "skipped_image_fields": sorted(set(skipped))}


# ---------------------------------------------------------------------
# bulk_create_from_csv — also template-locked: template_id required.
# ---------------------------------------------------------------------


def bulk_create_from_csv(arguments: dict, *, http_client=None) -> dict:
    """One autofill job per CSV row, all from one brand template.

    The tool name and its `rows` argument are unchanged — this is still
    "a CSV manifest in, a deck of designs out" from the caller's side, and
    function 45 still produces exactly the manifest it always did. What
    changed is that the row list is no longer handed to Canva verbatim as
    `data`; each row becomes its own job with its own typed data object.
    See the note above _dataset_fields for why.
    """
    template_id = arguments.get("template_id")
    rows = arguments.get("rows", [])
    if not template_id:
        raise ValueError("template_id is required")
    if not _live_mode(http_client=http_client):
        fixture = _load_fixture("bulk_create_from_csv")
        return {"source": "fixture", **fixture}

    access_token = _access_token(http_client=http_client)
    fields = _dataset_fields(template_id, http_client=http_client, access_token=access_token)
    if not fields:
        # Refusing beats guessing. Posting our own CSV column names as
        # Canva field names is how a job silently produces a deck of empty
        # slides -- the failure this whole reconciliation exists to avoid.
        raise ValueError(
            f"brand template {template_id} exposed no autofill dataset; "
            "cannot map manifest columns to Canva data fields"
        )

    jobs: list[dict] = []
    skipped: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload_row = {k: v for k, v in row.items() if k != MANIFEST_TEMPLATE_COLUMN}
        data, row_skipped = _autofill_data(payload_row, fields)
        skipped.extend(row_skipped)
        jobs.append(
            _submit_autofill(
                template_id,
                data,
                http_client=http_client,
                access_token=access_token,
            )
        )
    return {
        "source": "live",
        "result": {"jobs": jobs, "designs_requested": len(jobs)},
        # Named, never silent: the caller logs these so a text-only deck
        # is a visible outcome rather than a mystery.
        "skipped_image_fields": sorted(set(skipped)),
    }


# ---------------------------------------------------------------------
# smoke_read_check — NOT exposed as an MCP tool (not in tools.yaml / the
# MCPServer TOOLS list). Used only by smoke_test.py (AC-6), which requires
# a documented read-only (GET-shaped list/read) operation for its single
# live-mode call — never a create/bulk-create/export-write call, and none
# of this module's 3 exposed tools is read-only-shaped. Calls Canva's
# documented "list designs" endpoint (GET /v1/designs).
# ---------------------------------------------------------------------


def smoke_read_check(*, http_client=None) -> dict:
    if not _live_mode(http_client=http_client):
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
    if not _live_mode(http_client=http_client):
        fixture = _load_fixture("export_design")
        return {"source": "fixture", **fixture}
    data = _api_request(
        "POST",
        f"/designs/{design_id}/export",
        json_body={"format": {"type": "png"}},
        http_client=http_client,
    )
    return {"source": "live", "result": data}


def _reset_rotated_refresh_token_for_tests() -> None:
    """Test-only: clears the in-process rotated token so one test's
    rotation cannot leak into the next."""
    global _ROTATED_REFRESH_TOKEN
    with _TOKEN_LOCK:
        _ROTATED_REFRESH_TOKEN = None


__all__ = [
    "bulk_create_from_csv",
    "create_design_from_template",
    "export_design",
    "smoke_read_check",
]
