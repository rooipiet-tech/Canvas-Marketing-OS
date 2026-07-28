"""Redaction firewall — runs before any provider adapter call.

SCAN SCOPE, stated explicitly so it is never left implicit:

  (a) every messages[*].content, for every role (system, user, assistant,
      tool); and
  (b) the serialized tools[] payload — the frozen contract's free-form
      `additionalProperties: true` passthrough.

(b) matters as much as (a): client-identifying data smuggled into a tool
definition (a description, an enum value, a default) would bypass a
messages-only scanner entirely, and tools[] is the one part of the request
the contract deliberately does not constrain.

Neither branch may ever *skip* a value it does not recognise. The frozen
contract types messages[].content as a string and completion.py rejects
anything else at the boundary, but the firewall must not depend on that:
a non-string content (e.g. an Anthropic-style content-block array) is
serialized with json.dumps and scanned as text, exactly like tools[]. A
scanner that silently continues past a shape it did not expect is a bypass,
not a scanner.

Rules are data, loaded from contracts/model-gateway/redaction-rules.yaml —
a plain YAML contract file, resolved through pathlib from this module's own
location rather than a bare relative path, so the loader behaves the same
regardless of the process working directory.

The firewall is defense-in-depth on top of the lawful-basis/consent regime,
and its pattern coverage is known to be incomplete — which is exactly why
every block is written to gate_decisions as an audit row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REDACTION_RULES_PATH = REPO_ROOT / "contracts" / "model-gateway" / "redaction-rules.yaml"

_rules: dict[str, Any] | None = None
_compiled: list[tuple[str, re.Pattern[str]]] | None = None


@dataclass(frozen=True)
class RedactionResult:
    """Outcome of one scan."""

    blocked: bool
    matched_pattern_id: str | None = None

    @property
    def outcome(self) -> str:
        return "blocked" if self.blocked else "ok"


def load_rules(path: Path | None = None) -> dict[str, Any]:
    """Load (and cache) the YAML rules contract."""
    global _rules
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if _rules is None:
        _rules = yaml.safe_load(REDACTION_RULES_PATH.read_text(encoding="utf-8")) or {}
    return _rules


def reset_rules() -> None:
    """Drop cached rules/compiled patterns (test hook)."""
    global _rules, _compiled
    _rules = None
    _compiled = None


def _compile(rules: dict[str, Any]) -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for entry in rules.get("patterns") or rules.get("rules") or []:
        pattern_id = str(entry.get("id", "unnamed"))
        compiled.append((pattern_id, re.compile(entry["regex"])))
    # Fixture literals are blocked on exact, case-insensitive match too, so
    # the documented example values are always caught even if a pattern is
    # later loosened.
    fixtures = rules.get("fixtures") or {}
    values: list[str] = []
    if isinstance(fixtures, dict):
        for group in fixtures.values():
            values.extend(str(v) for v in (group if isinstance(group, list) else [group]))
    elif isinstance(fixtures, list):
        values.extend(str(v) for v in fixtures)
    for value in values:
        if value.strip():
            compiled.append((f"fixture:{value}", re.compile(re.escape(value), re.IGNORECASE)))
    return compiled


def _patterns(rules: dict[str, Any]) -> list[tuple[str, re.Pattern[str]]]:
    global _compiled
    if rules is _rules:
        if _compiled is None:
            _compiled = _compile(rules)
        return _compiled
    return _compile(rules)


def _first_match(text: str, patterns) -> str | None:
    for pattern_id, regex in patterns:
        if regex.search(text):
            return pattern_id
    return None


def _as_text(value: Any) -> str:
    """Render any scannable value as text — never skip an unexpected shape."""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def scan_request(payload: dict, rules: dict[str, Any] | None = None) -> RedactionResult:
    """Scan a CompletionRequest payload; block on the first pattern hit."""
    rules = rules if rules is not None else load_rules()
    patterns = _patterns(rules)

    # (a) every messages[*].content, all roles. Non-string content (a
    # content-block array, a dict, anything) is serialized and scanned
    # rather than skipped — see this module's docstring.
    for m in payload.get("messages") or []:
        content = m.get("content") if isinstance(m, dict) else m
        if content is None:
            continue
        matched = _first_match(_as_text(content), patterns)
        if matched:
            return RedactionResult(blocked=True, matched_pattern_id=matched)

    # (b) the serialized tools[] passthrough.
    tools = payload.get("tools") or []
    if tools:
        matched = _first_match(_as_text(tools), patterns)
        if matched:
            return RedactionResult(blocked=True, matched_pattern_id=matched)

    return RedactionResult(blocked=False)
