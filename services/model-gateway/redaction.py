"""Redaction firewall — runs before any provider adapter call.

SCAN SCOPE, stated explicitly so it is never left implicit:

  (a) every messages[*].content with role in {user, assistant, tool}; and
  (b) the serialized tools[] payload — the frozen contract's free-form
      `additionalProperties: true` passthrough.

`system`-role content is NOT scanned — see the INCIDENT note below for
why, and why that is a role-scoped narrowing rather than a general
weakening of coverage.

(b) matters as much as (a): client-identifying data smuggled into a tool
definition (a description, an enum value, a default) would bypass a
messages-only scanner entirely, and tools[] is the one part of the request
the contract deliberately does not constrain.

INCIDENT (2026-08-03, live — escalation 13, deploy-loop-e2e-smoke #19):
functions 09/42/02's own prompt.md system prompts — static, developer-
authored, checked-into-git English text, never derived from ingested or
end-user data — repeatedly trip the frozen `full-name-like` pattern
(any two consecutive Title-Case words)
via completely ordinary product/place names and section headings
("Market Intelligence", "Brand Steward", "South African", "Microsoft
Fabric", ...). Since this scanner previously covered every role
including system, EVERY call these three functions ever made to
model-gateway was structurally guaranteed to be blocked before reaching
a provider — not a proof-circuit-specific issue; the real production
signal->brief->draft->QA->approval loop could never complete a single
real LLM call either. `contracts/model-gateway/redaction-rules.yaml` is
one of this repo's 9 hash-guarded frozen contract files and was correctly
left untouched — its own scan-scope intent (documented in this module,
not the frozen file) was ITSELF the thing narrowed here, following an
explicit ruling: `system`-role messages are structurally never a vector
for a data subject's PII to reach a provider, because — orchestrator's
own gateway_client.py's `complete()` confirms this for every current
caller — `system_prompt` is always `_read_prompt()`'s output (a static
file read), never any ingested/dynamic/caller-supplied value; this holds
for `system` role specifically because it is the universal LLM-API
convention (OpenAI, Anthropic, etc.) for "developer/operator instructions,
never end-user content" — not an accident of this one client's current
usage. `user`/`assistant`/`tool` roles (where real ingested signals,
fetched content, and any future caller's dynamic data actually flow)
remain fully scanned, unchanged.

INCIDENT 2 (2026-08-04, heartbeat round 15, deploy-loop-e2e-smoke #33):
unlike the system-prompt case above, `ingest-signals`' `user`-role content
is genuinely dynamic, ingested, real-world text — fetched news article
bodies from `functions/09-market-intelligence-director/fetch_sources.yaml`'s
3 public domains — so the general rule above (user role stays fully
scanned) still holds by default. What changed here is narrower: Log
Analytics showed ALL 4 configured sources tripping `full-name-like` on
EVERY dispatch attempt (place names, product names, headline phrasing —
the exact same false-positive class as INCIDENT 1, just arriving via
`user` role instead of `system`), so PR #63's per-source drop-and-retry
fallback (round 14) always exhausted to zero surviving sources and
dead-lettered the task. Pieter's explicit ruling (round 15): these
specific sources are already-public content before this request ever
happens (public RSS feeds, a public Microsoft Learn page — confirmed by
reading fetch_sources.yaml, never Canvas client/customer data), so
`full-name-like` specifically should not block them. Narrowed via
`scan_request()`'s new `exempt_pattern_ids` parameter, invoked ONLY by
completion.py's `content_class == "public_source_content"` branch, which
is itself set ONLY by orchestrator's ingest-signals dispatch handler (see
gateway_client.py/dispatch.py). This narrows exactly ONE pattern
(`full-name-like`) for exactly ONE explicitly-named content class — the
other 3 heuristic patterns (`email-address`, `sa-phone-number`,
`sa-id-number`) and every fixture exact-match (real client names/emails)
keep scanning this content unchanged, since none of those have any
legitimate reason to appear in public news prose and a real hit there
would still be worth blocking. Every other `user`-role caller in the
system is entirely unaffected — this is not a role-scoped or blanket
change, it is a single named content-class exemption for a single named
pattern, requested and set explicitly by its one caller.

`content_class == "public_source_content"` is no longer set by only one
caller — see INCIDENT 3 below for the current, complete list. The
exemption mechanism itself (narrowed to exactly the `full-name-like`
pattern, exactly this one content class) is unchanged; what has grown is
the number of orchestrator call sites explicitly authorised to set it.

INCIDENT 3 (2026-08-04/05, heartbeat rounds 17-19, deploy-loop-e2e-smoke
various): two further orchestrator call sites were explicitly authorised
by Pieter, on the same underlying reasoning as INCIDENT 2 (content that
is either already-public or static/developer-authored is not new PII by
being reviewed or quoted one hop later), each its own named ruling rather
than a silent widening:
  - round 17/18 (PR #68, F-QA-REVIEW-PUBLIC-SOURCE): `qa_review_handler`'s
    review of a rendered daily-brief's body -- the SAME already-public
    news text `ingest-signals` fetched, just one hop downstream.
  - round 19 (PR #71 follow-up, F-QA-REVIEW-DRAFT-CONTENT-PUBLIC-SOURCE):
    `qa_review_handler`'s review of a drafted LinkedIn post (the
    `channel=="linkedin"` lineage) -- static, developer-authored
    positioning.md content, not third-party PII, which round 18's PR #68
    had deliberately left un-exempted pending real content to test the
    "client-free generic" assumption against; that assumption did not
    hold once real content existed (see dispatch.py's own comment at
    this call site for the full account).
Every set-site remains in `services/orchestrator/orchestrator/dispatch.py`
only -- no gateway-side code chooses this content_class for itself, and
the exemption stays scoped to exactly the `full-name-like` pattern
regardless of how many callers are authorised to request it.

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


def scan_request(
    payload: dict,
    rules: dict[str, Any] | None = None,
    *,
    exempt_pattern_ids: frozenset[str] = frozenset(),
) -> RedactionResult:
    """Scan a CompletionRequest payload; block on the first pattern hit.

    ``exempt_pattern_ids`` narrows the pattern set for THIS scan only — never
    the loaded/cached rules themselves (see ``_patterns()``'s own cache,
    which this never touches). It exists for a single, explicit, narrowly-
    scoped caller: completion.py's ``content_class == "public_source_content"``
    branch (F-INGEST-PUBLIC-SOURCE, 4 Aug 2026, heartbeat round 15, Pieter's
    explicit ruling) — see that module's own comment for the full reasoning.
    An empty (default) set changes nothing: every existing caller that never
    passes this argument scans the full, unmodified pattern list exactly as
    before.
    """
    rules = rules if rules is not None else load_rules()
    patterns = _patterns(rules)
    if exempt_pattern_ids:
        patterns = [(pid, rx) for pid, rx in patterns if pid not in exempt_pattern_ids]

    # (a) every messages[*].content with role != "system" (see this
    # module's docstring INCIDENT note for why system-role content is
    # exempt). Non-string content (a content-block array, a dict,
    # anything) is serialized and scanned rather than skipped.
    for m in payload.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "system":
            continue
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
