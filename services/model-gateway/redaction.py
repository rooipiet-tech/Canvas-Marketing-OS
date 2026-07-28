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
a plain YAML contract file, resolved through config.contracts_dir() rather
than a bare relative path, so the loader behaves the same regardless of the
process working directory AND regardless of whether a repository checkout
surrounds the code (it does locally; it does not inside the container image,
which stages the file and sets CONTRACTS_DIR — see config.contracts_dir()).

The firewall is defense-in-depth on top of the lawful-basis/consent regime,
and its pattern coverage is known to be incomplete — which is exactly why
every block is written to gate_decisions as an audit row.

MATCHED-PATTERN IDS ARE OPAQUE. ``RedactionResult.matched_pattern_id`` is the
only thing a scan reports about *what* it hit, and it travels into the
caller-facing 400 body and into the permanent gate_decisions.reason audit
column. It is therefore always a contract-side coordinate — a pattern's ``id``
from the rules file, or ``fixture:<group>:<index>`` — and never any part of
the matched text. Nothing in this module returns, logs, or stores the matched
value itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
import yaml


def redaction_rules_path() -> Path:
    """Resolved location of the frozen redaction-rules contract file."""
    return config.contracts_dir() / "model-gateway" / "redaction-rules.yaml"


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
        _rules = yaml.safe_load(redaction_rules_path().read_text(encoding="utf-8")) or {}
    return _rules


def reset_rules() -> None:
    """Drop cached rules/compiled patterns (test hook)."""
    global _rules, _compiled
    _rules = None
    _compiled = None


# Group name used for the (currently unused) bare-list form of `fixtures:`,
# so an id is always three colon-separated segments regardless of shape.
_UNGROUPED_FIXTURES = "ungrouped"


def _fixture_entries(fixtures: Any) -> list[tuple[str, int, str]]:
    """Flatten the contract's ``fixtures:`` block into (group, index, value).

    Accepts both shapes the loader has always tolerated: a mapping of group
    name -> list (what contracts/model-gateway/redaction-rules.yaml uses:
    ``client_names``, ``emails``), and a bare list. The group name and the
    index are the only things that ever become part of a pattern id — see
    ``_compile``.
    """
    entries: list[tuple[str, int, str]] = []
    if isinstance(fixtures, dict):
        for group, members in fixtures.items():
            values = members if isinstance(members, list) else [members]
            for index, value in enumerate(values):
                entries.append((str(group), index, str(value)))
    elif isinstance(fixtures, list):
        for index, value in enumerate(fixtures):
            entries.append((_UNGROUPED_FIXTURES, index, str(value)))
    return entries


def _compile(rules: dict[str, Any]) -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for entry in rules.get("patterns") or rules.get("rules") or []:
        pattern_id = str(entry.get("id", "unnamed"))
        compiled.append((pattern_id, re.compile(entry["regex"])))
    # Fixture literals are blocked on exact, case-insensitive match too, so
    # the documented example values are always caught even if a pattern is
    # later loosened.
    #
    # DR-4 — THE ID IS OPAQUE AND NEVER EMBEDS THE MATCHED VALUE. A fixture
    # value IS the personal information (a real client name, a real email);
    # completion.py puts matched_pattern_id verbatim into both the 400
    # REDACTION_BLOCKED body and the gate_decisions.reason audit column, so an
    # id of the form f"fixture:{value}" echoed the client's name straight back
    # to the caller and wrote it, unredacted and permanently, into the Vault —
    # defeating the firewall through its own audit trail. The id is therefore
    # built from contract-side coordinates only (the group key and the
    # position within it, e.g. "fixture:client_names:0"), which are stable,
    # value-free, and enough for an auditor to look the rule up in the frozen
    # contract file.
    for group, index, value in _fixture_entries(rules.get("fixtures") or {}):
        if value.strip():
            compiled.append(
                (f"fixture:{group}:{index}", re.compile(re.escape(value), re.IGNORECASE))
            )
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
