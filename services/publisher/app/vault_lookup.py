"""asset_id -> (content_hash, agent_name) Vault cross-check (plan step 14,
PV3-01; AC-07, AC-08, AC-30).

GENUINELY NEW integration work — not a reuse of anything existing.
PublishRequest's own docstring confirms the pre-session hash-verification
path trusts NO caller-supplied hash and reads NO Vault column at all; the
only existing check is `hashing.recompute_content_hash` over the caller-
supplied raw bytes, compared against the gate token's bound resource
claim (app/routers/publish.py, unchanged, stays primary).

`asset_id` is PublishRequest's existing-but-currently-unused optional
field. When a caller SUPPLIES it, this module:
  1. GETs /assets/{asset_id} (the only Vault lookup its real API supports
     — by uuid, not content_hash, since assets.content_hash carries no
     UNIQUE constraint per the frozen schema).
  2. Cross-checks the returned content_hash against the token-bound hash
     as an ADDITIONAL defense-in-depth check (alongside, never instead
     of, the existing bytes-recompute check).
  3. Reads that asset's agent_run_id -> GET /agent-runs/{id} -> agent_name,
     used to force dry_run=True whenever agent_name==AGENT_NAME_LOOP_PROOF
     regardless of PUBLISHER_DRY_RUN's value (AC-30).

If asset_id is SUPPLIED but the lookup fails/errors/returns something
malformed, this module raises VaultLookupError — the caller (routers/
publish.py) MUST fail closed (refuse to publish) on that, never proceed
as if asset_id had been absent.

If asset_id is ABSENT (every existing caller today, e.g.
caj-governance-smoke, never sets it), routers/publish.py never calls into
this module at all — fully backward-compatible, zero behavior change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


class VaultLookupError(RuntimeError):
    """asset_id was supplied but the Vault lookup failed, errored, or
    returned a malformed/incomplete shape. The caller MUST fail closed —
    refuse to publish — on this, never silently proceed as though
    asset_id had never been supplied."""


@dataclass(frozen=True)
class AssetLookupResult:
    content_hash: str
    agent_name: str
    agent_run_id: str


def vault_base_url() -> str | None:
    return os.environ.get("VAULT_API_URL")


def fetch_asset_and_agent_name(
    asset_id: str,
    *,
    base_url: str | None = None,
    timeout: float = 10.0,
    http_client: httpx.Client | None = None,
) -> AssetLookupResult:
    """Returns (content_hash, agent_name, agent_run_id) for `asset_id`.
    Raises VaultLookupError on ANY failure — network, non-200, or a
    response missing a required field — never returns a partial/guessed
    result.

    `http_client` is injectable (tests pass an httpx.Client wired to
    httpx.MockTransport) so this never needs a live Vault to unit-test.
    """
    resolved = base_url or vault_base_url()
    if not resolved and http_client is None:
        raise VaultLookupError(
            "VAULT_API_URL is not configured — cannot resolve asset_id, failing closed"
        )

    owns_client = http_client is None
    client = http_client or httpx.Client(base_url=(resolved or "").rstrip("/"), timeout=timeout)
    try:
        try:
            asset_response = client.get(f"/assets/{asset_id}")
            if asset_response.status_code != 200:
                raise VaultLookupError(
                    f"GET /assets/{asset_id} returned HTTP {asset_response.status_code}"
                )
            asset = asset_response.json()
            content_hash = asset.get("content_hash")
            agent_run_id = asset.get("agent_run_id")
            if not content_hash or not agent_run_id:
                raise VaultLookupError(
                    f"asset {asset_id} is missing content_hash and/or agent_run_id"
                )

            agent_run_response = client.get(f"/agent-runs/{agent_run_id}")
            if agent_run_response.status_code != 200:
                raise VaultLookupError(
                    f"GET /agent-runs/{agent_run_id} returned HTTP "
                    f"{agent_run_response.status_code}"
                )
            agent_run = agent_run_response.json()
            agent_name = agent_run.get("agent_name")
            if not agent_name:
                raise VaultLookupError(f"agent_run {agent_run_id} is missing agent_name")
        except httpx.HTTPError as exc:
            raise VaultLookupError(f"Vault lookup for asset_id={asset_id} failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    return AssetLookupResult(
        content_hash=str(content_hash), agent_name=str(agent_name), agent_run_id=str(agent_run_id)
    )
