"""AC-026: every task envelope validates against
contracts/service-bus/task-envelope.schema.json before it would be
published; a deliberately malformed envelope (missing a required field)
is rejected rather than silently sent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from orchestrator.servicebus.local_double import InMemoryServiceBus
from orchestrator.servicebus.producer import EnvelopeValidationError, publish


def _valid_envelope() -> dict:
    return {
        "envelope_version": "1",
        "task_id": str(uuid.uuid4()),
        "task_type": "draft-brief",
        "agent_run_id": str(uuid.uuid4()),
        "campaign_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "retry_count": 0,
    }


def test_valid_envelope_accepted():
    bus = InMemoryServiceBus()
    envelope = _valid_envelope()
    publish("task", envelope, bus)
    assert bus.queue_depth("task") == 1


def test_malformed_envelope_rejected():
    bus = InMemoryServiceBus()
    envelope = _valid_envelope()
    del envelope["task_id"]  # remove a required field

    with pytest.raises(EnvelopeValidationError):
        publish("task", envelope, bus)

    assert bus.queue_depth("task") == 0
