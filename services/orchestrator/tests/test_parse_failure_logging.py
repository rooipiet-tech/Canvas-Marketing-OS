"""A model response that fails to parse must not reach the log verbatim.

Audit finding 01-security-and-data #3 (issue #135): `_parse_json_content`
logged `response_preview=text[:4000]` on a JSON parse failure, justified by a
comment claiming every caller sets `content_class="public_source_content"` and
that this made the log free of client names. Both halves were false — three of
the thirteen call sites set no content class at all (one of them function 26,
the client-advocacy harvester), and `content_class` governs the OUTBOUND
request scan only; `services/model-gateway/redaction.py` has no response-side
scanner, so a model reply is never scanned in either direction.

These tests pin the fix: the log carries a structural skeleton, never the text.
"""

from __future__ import annotations

import json
import logging

import pytest
from orchestrator.dispatch import DispatchError, _parse_json_content
from orchestrator.logging_config import structural_skeleton

# Drawn from contracts/model-gateway/redaction-rules.yaml's own fixtures and
# pattern descriptions, so this test moves if the contract's idea of personal
# information does.
CLIENT_NAME = "Thabo Nkosi"
EMAIL = "thabo.nkosi@example.co.za"
SA_PHONE = "+27 82 123 4567"
SA_ID = "8001015009087"

# A plausible function-26 failure: the model answers in prose instead of JSON.
PROSE_RESPONSE = (
    f"Certainly! Here is the advocacy summary you asked for. {CLIENT_NAME} at "
    f"Imperial Logistics said the rollout exceeded expectations. Reach them on "
    f"{EMAIL} or {SA_PHONE}. Their reference number is {SA_ID}."
)


def test_skeleton_masks_every_identifier_class():
    out = structural_skeleton(PROSE_RESPONSE)
    for secret in (CLIENT_NAME, EMAIL, SA_PHONE, SA_ID, "Imperial Logistics"):
        assert secret not in out, f"{secret!r} survived the skeleton"
    # No letter or digit from the original survives as itself.
    assert not any(c.isdigit() for c in out.replace("0", ""))


def test_skeleton_preserves_the_structure_that_makes_it_useful():
    out = structural_skeleton('Sure!\n```json\n{"a": [1, 2]}\n```')
    # Delimiters, fences, punctuation and newlines all survive — this is what
    # a parse failure is diagnosed from.
    assert out == 'Aaaa!\n```aaaa\n{"a": [0, 0]}\n```'


def test_parse_failure_logs_a_skeleton_and_never_the_response(caplog):
    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        with pytest.raises(DispatchError):
            _parse_json_content(PROSE_RESPONSE)

    records = [r for r in caplog.records if r.getMessage() == "model_response_json_parse_failed"]
    assert records, "the parse failure should still be logged at WARNING"
    record = records[0]
    fields = getattr(record, "extra_fields", {})

    assert "response_preview" not in fields, "the raw preview field must be gone"
    assert fields["response_length"] == len(PROSE_RESPONSE)
    assert fields["response_skeleton"] == structural_skeleton(PROSE_RESPONSE, limit=1000)
    assert len(fields["response_sha256"]) == 64

    # The whole emitted record, serialised as it would reach stdout.
    emitted = json.dumps({"event": record.getMessage(), **fields})
    for secret in (CLIENT_NAME, EMAIL, SA_PHONE, SA_ID, "Imperial Logistics"):
        assert secret not in emitted, f"{secret!r} reached the log record"


def test_a_successful_parse_still_returns_the_parsed_object():
    # The fix must not change the success path.
    assert _parse_json_content('{"ok": true}') == {"ok": True}
