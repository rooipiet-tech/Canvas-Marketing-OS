"""Vault-recording adapter (AC-11; TD-02).

Publisher is the only service ever permitted to hold external write
credentials, and "publishing" here means: record the publication in the
Vault (Postgres, via Vault's own HTTP API) through this adapter. Vault is
the system of record — an audit that starts there must be able to find
every publication.

TD-02 (docs/architecture/09-technical-debt.md): the pre-fix adapter only
appended to an in-memory Python list, so `governance.publish_attempts`
recorded the attempt but the Vault never learned anything was published.
The fix is a real write: for every accepted publish, this adapter appends
a NEW `gate_decisions` row (gate_decisions is append-only by design, see
contracts/vault-schema/schema.sql — this never mutates the original
decision that authorized the token, it adds one recording that the
authorized content was actually shipped), scoped to the same
vertical/campaign/evidence_grade/consent_status/retention_class as the
gate_decision the publish's own token was bound to (fetched via GET
/gate-decisions/{gate_decision_id} — the same taxonomy-join every Vault
object type carries, see services/vault/vault/routers/objects.py).

The adapter doubles as a spy so a test can assert it was called EXACTLY
once for a fully valid token, and never on any refusal branch — unchanged
by this fix; `record_publish`'s signature and the pre-existing
tests/test_publish_exactly_once.py both stay intact. `http_client` is
injectable (tests pass an httpx.Client wired to httpx.MockTransport,
exactly as app/vault_lookup.py's own tests do) so this never needs a live
Vault to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.vault_lookup import vault_auth_headers, vault_base_url

# The taxonomy fields every Vault object type requires (contracts/
# vault-api.yaml's TaxonomyFields) — carried forward from the SOURCE
# gate_decision (the one the publish's token was bound to) onto the NEW
# gate_decisions row this adapter appends, so the publish-confirmation row
# lives in the same governance scope as the decision that authorized it.
_TAXONOMY_FIELDS = (
    "vertical",
    "campaign",
    "evidence_grade",
    "consent_status",
    "retention_class",
)


class VaultWriteError(RuntimeError):
    """The Vault write that should have recorded this publish failed.

    Raised on ANY failure — unreachable Vault, non-2xx, or a response
    missing a required field — never silently proceeds as though the
    write had succeeded (mirrors app/vault_lookup.py's fail-closed
    contract for the same reason: a caller must never see `outcome:
    published` when the Vault does not actually carry that record).
    """


@dataclass
class RecordedPublish:
    agent_run_id: str
    function_id: str
    content_hash: str
    gate_decision_id: str
    jti: str
    record_id: str


def write_gate_decision(
    *,
    agent_run_id: str,
    function_id: str,
    reason: str,
    source_gate_decision_id: str,
    base_url: str | None = None,
    timeout: float = 10.0,
    http_client: httpx.Client | None = None,
) -> str:
    """Append one new `gate_decisions` row recording that `agent_run_id`'s
    content was published, and return its id.

    1. GETs /gate-decisions/{source_gate_decision_id} — the decision the
       publish's own gate token was bound to — for its taxonomy fields.
    2. POSTs /gate-decisions with those taxonomy fields, `outcome:
       approved`, `decided_by: service:publisher`, and the given `reason`.

    Raises VaultWriteError on any failure. `http_client` is injectable
    (tests pass an httpx.Client wired to httpx.MockTransport) so this
    never needs a live Vault to unit-test.
    """
    resolved = base_url or vault_base_url()
    if not resolved and http_client is None:
        raise VaultWriteError(
            "VAULT_API_URL is not configured — cannot record this publish in "
            "the Vault, failing closed"
        )

    owns_client = http_client is None
    client = http_client or httpx.Client(
        base_url=(resolved or "").rstrip("/"), timeout=timeout, headers=vault_auth_headers()
    )
    try:
        try:
            source_response = client.get(f"/gate-decisions/{source_gate_decision_id}")
            if source_response.status_code != 200:
                raise VaultWriteError(
                    f"GET /gate-decisions/{source_gate_decision_id} returned HTTP "
                    f"{source_response.status_code}"
                )
            source = source_response.json()
            taxonomy = {name: source.get(name) for name in _TAXONOMY_FIELDS}
            missing = [name for name, value in taxonomy.items() if not value]
            if missing:
                raise VaultWriteError(
                    f"gate_decision {source_gate_decision_id} is missing taxonomy "
                    f"field(s): {missing}"
                )

            create_response = client.post(
                "/gate-decisions",
                json={
                    "agent_run_id": agent_run_id,
                    "decided_by": "service:publisher",
                    "outcome": "approved",
                    "reason": reason,
                    "function_id": function_id,
                    **taxonomy,
                },
            )
            if create_response.status_code not in (200, 201):
                raise VaultWriteError(
                    f"POST /gate-decisions returned HTTP {create_response.status_code}: "
                    f"{create_response.text[:500]}"
                )
            created = create_response.json()
            record_id = created.get("id")
            if not record_id:
                raise VaultWriteError("POST /gate-decisions response is missing id")
        except httpx.HTTPError as exc:
            raise VaultWriteError(
                f"Vault write for agent_run_id={agent_run_id} failed: {exc}"
            ) from exc
    finally:
        if owns_client:
            client.close()
    return str(record_id)


@dataclass
class VaultRecordingAdapter:
    """Vault-backed publish-recording adapter — see module docstring."""

    calls: list[RecordedPublish] = field(default_factory=list)
    # Injectable for tests (httpx.Client wired to httpx.MockTransport);
    # None in production, where write_gate_decision resolves a real
    # httpx.Client from VAULT_API_URL on every call.
    http_client: httpx.Client | None = None

    def record_publish(
        self,
        *,
        agent_run_id: str,
        function_id: str,
        content_hash: str,
        gate_decision_id: str,
        jti: str,
    ) -> RecordedPublish:
        vault_record_id = write_gate_decision(
            agent_run_id=agent_run_id,
            function_id=function_id,
            reason=f"published (content_hash={content_hash}, jti={jti})",
            source_gate_decision_id=gate_decision_id,
            http_client=self.http_client,
        )
        record = RecordedPublish(
            agent_run_id=str(agent_run_id),
            function_id=function_id,
            content_hash=content_hash,
            gate_decision_id=str(gate_decision_id),
            jti=jti,
            record_id=vault_record_id,
        )
        self.calls.append(record)
        return record

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        self.calls.clear()

    def as_dict(self) -> dict[str, Any]:
        return {"call_count": self.call_count}


_ADAPTER = VaultRecordingAdapter()


def get_vault_adapter() -> VaultRecordingAdapter:
    return _ADAPTER
