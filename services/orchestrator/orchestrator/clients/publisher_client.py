"""Publisher client for the orchestrator's publish step (process 7).

The orchestrator has never had one. ca-publisher exists, is deployed, and
exposes POST /publish with a real Buffer path, a gate-token verifier and
a JTI ledger -- and nothing in this repository ever called it. Both loops
terminated at the approval request: `request-approval`,
`schedule-social-buffer` and `publish-newsletter` each raise an approval
card and complete, and no task in either graph depends on them. The
pipeline's last act was to ask a human, and nothing consumed the answer.

Same conventions as gatekeeper_client.py, deliberately: resolved base URL
only (never a guessed hostname, L-0025), memoized FQDN resolution
(PERF-04), traceparent injected on every call.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx

from orchestrator.clients.azure_fqdn import resolve_live_fqdn
from orchestrator.telemetry_wiring import inject_traceparent

AZURE_CONTAINER_APP = "ca-publisher"


class PublisherClientError(RuntimeError):
    """Publisher could not be reached or returned an unexpected shape."""


@lru_cache(maxsize=1)
def resolve_publisher_base_url() -> str | None:
    """CMOS_PUBLISHER_BASE_URL env override wins; otherwise resolve
    ca-publisher's real live FQDN (AC-19, L-0025). Call
    resolve_publisher_base_url.cache_clear() to force re-resolution
    (tests only)."""
    override = os.environ.get("CMOS_PUBLISHER_BASE_URL")
    if override:
        return override
    return resolve_live_fqdn(AZURE_CONTAINER_APP)


class PublisherClient:
    def __init__(self, *, base_url: str, timeout: float = 30.0) -> None:
        if not base_url:
            raise PublisherClientError(
                "PublisherClient requires a resolved base_url — never a guessed hostname "
                "(L-0025); call resolve_publisher_base_url() first"
            )
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PublisherClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def publish(
        self,
        *,
        agent_run_id: str,
        function_id: str,
        asset_bytes_b64: str,
        gate_token: str,
        asset_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /publish.

        `asset_bytes_b64` is the EXACT bytes to publish. Publisher
        recomputes their hash itself and compares it with the hash bound
        into the gate token -- no caller-supplied hash is trusted, and
        this client deliberately offers no way to send one.

        A 403 is a real, meaningful answer (the token did not verify, the
        hash did not match, the kill switch is active, the queue is at
        cap), not a transport failure, so its body is preserved in the
        error rather than collapsed into a status code.
        """
        body = {
            "agent_run_id": agent_run_id,
            "function_id": function_id,
            "asset_bytes_b64": asset_bytes_b64,
            "gate_token": gate_token,
            "asset_id": asset_id,
        }
        response = self._client.post("/publish", json=body, headers=inject_traceparent())
        if response.status_code != 200:
            raise PublisherClientError(
                f"POST /publish returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()
