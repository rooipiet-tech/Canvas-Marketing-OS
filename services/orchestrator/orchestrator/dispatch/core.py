from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from governance_lib.constants import AGENT_NAME_LOOP_PROOF
from jsonschema import Draft202012Validator

from orchestrator import manifest as registry_manifest
from orchestrator.config import functions_dir
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import get_logger, log_event, structural_skeleton
from orchestrator.models import TaskEnvelope

logger = get_logger("dispatch")

FUNCTION_ID_09 = "09-market-intelligence-director"
FUNCTION_ID_42 = "42-linkedin-post-writer"
FUNCTION_ID_02 = "02-brand-steward-qa"
# Deterministic rendering only (plan step 9) -- no LLM call, so this isn't
# one of the numbered function packages under functions/.
FUNCTION_ID_BRIEF_COMPOSE = "brief.compose"

# S11 real handlers (6 Aug 2026) -- function IDs for weekly-content-loop's
# 8 drafting functions, all already-existing numbered prompt packages.
FUNCTION_ID_26 = "26-client-advocacy-harvester"
FUNCTION_ID_39 = "39-insight-to-story-editor"
FUNCTION_ID_41 = "41-research-brief-writer"
FUNCTION_ID_43 = "43-executive-ghostwriter"
FUNCTION_ID_45 = "45-carousel-post-writer"
FUNCTION_ID_46 = "46-newsletter-writer"
FUNCTION_ID_47 = "47-case-study-writer"
FUNCTION_ID_52 = "52-content-repurposer"
# New prompt package this change adds -- see module docstring's "first
# draft, not an approved QA policy" note.
FUNCTION_ID_48_FACT_CHECK = "48-fact-check-verdict"

# Appendix D PR 5 (options-approval-loop.yaml). Fn 124 (legal_triage) is
# deliberately absent here -- its package is still status: scaffold (no
# schema.json/tools.yaml) and its own completion-plan row (App D PR
# 10-13) comes after this one; see compose_options_handler's docstring.
FUNCTION_ID_116 = "116-options-composer"
FUNCTION_ID_117 = "117-approval-inbox-router"

# AGENT_NAME_LOOP_PROOF is imported from governance_lib.constants above
# (TD-08) -- services/publisher/app/config.py imports the same constant,
# so the two can no longer drift the way PV2-03's residual-risk
# mitigation originally guarded against with a cross-service equality
# test (services/publisher/tests/test_agent_name_constant_matches_
# orchestrator.py, still present, now asserting the import rather than
# comparing two independent literals).

# AC-30's queryable isolation tag: threaded into every proof-circuit
# gate-check's preview_reference/preview_title and into the Vault
# agent_run.input of every proof-circuit model call.
PROOF_CIRCUIT_TAG = "loop-proof"

MAX_LINEAGE_HOPS = 6


def _read_prompt(function_dir_name: str) -> str:
    """TD-09: resolves THROUGH the verified registry manifest rather than
    reading functions_dir() directly -- see orchestrator/manifest.py's
    resolve_prompt() for what "verified" means and why a mismatch or an
    unregistered function_id raises instead of returning stale/tampered
    content."""
    return registry_manifest.resolve_prompt(function_dir_name)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _campaign_name(envelope: TaskEnvelope) -> str:
    return f"run-{envelope.campaign_id}"

def is_proof_circuit(envelope: TaskEnvelope) -> bool:
    return bool(envelope.metadata and envelope.metadata.get("proof_circuit") == "true")

def _agent_name(base_name: str, envelope: TaskEnvelope) -> str:
    """AC-30 (and step 10/11's own wording): proof-circuit invocations tag
    their Vault agent_run with AGENT_NAME_LOOP_PROOF; every other
    invocation keeps its ordinary descriptive agent_name."""
    return AGENT_NAME_LOOP_PROOF if is_proof_circuit(envelope) else base_name

def _parse_json_content(content: str) -> dict[str, Any]:
    """CompletionResponse.content is a plain string (contract) that the
    prompt asks the model to make a single bare JSON object.

    F-JSON-TRAILING-CONTENT (10 Aug 2026, round 30). The previous
    implementation stripped a leading code fence with `text.strip("`")`
    and then called a strict `json.loads`, which fails with "Extra data"
    the moment the model emits ANYTHING after the JSON object -- a
    closing ``` fence, or a sentence of explanation. `strip("`")` only
    removes backticks at the two ends of the whole string, so a response
    shaped

        ```json
        {...}
        ```

    left the closing fence sitting on its own line after the object and
    died with `Extra data: line 6 column 1`. That is the exact error that
    dead-lettered `qa-review-brand-steward` on the 10 Aug 05:00 UTC run,
    three retries in a row, at char offsets 589 / 892 / 1645 -- the
    growing offsets being the model's own variation in how much it added,
    not a truncation. Distinct from round 28's `Expecting ',' delimiter`
    bug (a genuine `max_tokens` truncation, fixed in PR #91): this one is
    the model producing MORE than asked, not less.

    The fix is to parse the first complete JSON value and tolerate
    trailing content rather than reject the whole response:
      - if the text is fenced, take what is between the fences;
      - if it does not start with a JSON opener, skip forward to the
        first `{` (covers "Here is the verdict:" preambles);
      - use `raw_decode`, which stops at the end of the first value;
      - log anything left over so it stays visible rather than silent.

    Deliberately NOT a prompt change: every function's prompt.md already
    says "and nothing else", and CI's `prompt-missing-json-output-
    contract` rule enforces it. The contract is right; the parser was
    brittle about a model that is occasionally chatty anyway.
    """
    text = content.strip()

    if text.startswith("```"):
        newline = text.find("\n")
        text = text[newline + 1 :] if newline != -1 else text[3:]
        closing = text.find("```")
        if closing != -1:
            text = text[:closing]
        text = text.strip()

    if not text.startswith(("{", "[")):
        opener = text.find("{")
        if opener > 0:
            text = text[opener:]

    try:
        parsed, end_index = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        # F-JSON-PARSE-VISIBILITY (10 Aug 2026): the raw response is never
        # otherwise persisted -- the handler's own agent_run row stays at
        # status="running" (update_agent_run is only called on the success
        # path) and model-gateway's own completion log only carries metadata,
        # never body text. Without this, a parse failure is permanently
        # unrecoverable for root-causing after the fact.
        #
        # The preview is a structural skeleton, not the text. The original
        # justification for logging raw output was that "content_class is
        # already public_source_content for every caller of this function
        # (marketing drafts, no client names)". Both halves of that were
        # wrong, which is why this now masks instead:
        #
        #   1. Not every caller. Of the 13 call sites, propose_sources_handler,
        #      draft_content_handler and draft_client_advocacy_harvest_handler
        #      set no content_class at all -- and the last is function 26,
        #      whose whole subject is client naming and consent.
        #   2. content_class says nothing about the response anyway.
        #      services/model-gateway/redaction.py defines exactly one
        #      scanner, scan_request; there is no response-side scan. The
        #      content class narrows which patterns apply to the OUTBOUND
        #      request. A model's reply is never scanned, in either
        #      direction, whatever the class.
        #
        # So a parse failure on function 26 could put a named client contact
        # and a testimonial quote into log-cmos-dev verbatim. The skeleton
        # keeps every delimiter, fence and truncation point that diagnosing
        # the failure needs, and can carry no name, address, number or
        # identifier by construction. The sha256 lets repeat failures of the
        # same response be correlated without storing it.
        log_event(
            logger,
            logging.WARNING,
            "model_response_json_parse_failed",
            error=str(exc),
            response_skeleton=structural_skeleton(text, limit=1000),
            response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            response_length=len(text),
        )
        raise DispatchError(f"model response was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise DispatchError(
            f"model response was valid JSON but not an object (got {type(parsed).__name__})"
        )

    trailing = text[end_index:].strip()
    if trailing:
        # Skeletonised for the same reason as the parse-failure branch above,
        # and it matters more here: this branch fires on a SUCCESSFUL parse
        # whenever the model is chatty after the object, which the docstring
        # says is the common case -- so it runs more often than the failure
        # path. `trailing` is text[end_index:], the same string. The
        # justification this line used to rest on was the function-scoped
        # "no redaction/PII concern" comment that this change deletes as
        # false; nothing replaced it until now. What the field is for --
        # was there trailing junk, and roughly what shape -- survives
        # masking intact.
        log_event(
            logger,
            logging.WARNING,
            "model_response_trailing_content_discarded",
            trailing_chars=len(trailing),
            trailing_preview=structural_skeleton(trailing, limit=120),
        )

    return parsed

def _load_function_input_schema(function_id: str) -> dict[str, Any]:
    """The `input` subschema of a function package's own schema.json."""
    path = functions_dir() / function_id / "schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    input_schema = schema.get("properties", {}).get("input")
    if not isinstance(input_schema, dict):
        raise DispatchError(
            f"{function_id}: schema.json carries no properties.input subschema to validate against"
        )
    return input_schema


def _validate_function_input(function_id: str, payload: Any) -> None:
    """Validate what a handler is about to SEND against the function's own
    input contract (F-INPUT-UNVALIDATED).

    The output side got this in the F-A commit; the input side had the
    identical hole, and it hid a worse bug. Every one of the eight weekly
    drafting handlers was sending a payload its own schema.json would
    reject -- most consequentially function 41, the Research Brief Writer,
    which received `{"pillar": ...}` alone while its schema requires
    `signal_summary`, the field whose own description reads "the raw
    signal or opportunity-card text this brief is built from... a brief
    must never invent evidence the signal does not supply". A function
    asked for citations, handed no sources, and nothing anywhere noticed.

    Validating the input is what stops a handler and its package drifting
    apart again silently: the schema stops being documentation and starts
    being the wire format.
    """
    validator = Draft202012Validator(_load_function_input_schema(function_id))
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.absolute_path) or "<root>"
    raise DispatchError(
        f"{function_id}: handler input failed schema.json validation at {location} "
        f"({len(errors)} violation(s)): {first.message[:200]}"
    )


def _load_function_output_schema(function_id: str) -> dict[str, Any]:
    """The `output` subschema of a function package's own schema.json.

    Resolved through functions_dir() at call time, never cached at module
    import -- same reason _load_scan_profiles() and _read_prompt() do
    (see config.functions_dir()'s docstring)."""
    path = functions_dir() / function_id / "schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    output_schema = schema.get("properties", {}).get("output")
    if not isinstance(output_schema, dict):
        raise DispatchError(
            f"{function_id}: schema.json carries no properties.output subschema to validate against"
        )
    return output_schema


def _validate_function_output(function_id: str, output: Any) -> None:
    """Validate a parsed model output against its own package's schema.

    The violation message is truncated to telemetry_lib's MAX_TEXT_LEN
    (200) before it reaches the exception text: jsonschema echoes the
    offending value, and that value is model output derived from fetched
    news bodies -- exactly the content class the rest of this pipeline is
    careful not to spill into logs wholesale.
    """
    validator = Draft202012Validator(_load_function_output_schema(function_id))
    errors = sorted(validator.iter_errors(output), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.absolute_path) or "<root>"
    raise DispatchError(
        f"{function_id}: model output failed schema.json validation at {location} "
        f"({len(errors)} violation(s)): {first.message[:200]}"
    )


# The five pillars, exactly as positioning.md section 5 and every
# function's own prompt.md name them -- kept as one literal list so a
# rotation (plan-content-monday) and any future validation share one
# source of truth rather than five independently-typed copies.
CONTENT_PILLARS = [
    "Finance-grade trust",
    "Consolidation at scale",
    "Fabric-native",
    "Productised speed",
    "Beyond the dashboard",
]
