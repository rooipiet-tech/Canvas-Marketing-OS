"""Pydantic models for the orchestrator service.

These models mirror the frozen/new JSON Schema contracts byte-for-byte on
required fields:
  - TaskEnvelope        <-> contracts/service-bus/task-envelope.schema.json (frozen v1)
  - LoopDefinition/LoopTask <-> contracts/orchestrator/loop-definition.schema.json
  - HeartbeatEvent       <-> contracts/orchestrator/heartbeat-event.schema.json
  - DeadLetterAlert      <-> contracts/orchestrator/dead-letter-alert.schema.json

TransitionReason is a closed enum (F6): every task_transitions row's
`reason` column must be one of these 8 values, enforced structurally in
Python (this enum, used as db.transition()'s type signature) AND at the
database level (migrations/0001_orchestrator_init.sql's CHECK constraint)
— defense in depth, not a naming convention alone.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TaskStateEnum(str, Enum):
    PENDING = "pending"
    DISPATCHABLE = "dispatchable"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETED = "completed"
    RETRY_PENDING = "retry_pending"
    DEAD_LETTERED = "dead_lettered"
    FAILED = "failed"


class TransitionReason(str, Enum):
    CREATED = "created"
    DEPENDENCY_SATISFIED = "dependency_satisfied"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED_ATTEMPT_1 = "failed_attempt_1"
    FAILED_ATTEMPT_2 = "failed_attempt_2"
    DEAD_LETTERED = "dead_lettered"
    VAULT_WRITE_FAILED = "vault_write_failed"
    # migrations/0003_qa_blocked_reason.sql: a NORMAL business outcome (a
    # deliberately-seeded missing-UTM violation, an uncleared client
    # reference) dispatch.py's qa-review handler records, distinct from
    # the retry-state-machine reasons above (AC-05, AC-06).
    QA_BLOCKED = "qa_blocked"


class TaskEnvelope(BaseModel):
    """Mirrors contracts/service-bus/task-envelope.schema.json (frozen v1)."""

    model_config = ConfigDict(extra="forbid")

    envelope_version: str = Field(default="1", pattern="^1$")
    task_id: UUID
    task_type: str
    agent_run_id: UUID
    campaign_id: UUID
    priority: Priority = Priority.NORMAL
    created_at: datetime
    retry_count: int = Field(default=0, ge=0)
    metadata: dict[str, str] | None = None

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize to the exact JSON shape the frozen schema expects.

        additionalProperties is false on the frozen schema, so absent
        optional fields (metadata) must not appear at all, not as null.
        """
        data = self.model_dump(mode="json", exclude_none=True)
        return data


class LoopTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_type: str
    depends_on: list[str] = Field(default_factory=list)
    params: dict[str, Any] | None = None
    description: str | None = None


class LoopDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern="^1$")
    loop_id: str
    description: str | None = None
    tasks: list[LoopTask]


class HeartbeatEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope_version: str = Field(default="1", pattern="^1$")
    event_type: str = Field(default="heartbeat", pattern="^heartbeat$")
    event_id: UUID
    loop_id: str
    fired_at: datetime
    source: str


class DeadLetterAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_version: str = Field(default="1", pattern="^1$")
    task_id: UUID
    task_type: str
    loop_id: str
    failure_count: int = Field(ge=3, le=3)
    dead_lettered_at: datetime

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
