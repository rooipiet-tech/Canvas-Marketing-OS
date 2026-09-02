"""deploy-pipeline.yml must stay the only holder of `cmos-dev-deploy`.

WHAT WENT WRONG. Eight deploy workflows each declared
`concurrency: group: cmos-dev-deploy` to serialise their writes to the
same live resources. That group does not queue -- GitHub permits ONE
pending entry, and a newly-queued job evicts whichever was already
waiting. `cancel-in-progress: false` protects the RUNNING job, not the
pending one. A merge fires all eight, and whichever arrives last wins.

A cancelled run is grey, not red, so this was invisible for weeks:
deploy-loop-e2e-smoke did not actually execute between 10 Aug and 2 Sep
(18 consecutive cancels/skips) and deploy-mcp has not deployed since
10 Aug. The run being discarded was the one whose job is to notice
breakage.

deploy-pipeline.yml replaces the race with one ordered run. These tests
pin the two invariants that make that work, both of which are silent
failures if broken:

  * A called workflow must NOT re-declare the group its caller already
    holds -- it would deadlock against its own parent, forever.

  * A called workflow's jobs must not be gated on an `if:` that only
    allow-lists event names. Under `workflow_call`, `github.event_name`
    is the CALLER's event (push), never 'workflow_call' -- so
    `if: github.event_name == 'workflow_dispatch' || ...` evaluates
    false and the job SKIPS. A skipped job reports success, so the
    pipeline would go green having deployed nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github/workflows"
PIPELINE = WORKFLOWS / "deploy-pipeline.yml"
SHARED_GROUP = "cmos-dev-deploy"


def _load(path: Path) -> dict[str, Any]:
    # PyYAML parses a bare `on:` key as the boolean True.
    return yaml.safe_load(path.read_text())


def _triggers(doc: dict[str, Any]) -> dict[str, Any]:
    on = doc.get(True, doc.get("on"))
    return on if isinstance(on, dict) else {}


def _called_workflows() -> list[Path]:
    """Every workflow deploy-pipeline.yml calls, in declaration order."""
    jobs = _load(PIPELINE).get("jobs") or {}
    out = []
    for job in jobs.values():
        uses = job.get("uses")
        if uses:
            # removeprefix, not lstrip: lstrip("./") strips ANY leading
            # '.' or '/', which eats the dot of ".github" as well.
            out.append(REPO_ROOT / uses.removeprefix("./"))
    return out


def test_the_pipeline_calls_the_whole_deploy():
    """Guard the guard: an empty list would make every test below vacuous."""
    called = _called_workflows()
    assert len(called) >= 8, f"pipeline calls only {len(called)} workflow(s)"
    for path in called:
        assert path.exists(), f"pipeline calls a workflow that does not exist: {path}"


def test_the_pipeline_holds_the_shared_concurrency_group():
    concurrency = _load(PIPELINE).get("concurrency") or {}
    assert concurrency.get("group") == SHARED_GROUP
    # Serialising is the entire point; cancelling the in-flight deploy
    # would leave cmos-dev half-applied.
    assert concurrency.get("cancel-in-progress") is False


@pytest.mark.parametrize("path", _called_workflows(), ids=lambda p: p.name)
def test_a_called_workflow_never_takes_its_callers_group(path: Path):
    doc = _load(path)

    workflow_group = (doc.get("concurrency") or {}).get("group", "")
    assert SHARED_GROUP not in str(workflow_group), (
        f"{path.name} declares the {SHARED_GROUP!r} group its own caller already "
        "holds. Called as a nested job of deploy-pipeline, it would wait forever "
        "on a group its parent will not release until the child finishes."
    )

    for job_name, job in (doc.get("jobs") or {}).items():
        job_group = (job.get("concurrency") or {}).get("group", "")
        assert SHARED_GROUP not in str(job_group), (
            f"{path.name} job {job_name!r} takes the {SHARED_GROUP!r} group -- "
            "same self-deadlock as above, one level down."
        )


@pytest.mark.parametrize("path", _called_workflows(), ids=lambda p: p.name)
def test_a_called_workflow_is_actually_callable(path: Path):
    assert "workflow_call" in _triggers(_load(path)), (
        f"{path.name} is called by deploy-pipeline.yml but declares no "
        "`workflow_call` trigger, so the pipeline cannot invoke it at all."
    )


@pytest.mark.parametrize("path", _called_workflows(), ids=lambda p: p.name)
def test_no_job_is_gated_on_an_event_name_allow_list(path: Path):
    """The silent one.

    `github.event_name` under workflow_call is the CALLER's event, so a
    condition that allow-lists event names evaluates false and the job
    skips -- reporting success while deploying nothing. Conditions may
    still EXCLUDE an event (`!= 'workflow_run'`), which is how these were
    rewritten; they may not require one.
    """
    for job_name, job in (_load(path).get("jobs") or {}).items():
        condition = str(job.get("if", ""))
        if "event_name" not in condition:
            continue
        assert "==" not in condition.split("event_name")[1][:8], (
            f"{path.name} job {job_name!r} requires a specific github.event_name:\n"
            f"    {condition.strip()}\n"
            "Under workflow_call that context holds the CALLER's event, so this "
            "skips silently and the pipeline goes green having done nothing. "
            "State it as an exclusion (event_name != 'workflow_run') instead."
        )


# --- L-0022 / L-0023: no CLI container-argument overrides --------------------

CONTAINER_ARG_FLAGS = ("--command", "--args", "--env-vars", "--yaml")


def _uncommented_lines(path: Path) -> list[tuple[int, str]]:
    """Workflow lines with YAML comment lines removed.

    Only whole-line comments are dropped: a '#' inside a run: script is
    shell, and a flag mentioned there would still be a real invocation.
    """
    out = []
    for number, line in enumerate(path.read_text().split("\n"), start=1):
        if line.lstrip().startswith("#"):
            continue
        out.append((number, line))
    return out


def test_no_workflow_passes_cli_container_args():
    """F-CONSOLE-SMOKE-CLI-ARGS.

    deploy-console.yml created caj-console-smoke inline with
    `--command /bin/sh --args '-c' "$SMOKE_CMD"`. az's argparse reads a
    value starting with '-' as an option, so '-c' was never passed and
    every run died with `unrecognized arguments: -c ...` -- on the create
    AND the `job update` fallback. All eight deploy-console runs from
    31 July onward failed on it. The image build and `az containerapp
    update` kept succeeding, so ca-console deployed and only its
    verification was broken, which is how a permanently red workflow went
    a month without being looked at.

    It was also the ONLY workflow here passing those flags -- the sole
    violator of a rule the repo had already written down, in
    deploy-governance.yml's own words: the job's real command and env
    belong in its Bicep-persisted template, and `start` should be bare.
    L-0022: any container-argument flag replaces the WHOLE container
    spec. L-0023: `--yaml` overrides can be silently ignored.

    Both of those are silent corruptions rather than errors, which is why
    this is a guard and not a comment.
    """
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in _uncommented_lines(path):
            if not any(flag in line for flag in CONTAINER_ARG_FLAGS):
                continue
            # Only container-argument flags aimed at a JOB. `az containerapp
            # update --set-env-vars` on an APP is a different, legitimate
            # call, and --env-vars on `job create` is the one this forbids.
            if "--set-env-vars" in line:
                continue
            offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, (
        "CLI container-argument override(s) found:\n  "
        + "\n  ".join(offenders)
        + "\n\nDeclare the job's command/env in its Bicep module and issue a bare "
        "`az containerapp job start` instead. L-0022: these flags replace the whole "
        "container spec. L-0023: --yaml overrides can be silently ignored. And az "
        "cannot pass a value beginning with '-' through --args at all, which is what "
        "broke every deploy-console run for a month."
    )
