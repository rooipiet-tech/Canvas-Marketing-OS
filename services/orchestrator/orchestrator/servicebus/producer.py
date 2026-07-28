"""Envelope-validating publish path.

publish() validates the outgoing envelope against the relevant contract
schema BEFORE calling client.send() — a deliberately malformed envelope
(missing a required field) raises EnvelopeValidationError and is never
sent (AC-026).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

_CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "contracts"

_TASK_ENVELOPE_SCHEMA_PATH = _CONTRACTS_DIR / "service-bus" / "task-envelope.schema.json"
_HEARTBEAT_SCHEMA_PATH = _CONTRACTS_DIR / "orchestrator" / "heartbeat-event.schema.json"
_DEAD_LETTER_SCHEMA_PATH = _CONTRACTS_DIR / "orchestrator" / "dead-letter-alert.schema.json"


class EnvelopeValidationError(Exception):
    """Raised when an outgoing envelope fails schema validation. The
    envelope is never sent when this is raised.
    """


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


_TASK_ENVELOPE_SCHEMA = None
_HEARTBEAT_SCHEMA = None
_DEAD_LETTER_SCHEMA = None


def _task_envelope_schema() -> dict[str, Any]:
    global _TASK_ENVELOPE_SCHEMA
    if _TASK_ENVELOPE_SCHEMA is None:
        _TASK_ENVELOPE_SCHEMA = _load(_TASK_ENVELOPE_SCHEMA_PATH)
    return _TASK_ENVELOPE_SCHEMA


def _heartbeat_schema() -> dict[str, Any]:
    global _HEARTBEAT_SCHEMA
    if _HEARTBEAT_SCHEMA is None:
        _HEARTBEAT_SCHEMA = _load(_HEARTBEAT_SCHEMA_PATH)
    return _HEARTBEAT_SCHEMA


def _dead_letter_schema() -> dict[str, Any]:
    global _DEAD_LETTER_SCHEMA
    if _DEAD_LETTER_SCHEMA is None:
        _DEAD_LETTER_SCHEMA = _load(_DEAD_LETTER_SCHEMA_PATH)
    return _DEAD_LETTER_SCHEMA


def _schema_for(queue: Literal["task", "event"], envelope: dict[str, Any]) -> dict[str, Any]:
    if queue == "task":
        return _task_envelope_schema()
    if queue == "event":
        if "alert_version" in envelope:
            return _dead_letter_schema()
        return _heartbeat_schema()
    raise ValueError(f"unknown queue {queue!r}")


def validate_envelope(queue: Literal["task", "event"], envelope: dict[str, Any]) -> None:
    schema = _schema_for(queue, envelope)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(envelope), key=lambda e: e.path)
    if errors:
        messages = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise EnvelopeValidationError(
            f"envelope failed schema validation for queue {queue!r}: {messages}"
        )


def publish(queue: Literal["task", "event"], envelope: dict[str, Any], client: Any) -> None:
    """Validate envelope against the relevant contract schema, then send it
    via client.send(queue, envelope). Raises EnvelopeValidationError (and
    never calls client.send) on any schema violation.
    """
    validate_envelope(queue, envelope)
    client.send(queue, envelope)
