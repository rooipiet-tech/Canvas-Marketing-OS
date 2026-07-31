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

from orchestrator import config

# INCIDENT (2026-07-31, same class as loop_loader.py's _SCHEMA_PATH — see
# that file's header): this used to be a module-level constant computed via
# five chained .parent hops, correct only in a full repository checkout.
# The orchestrator image is built from services/orchestrator alone, so
# there is no repository around the code inside the container and that
# walk lands on the filesystem root, not a repo root — confirmed live
# (ca-vault... no, ca-orchestrator's smoke test crashing with
# FileNotFoundError once the loop_loader.py sibling instance of this same
# bug was fixed and the run got one step further). Routed through
# config.contracts_dir() now, honouring CONTRACTS_DIR (set by the
# Dockerfile inside the image) with the same checkout-relative fallback
# for local dev/pytest.


def _task_envelope_schema_path() -> Path:
    return config.contracts_dir() / "service-bus" / "task-envelope.schema.json"


def _heartbeat_schema_path() -> Path:
    return config.contracts_dir() / "orchestrator" / "heartbeat-event.schema.json"


def _dead_letter_schema_path() -> Path:
    return config.contracts_dir() / "orchestrator" / "dead-letter-alert.schema.json"


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
        _TASK_ENVELOPE_SCHEMA = _load(_task_envelope_schema_path())
    return _TASK_ENVELOPE_SCHEMA


def _heartbeat_schema() -> dict[str, Any]:
    global _HEARTBEAT_SCHEMA
    if _HEARTBEAT_SCHEMA is None:
        _HEARTBEAT_SCHEMA = _load(_heartbeat_schema_path())
    return _HEARTBEAT_SCHEMA


def _dead_letter_schema() -> dict[str, Any]:
    global _DEAD_LETTER_SCHEMA
    if _DEAD_LETTER_SCHEMA is None:
        _DEAD_LETTER_SCHEMA = _load(_dead_letter_schema_path())
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
