"""Logic Apps recurrence blocks must use a frequency their schedule allows.

deploy-infra #138 failed live, and failed the WHOLE `main` deployment with
it, on a single invalid trigger:

    InvalidWorkflowTriggerRecurrenceSchedule: The recurrence schedule of
    trigger 'Recurrence' has an invalid recurrence frequency 'Hour'.

publish-trigger.bicep asked for `frequency: 'Hour'` with a
`schedule.weekDays` restricting it to Mon-Fri. Azure only honours
`weekDays` on `frequency: 'Week'`; on any other frequency it rejects the
whole workflow rather than ignoring the field. The intent -- hourly on
weekdays, 06:10-18:10 -- is expressible as Week/interval 1 with every
weekday hour listed, which is what source-discovery-trigger.bicep was
already doing.

`az bicep build`/lint does NOT catch this: the template compiles cleanly
and only the ARM control plane rejects it, so the first signal is a failed
live deploy that takes every other module in main.bicep down with it.
Hence a static guard here, over the same source of truth the deploy reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEDULING_DIR = REPO_ROOT / "infra/modules/scheduling"

# Which schedule keys Azure accepts per recurrence frequency. `minutes` is
# accepted on the sub-day frequencies too; `hours` and `weekDays` are the
# two that narrow it, and weekDays is Week-only.
ALLOWED_SCHEDULE_KEYS = {
    "Week": {"weekDays", "hours", "minutes"},
    "Day": {"hours", "minutes"},
    "Hour": {"minutes"},
    "Minute": set(),
    "Month": {"monthDays", "weekDays", "hours", "minutes"},
}

_FREQUENCY = re.compile(r"^\s*frequency:\s*'(?P<freq>\w+)'", re.MULTILINE)
_SCHEDULE_KEY = re.compile(r"^\s*(?P<key>weekDays|hours|minutes|monthDays):", re.MULTILINE)


def _bicep_files() -> list[Path]:
    return sorted(SCHEDULING_DIR.glob("*-trigger.bicep"))


def _strip_comments(source: str) -> str:
    """Drop `//` comments so commented-out config is not read as live.

    weekly-planning-trigger.bicep carries its intended Week/weekDays block
    commented out above the Day/interval-1 one it actually deploys; without
    this the guard reads that comment and reports a violation the ARM
    control plane would never see. The `(?<!:)` guard keeps `https://` in
    the `$schema` URL from being treated as a comment marker.
    """
    without_full_line = re.sub(r"(?m)^[ \t]*//.*$", "", source)
    return re.sub(r"(?m)(?<!:)//.*$", "", without_full_line)


def test_scheduling_dir_has_trigger_modules() -> None:
    """Guard the guard: a glob that silently matches nothing proves nothing."""
    assert _bicep_files(), f"no *-trigger.bicep found under {SCHEDULING_DIR}"


@pytest.mark.parametrize("path", _bicep_files(), ids=lambda p: p.name)
def test_recurrence_schedule_keys_are_legal_for_their_frequency(path: Path) -> None:
    source = _strip_comments(path.read_text())

    frequencies = _FREQUENCY.findall(source)
    if not frequencies:
        pytest.skip(f"{path.name} declares no recurrence frequency")

    # Every trigger module here defines exactly one Recurrence trigger; if
    # that ever stops being true this needs to become a per-block parse
    # rather than a whole-file one.
    assert len(frequencies) == 1, (
        f"{path.name} declares {len(frequencies)} frequencies ({frequencies}); "
        "this guard assumes one Recurrence trigger per module"
    )
    frequency = frequencies[0]
    assert frequency in ALLOWED_SCHEDULE_KEYS, (
        f"{path.name} uses unknown recurrence frequency {frequency!r}"
    )

    # Only look at keys inside a `schedule: {` block -- `minutes` also
    # appears in unrelated comments and parameter defaults.
    schedule_body = _schedule_block(source)
    if schedule_body is None:
        return

    used = set(_SCHEDULE_KEY.findall(schedule_body))
    illegal = used - ALLOWED_SCHEDULE_KEYS[frequency]
    assert not illegal, (
        f"{path.name}: schedule key(s) {sorted(illegal)} are not valid with "
        f"frequency {frequency!r}. Azure rejects the whole workflow with "
        f"InvalidWorkflowTriggerRecurrenceSchedule -- and the whole main.bicep "
        f"deployment with it. Legal here: {sorted(ALLOWED_SCHEDULE_KEYS[frequency])}."
    )


def _schedule_block(source: str) -> str | None:
    """The body of the first `schedule: { ... }`, brace-matched."""
    start = source.find("schedule: {")
    if start == -1:
        return None
    cursor = source.index("{", start)
    depth = 0
    for index in range(cursor, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[cursor + 1 : index]
    raise AssertionError(f"unbalanced braces in schedule block of {source[:80]!r}")
