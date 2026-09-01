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


# PERF-01 (performance lens, major): get_or_create_campaign() previously did
# an uncached full list=500 fetch + linear scan on every single call, and
# dispatch.py's handlers construct a brand-new VaultClientExt per task
# invocation (build_vault_client()), so an instance attribute would not
# help -- a fresh VaultClientExt is exactly as uncached as no cache at all.
# envelope.campaign_id (and therefore _campaign_name(envelope)'s
# "run-{campaign_id}" derived name) is a stable per-run key, called 5x
# per full daily-loop run (once each from ingest-signals/draft-brief/
# draft-content, twice from qa-review's two loop positions) -- every call
# after the first within the same run is a pure redundant round trip.
# Process-lifetime memoization here (module-level, keyed on run_name) is
# the natural scope: it survives across the many short-lived
# VaultClientExt instances one dispatch worker process creates over a run,
# and low cardinality (one distinct run_name per real day) means it never
# grows unbounded. clear_campaign_cache() exists for tests only -- a real
# worker process never needs to invalidate it (a run_name's campaign never
# changes once created).
_CAMPAIGN_ID_CACHE: dict[str, str] = {}


def clear_campaign_cache() -> None:
    """Test-only: drop all memoized run_name -> campaign_id entries."""
    _CAMPAIGN_ID_CACHE.clear()


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
        response = self._client.get(path, params={"limit": limit}, headers=inject_traceparent())
        if response.status_code != 200:
            raise VaultClientExtError(
                f"GET {path} returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    # -- campaigns --------------------------------------------------------

    def get_or_create_campaign(self, run_name: str, *, function_id: str) -> str:
        """Returns campaigns.id for `run_name`, creating it if this is the
        first task in this run to ask. See module docstring and PERF-01's
        module-level _CAMPAIGN_ID_CACHE note above: a cache hit skips the
        list=500 fetch + linear scan (and the create POST) entirely.

        INCIDENT (2026-08-03, live — escalation 10, deploy-loop-e2e-smoke
        #15): this was the one Vault-writing call in this whole client
        that never used the module's own `_taxonomy()` helper every other
        create_*() method already does — the POST body was bare
        `{"name": run_name}`, missing all 6 of vault/models.py's
        TAXONOMY_FIELDS.

        FOLLOW-UP INCIDENT #1 (2026-08-03, same day, live — escalation 11,
        deploy-loop-e2e-smoke #17): the first fix passed `campaign=run_name`
        into `_taxonomy()`, but Vault casts that field straight to SQL
        `uuid` — `run_name` (`f"run-{campaign_id}"`, see `_campaign_name()`)
        isn't one.

        FOLLOW-UP INCIDENT #2 (2026-08-03, same day, live — escalation 12,
        deploy-loop-e2e-smoke #18): passing orchestrator's OWN deterministic
        `envelope.campaign_id` as the taxonomy `campaign` value (a valid
        UUID, fixing incident #1's symptom) still 422'd — this time with a
        Postgres FK violation (`object_taxonomy_campaign_id_fkey`), because
        that UUID has never been INSERTed into `public.campaigns` yet; it's
        purely an orchestrator-internal per-run key, never a real Vault
        campaigns.id. vault/routers/objects.py's insert_object() docstring
        is explicit that this is BY DESIGN, not a bug to work around
        server-side: "no special-casing... the submitted value must
        reference an existing campaigns row or the create fails... exactly
        as for signals/gate_decisions/costs/consent_register" — a prior
        Vault version DID auto-substitute the new row's own id here and was
        deliberately reverted (breaks AC-002's taxonomy round-trip
        guarantee). Vault's OWN smoke test (test_contract_smoke.py's
        create_campaign()) documents the correct client pattern: reuse ANY
        already-existing campaign's id as the taxonomy anchor — a
        campaign's taxonomy.campaign field is not semantically "which
        campaign do I belong to" (a campaign doesn't belong to a parent
        campaign), it just needs some real row to reference to satisfy the
        FK, uniformly with every other object type. Vault's own docstring
        also names the one gap this can't paper over: "only the very first
        campaign ever created against a completely empty database has
        nothing to reference... the generic create-object path has no
        bootstrap special-case of its own, by design" — confirmed NOT the
        case in cmos-dev live (Log Analytics shows multiple prior real
        `201 Created` /campaigns responses), so an anchor always exists
        here; a genuinely empty environment would need a one-time DB-level
        seed (matching Vault's own test's `db_conn` bootstrap branch), not
        an application-code workaround.
        """
        cached = _CAMPAIGN_ID_CACHE.get(run_name)
        if cached is not None:
            return cached
        existing = self._list("/campaigns", limit=CAMPAIGN_LOOKUP_LIMIT)
        for row in existing:
            if row.get("name") == run_name:
                campaign_id = str(row["id"])
                _CAMPAIGN_ID_CACHE[run_name] = campaign_id
                return campaign_id
        anchor = existing[0]["id"] if existing else None
        if anchor is None:
            raise VaultClientExtError(
                "get_or_create_campaign: no existing campaigns row to anchor the new "
                "campaign's taxonomy.campaign field to (Vault's object-taxonomy FK requires "
                "referencing a real, already-existing campaigns.id — see this method's own "
                "docstring). This is a genuine first-ever-bootstrap gap in a completely empty "
                "Vault database, not something this client can work around; seed one campaign "
                "row directly via SQL first (mirrors vault/tests/test_contract_smoke.py's own "
                "create_campaign() bootstrap branch)."
            )
        created = self._post(
            "/campaigns",
            {"name": run_name, **_taxonomy(campaign=str(anchor), function_id=function_id)},
        )
        campaign_id = str(created["id"])
        _CAMPAIGN_ID_CACHE[run_name] = campaign_id
        return campaign_id

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

    def list_signals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Most-recent-first page of signals rows (GET /signals, already in
        the frozen vault-api contract -- limit/offset only, no server-side
        filter, so callers narrow client-side).

        Added for ingest-signals' cross-run memory (F-INGEST-NO-MEMORY):
        without it every scan started cold, so a story inside the horizon
        could be re-reported on every run until it aged out."""
        return self._list("/signals", limit=limit)

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
