"""Vault client extension for the orchestrator's dispatch handlers
(AC-01, AC-19).

vault_client.py (existing, C1) is a best-effort, never-raises status
writer only. This module is a DIFFERENT, additive client the 5 new
dispatch handlers use to make REAL create/get calls against Vault's actual
object-type API (vault/routers/objects.py, contracts/vault-api.yaml,
unfrozen) for /campaigns, /signals, /briefs, /assets and /agent-runs —
every call here is expected to succeed (raises loudly on failure, unlike
vault_client.write_status_best_effort's swallow-everything contract),
because a dispatch handler's whole job IS producing this real artifact
(AC-01's "real downstream effects").

Taxonomy fields (vault/models.py's TAXONOMY_FIELDS): every Vault object
type requires exactly vertical/function_id/campaign/evidence_grade/
consent_status/retention_class. This loop's content is synthetic
marketing copy (DE-2/DE-4) — no data_subject_ref is ever supplied, so
consent_status is always 'not_required' and the consent gate never
engages.

Campaign-per-run: TaskEnvelope.campaign_id (orchestrator/worker.py) is
already a deterministic uuid5 shared by every task decomposed from the
same heartbeat/loop_id pair. get_or_create_campaign() looks up (by name,
the only handle Vault's campaigns list supports) a real Vault campaigns
row named after that deterministic id, creating it once — every
subsequent task in the same run reuses the SAME real campaign_id, which
is what lets AC-10's cost roll-up (costs -> agent_runs -> campaigns) sum
"one full daily loop run"'s total cost with one query.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from typing import Any

import httpx

from orchestrator.clients.azure_fqdn import resolve_live_fqdn
from orchestrator.telemetry_wiring import inject_traceparent

AZURE_CONTAINER_APP = "ca-vault"

DEFAULT_VERTICAL = "marketing-os"
DEFAULT_EVIDENCE_GRADE = "unverified"
DEFAULT_CONSENT_STATUS = "not_required"
DEFAULT_RETENTION_CLASS = "standard_1y"

# Campaigns list is paginated (max 500); this loop only ever needs to find
# ONE recently-created run campaign by name, so a single generous page is
# sufficient for this session's scope (see module docstring's "campaign
# per run" note) — a real query-by-name endpoint doesn't exist in Vault's
# unfrozen API today.
CAMPAIGN_LOOKUP_LIMIT = 500


class VaultClientExtError(RuntimeError):
    """A Vault call the caller needed to succeed did not."""


@lru_cache(maxsize=1)
def resolve_vault_base_url() -> str | None:
    """VAULT_API_URL env override wins (existing orchestrator convention,
    orchestrator/config.py); otherwise resolve ca-vault's real live FQDN
    (AC-19, L-0025).

    PERF-04 defense-in-depth: memoized for the lifetime of the process —
    the primary fix is deploy-time env-var wiring (orchestrator-image.yml),
    but even the CLI-fallback subprocess shell-out should only ever pay its
    cost once per container lifetime, not once per handler call. Call
    resolve_vault_base_url.cache_clear() to force re-resolution (tests
    only; a live container's env vars/live FQDN don't change mid-process)."""
    override = os.environ.get("VAULT_API_URL")
    if override:
        return override
    return resolve_live_fqdn(AZURE_CONTAINER_APP)


def _taxonomy(
    *,
    campaign: str,
    function_id: str,
    vertical: str = DEFAULT_VERTICAL,
    evidence_grade: str = DEFAULT_EVIDENCE_GRADE,
    consent_status: str = DEFAULT_CONSENT_STATUS,
    retention_class: str = DEFAULT_RETENTION_CLASS,
) -> dict[str, Any]:
    return {
        "vertical": vertical,
        "function_id": function_id,
        "campaign": campaign,
        "evidence_grade": evidence_grade,
        "consent_status": consent_status,
        "retention_class": retention_class,
    }


class VaultClientExt:
    def __init__(self, *, base_url: str, timeout: float = 15.0) -> None:
        if not base_url:
            raise VaultClientExtError(
                "VaultClientExt requires a resolved base_url — never a guessed hostname "
                "(L-0025); call resolve_vault_base_url() first"
            )
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VaultClientExt:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=payload, headers=inject_traceparent())
        if response.status_code not in (200, 201):
            raise VaultClientExtError(
                f"POST {path} returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def _get(self, path: str) -> dict[str, Any]:
        response = self._client.get(path, headers=inject_traceparent())
        if response.status_code != 200:
            raise VaultClientExtError(
                f"GET {path} returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def _patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.patch(path, json=payload, headers=inject_traceparent())
        if response.status_code != 200:
            raise VaultClientExtError(
                f"PATCH {path} returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def _list(self, path: str, *, limit: int = 50) -> list[dict[str, Any]]:
        response = self._client.get(
            path, params={"limit": limit}, headers=inject_traceparent()
        )
        if response.status_code != 200:
            raise VaultClientExtError(
                f"GET {path} returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    # -- campaigns --------------------------------------------------------

    def get_or_create_campaign(self, run_name: str) -> str:
        """Returns campaigns.id for `run_name`, creating it if this is the
        first task in this run to ask. See module docstring."""
        existing = self._list("/campaigns", limit=CAMPAIGN_LOOKUP_LIMIT)
        for row in existing:
            if row.get("name") == run_name:
                return str(row["id"])
        created = self._post("/campaigns", {"name": run_name})
        return str(created["id"])

    # -- signals ------------------------------------------------------------

    def create_signal(
        self,
        *,
        source: str,
        signal_type: str,
        payload: dict[str, Any],
        campaign_id: str,
        function_id: str,
    ) -> dict[str, Any]:
        body = {
            "source": source,
            "signal_type": signal_type,
            "payload": payload,
            **_taxonomy(campaign=campaign_id, function_id=function_id),
        }
        return self._post("/signals", body)

    # -- briefs ---------------------------------------------------------

    def create_brief(
        self,
        *,
        title: str,
        body_text: str,
        campaign_id: str,
        function_id: str,
        opportunity_card_id: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "title": title,
            "body": body_text,
            "campaign": campaign_id,
            "opportunity_card_id": opportunity_card_id,
            **_taxonomy(campaign=campaign_id, function_id=function_id),
        }
        return self._post("/briefs", body)

    def get_brief(self, brief_id: str) -> dict[str, Any]:
        return self._get(f"/briefs/{brief_id}")

    # -- signals ------------------------------------------------------------

    def get_signal(self, signal_id: str) -> dict[str, Any]:
        return self._get(f"/signals/{signal_id}")

    # -- agent_runs ---------------------------------------------------------

    def create_agent_run(
        self,
        *,
        agent_name: str,
        campaign_id: str,
        function_id: str,
        status: str = "succeeded",
        input_payload: dict[str, Any] | None = None,
        output_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {
            "agent_name": agent_name,
            "campaign": campaign_id,
            "status": status,
            "input": input_payload or {},
            "output": output_payload or {},
            **_taxonomy(campaign=campaign_id, function_id=function_id),
        }
        return self._post("/agent-runs", body)

    def get_agent_run(self, agent_run_id: str) -> dict[str, Any]:
        return self._get(f"/agent-runs/{agent_run_id}")

    def update_agent_run(
        self,
        agent_run_id: str,
        *,
        status: str | None = None,
        output_payload: dict[str, Any] | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if status is not None:
            body["status"] = status
        if output_payload is not None:
            body["output"] = output_payload
        if completed_at is not None:
            body["completed_at"] = completed_at
        return self._patch(f"/agent-runs/{agent_run_id}", body)

    # -- costs ------------------------------------------------------------

    def get_cost(self, cost_id: str) -> dict[str, Any]:
        """Reads back a single costs row model-gateway's own metering.py
        already wrote (record_completion_costs) -- used to populate a
        dispatch handler's span `cost` attribute with the REAL metered
        usd amount rather than a guess."""
        return self._get(f"/costs/{cost_id}")

    # -- assets ---------------------------------------------------------

    def create_asset(
        self,
        *,
        asset_type: str,
        agent_run_id: str,
        campaign_id: str,
        function_id: str,
        content_bytes: bytes,
        brief_id: str | None = None,
        predecessor_asset_id: str | None = None,
        approval_state: str = "draft",
    ) -> dict[str, Any]:
        body = {
            "asset_type": asset_type,
            "agent_run_id": agent_run_id,
            "campaign": campaign_id,
            "brief_id": brief_id,
            "predecessor_asset_id": predecessor_asset_id,
            "approval_state": approval_state,
            "content_base64": base64.b64encode(content_bytes).decode("ascii"),
            **_taxonomy(campaign=campaign_id, function_id=function_id),
        }
        return self._post("/assets", body)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        return self._get(f"/assets/{asset_id}")

    # Deliberately no create_cost(): model-gateway's own completion.py
    # writes exactly 3 costs rows (usd/tokens/ms) per completion,
    # automatically, keyed by the agent_run_id passed to
    # OrchestratorGatewayClient.complete() — see
    # services/model-gateway/metering.py. Dispatch handlers never write
    # costs rows themselves; they only need a real agent_runs.id to pass
    # to the gateway before calling it.
