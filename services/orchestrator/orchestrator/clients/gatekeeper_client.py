"""Gatekeeper client for the orchestrator's request-approval dispatch
handler (AC-01, AC-15, AC-19).

Two calls:
  * `gate_check()` — POST /gate-check on ca-gatekeeper (internal-ingress),
    the existing production endpoint (services/gatekeeper/app/routers/
    gate_check.py). This is the ONLY thing request-approval's handler does
    — it issues the gate-token request / surfaces the approval card and
    returns immediately once /gate-check responds. It never polls or
    waits for the human decision (AC-01's bounded scope for
    request-approval).
  * `get_approval_status()` — GET /approval-status, a NEW read-only
    endpoint this session adds to ca-gatekeeper (plan step 4,
    services/gatekeeper/app/routers/approval_status.py) so an agent can
    later ask "what did the human decide" without a browser/Entra session
    (AC-15). Distinct from gate_check's own always-COMPLETED-once-issued
    task state.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx

from orchestrator.clients.azure_fqdn import resolve_live_fqdn
from orchestrator.telemetry_wiring import inject_traceparent

AZURE_CONTAINER_APP = "ca-gatekeeper"


class GatekeeperClientError(RuntimeError):
    """Gatekeeper could not be reached or returned an unexpected shape."""


@lru_cache(maxsize=1)
def resolve_gatekeeper_base_url() -> str | None:
    """CMOS_GATEKEEPER_BASE_URL env override wins; otherwise resolve
    ca-gatekeeper's real live FQDN (AC-19, L-0025).

    PERF-04 defense-in-depth: memoized for the lifetime of the process —
    see vault_client_ext.resolve_vault_base_url's identical note. Call
    resolve_gatekeeper_base_url.cache_clear() to force re-resolution (tests
    only)."""
    override = os.environ.get("CMOS_GATEKEEPER_BASE_URL")
    if override:
        return override
    return resolve_live_fqdn(AZURE_CONTAINER_APP)


class GatekeeperClient:
    def __init__(self, *, base_url: str, timeout: float = 15.0) -> None:
        if not base_url:
            raise GatekeeperClientError(
                "GatekeeperClient requires a resolved base_url — never a guessed hostname "
                "(L-0025); call resolve_gatekeeper_base_url() first"
            )
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GatekeeperClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def gate_check(
        self,
        *,
        agent_run_id: str,
        function_id: str,
        action_class: str,
        content_hash: str | None = None,
        preview_title: str | None = None,
        preview_reference: str | None = None,
        evidence_summary: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "agent_run_id": agent_run_id,
            "function_id": function_id,
            "action_class": action_class,
            "content_hash": content_hash,
            "preview_title": preview_title,
            "preview_reference": preview_reference,
            "evidence_summary": evidence_summary,
            "subject": subject,
        }
        response = self._client.post("/gate-check", json=body, headers=inject_traceparent())
        if response.status_code != 200:
            raise GatekeeperClientError(
                f"POST /gate-check returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def get_approval_status(
        self,
        *,
        agent_run_id: str,
        function_id: str,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"agent_run_id": agent_run_id, "function_id": function_id}
        if content_hash is not None:
            params["content_hash"] = content_hash
        response = self._client.get(
            "/approval-status", params=params, headers=inject_traceparent()
        )
        if response.status_code != 200:
            raise GatekeeperClientError(
                f"GET /approval-status returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response.json()

    def sign_option_card_link(self, card_id: str) -> str:
        """Appendix D PR 5: route_digest_handler (Fn 117) needs a real
        signature for every pending card's /decide links (services/
        options_inbox/teams_render.render_digest's `signer` param), but
        the RS256 signing key only ca-gatekeeper's own identity can reach
        (Key Vault) — see app/routers/option_link_signing.py's header."""
        response = self._client.post(
            "/sign-option-card-link", json={"card_id": card_id}, headers=inject_traceparent()
        )
        if response.status_code != 200:
            raise GatekeeperClientError(
                f"POST /sign-option-card-link returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response.json()["sig"]
