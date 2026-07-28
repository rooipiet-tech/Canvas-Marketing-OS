"""Stub Vault-recording adapter (AC-11).

Publisher is the only service ever permitted to hold external write
credentials, and this session ships the verification/refusal path ONLY —
no external channel is actually written to. What "publishing" means here
is: record the publication in the Vault (Postgres) through this adapter.

The adapter doubles as a spy so a test can assert it was called EXACTLY
once for a fully valid token, and never on any refusal branch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecordedPublish:
    agent_run_id: str
    function_id: str
    content_hash: str
    gate_decision_id: str
    jti: str
    record_id: str


@dataclass
class StubVaultRecordingAdapter:
    """In-memory stand-in for the real Vault write path."""

    calls: list[RecordedPublish] = field(default_factory=list)

    def record_publish(
        self,
        *,
        agent_run_id: str,
        function_id: str,
        content_hash: str,
        gate_decision_id: str,
        jti: str,
    ) -> RecordedPublish:
        record = RecordedPublish(
            agent_run_id=str(agent_run_id),
            function_id=function_id,
            content_hash=content_hash,
            gate_decision_id=str(gate_decision_id),
            jti=jti,
            record_id=str(uuid.uuid4()),
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


_ADAPTER = StubVaultRecordingAdapter()


def get_vault_adapter() -> StubVaultRecordingAdapter:
    return _ADAPTER
