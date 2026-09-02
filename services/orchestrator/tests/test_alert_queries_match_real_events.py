"""A2 — every log event an alert rule searches for is one something emits.

THE BUGS THIS EXISTS BECAUSE OF. The repository's independent reviewer
found that three of the five rules in infra/modules/monitoring/alerts.bicep
searched for strings no code has ever logged:

  * `task_completed` — appears in NO source file at any point in this
    repo's history. It came from L-0063's evidence prose, which is itself
    inaccurate about that day's events. The real completion-path event is
    `task_dispatched` (worker.py:301). Because an absence-alert's
    `summarize count()` returns one row of 0 on empty input, the Sev 1
    stall rule was satisfied on its first evaluation and every hour after
    — permanently firing, autoMitigate flapping.
  * `budget_hard_breach` / `budget_exceeded` — neither is emitted.
    model-gateway logs `"event": "completion"` with the state in a
    separate `budget_state` field, so the Sev 2 rule was permanently
    silent, which is worse than absent: the module looked like it covered
    budget breaches.
  * `cascade_dead_letter` — a function name, not an event. Term-based
    `has` could never match `task_cascade_dead_lettered`.

One rule wrong is a typo. Three is a method failure, and CLAUDE.md names
it: "An external identifier — a model id, a hostname, a vendor field name
— is a hypothesis until a live call returns it" (L-0026, L-0068, L-0078).
A KQL search term is exactly that kind of identifier, and a Bicep template
compiles just as cleanly with a dead one. `validate_bicep.sh` cannot catch
this and never could.

So this test is the check that can: it reads the search terms out of the
alert queries and requires each to appear as a logged event name in the
services' own source.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ALERTS_BICEP = REPO_ROOT / "infra/modules/monitoring/alerts.bicep"
SERVICES_DIR = REPO_ROOT / "services"

# A term may be searched for before its emitter exists, but only
# deliberately and only with a reason recorded here. An empty reason, or a
# term that has quietly become permanent, is the thing this test exists to
# surface.
#
# EMPTY SINCE 2 Sep 2026, and that is the mechanism working rather than a
# tidy-up. `buffer_queue_depth_high` sat here while its emitter lived on
# PR #137 (B1) and this module's rule had nothing to match. #137 merged,
# main started emitting it, and
# test_the_exemptions_are_still_needed_and_still_explained failed on the
# very next run -- naming the term and the fix. The exemption could not
# have been dropped before then: without it,
# test_every_alert_search_term_is_actually_emitted fails on this branch,
# where nothing emitted the event yet. So the two states are both
# guarded, and neither can be reached silently.
KNOWN_ABSENT_EMITTERS: dict[str, str] = {}


def _searched_terms() -> set[str]:
    """Every string literal the alert queries match log lines against.

    Covers both `has "x"` and `has_any ("x", "y")`. Deliberately reads the
    queries rather than a hand-kept list, so a rule added later is covered
    without anyone remembering to update this.
    """
    source = ALERTS_BICEP.read_text(encoding="utf-8")
    # Only inside query blocks, so prose in comments is not mistaken for a
    # search term.
    queries = re.findall(r"query: '''(.*?)'''", source, re.DOTALL)
    assert queries, "no queries found in alerts.bicep -- this guard would pass vacuously"

    terms: set[str] = set()
    for query in queries:
        terms.update(re.findall(r'\bhas\s+"([a-z0-9_]+)"', query))
        for group in re.findall(r"has_any\s*\(([^)]*)\)", query):
            terms.update(re.findall(r'"([a-z0-9_]+)"', group))
    return terms


def _emitted_log_vocabulary() -> set[str]:
    """Every token a service actually puts into a log line.

    Two kinds, because alert queries legitimately match on both:

      * EVENT NAMES — log_event(logger, LEVEL, "name", ...) and json.dumps
        payloads carrying "event": "name".
      * LOGGED VALUES — a string literal assigned to a field that ends up
        in a payload, e.g. budget_state="hard_breach". The budget rule
        prefilters on one of these before parsing the field exactly.

    The value half is deliberately broad (any `x = "literal"` in service
    source), which trades some strictness for not having to model every
    logging call shape. It stays strong where it matters: none of the four
    dead terms this guard was written for — task_completed,
    budget_hard_breach, budget_exceeded, cascade_dead_letter — appears as
    a string literal anywhere in the services, and cascade_dead_letter in
    particular exists only as a `def`, which this does not match.
    """
    vocabulary: set[str] = set()
    for path in SERVICES_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Event names.
        vocabulary.update(
            re.findall(r'log_event\(\s*\w+,\s*logging\.\w+,\s*"([a-z0-9_]+)"', text)
        )
        vocabulary.update(re.findall(r'"([a-z0-9_]+)",\s*\n\s*(?:task_id|error|path)=', text))
        vocabulary.update(re.findall(r'"event":\s*"([a-z0-9_]+)"', text))
        vocabulary.update(re.findall(r'logger\.\w+\(\s*"([a-z0-9_]+)"', text))
        # Logged values.
        vocabulary.update(re.findall(r'=\s*"([a-z0-9_]+)"', text))
    return vocabulary


def test_the_guard_can_see_both_sides() -> None:
    """Vacuity check (L-0005, L-0046, L-0059): a discovery regex that stops
    matching would make every assertion below trivially true."""
    assert len(_searched_terms()) >= 4
    assert len(_emitted_log_vocabulary()) >= 20


def test_every_alert_search_term_is_actually_emitted() -> None:
    emitted = _emitted_log_vocabulary()
    unmatched = sorted(t for t in _searched_terms() if t not in emitted)

    unexplained = [t for t in unmatched if t not in KNOWN_ABSENT_EMITTERS]
    assert not unexplained, (
        "alert rules search for event(s) nothing emits: "
        + ", ".join(unexplained)
        + " -- a rule with a dead search term is permanently silent, or (for an "
        "absence alert) permanently firing. Point it at the real event name, or "
        "record it in KNOWN_ABSENT_EMITTERS with the reason."
    )


def test_the_exemptions_are_still_needed_and_still_explained() -> None:
    """An exemption that has quietly become permanent is the failure this
    whole test exists to prevent, one level up."""
    emitted = _emitted_log_vocabulary()
    for term, reason in KNOWN_ABSENT_EMITTERS.items():
        assert reason.strip(), f"{term} is exempted with no reason recorded"
        if term in emitted:
            raise AssertionError(
                f"{term} IS emitted now -- remove it from KNOWN_ABSENT_EMITTERS so "
                "the guard covers it like every other term"
            )


def test_the_stall_rule_counts_a_signal_the_orchestrator_really_logs() -> None:
    """Named explicitly because this one is the most dangerous to get wrong.

    An absence alert's `summarize count()` returns a single row of 0 on
    empty input, so a dead term does not make the rule silent — it makes it
    fire on every evaluation, forever, at Sev 1.
    """
    source = ALERTS_BICEP.read_text(encoding="utf-8")
    assert 'has "task_dispatched"' in source
    assert 'has "task_completed"' not in source, (
        "task_completed has never been emitted by anything in this repository"
    )
