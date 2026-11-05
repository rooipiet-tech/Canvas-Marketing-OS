"""Every workflow that deploys infra/main.bicep must supply every parameter
main.bicep REQUIRES (declared with no default).

TD-03 (6 Sep 2026) added `param vaultApiToken string` -- required, no default
-- and its own comments and runbook say "supplied at deploy time by the
workflow (openssl rand), same pattern as administratorLoginPassword". The
workflow step it describes was never added. From then on every deploy-pipeline
run on main failed in `infrastructure / what-if` with

    ERROR: Missing input parameters: vaultApiToken

and every job downstream of it (pgbouncer, orchestrator image, governance,
gateway, MCP servers, console, analytics, the loop smoke test) was skipped, so
nothing merged to main deployed. deploy-governance.yml additionally never
passed `consoleOperatorsGroupId`, a second required parameter added the same
way. The required parameters are exactly what a compile-only check cannot see:
validate_bicep.sh compiles the template, which succeeds without them.

This is the same bug class as L-0065 (a shared template changed, only some
of the workflows that deploy it updated), so it is checked for ALL required
parameters and ALL workflows, not just the two that failed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_BICEP = REPO_ROOT / "infra/main.bicep"
WORKFLOWS = REPO_ROOT / ".github/workflows"
PARAMETER_FILES = ("infra/dev.parameters.json", "infra/prod.parameters.json")

# `param name type` with nothing after the type but an optional comment: a
# default would put `= ...` there. `@secure()`/`@description()` sit on earlier
# lines, so they never interfere.
_REQUIRED_PARAM = re.compile(r"^param\s+(\w+)\s+\w+\s*(?://.*)?$", re.MULTILINE)
_AZ_DEPLOYMENT = re.compile(r"az deployment group (?:create|what-if)\b")
_INLINE_PARAMETER = re.compile(r"--parameters\s+(\w+)=")
_PARAMETER_FILE_ARG = re.compile(r"--parameters\s+@")


def _required_params() -> set[str]:
    return set(_REQUIRED_PARAM.findall(MAIN_BICEP.read_text()))


def _supplied_by_every_parameter_file() -> set[str]:
    """Names present in dev AND prod parameter files: what `--parameters
    @$PARAMETERS_FILE` can be relied on to supply whichever one is chosen."""
    per_file = [
        set(json.loads((REPO_ROOT / name).read_text()).get("parameters", {}))
        for name in PARAMETER_FILES
    ]
    return set.intersection(*per_file)


def _main_bicep_invocations() -> list[tuple[str, int, str]]:
    """(workflow file, 1-based line, full command) for every `az deployment
    group create|what-if` of infra/main.bicep, with `\\` continuations joined."""
    found: list[tuple[str, int, str]] = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        lines = workflow.read_text().splitlines()
        for index, line in enumerate(lines):
            if not _AZ_DEPLOYMENT.search(line):
                continue
            command, cursor = line, index
            while command.rstrip().endswith("\\") and cursor + 1 < len(lines):
                cursor += 1
                command += " " + lines[cursor]
            if "--template-file infra/main.bicep" in command:
                found.append((workflow.name, index + 1, command))
    return found


def _supplied(command: str) -> set[str]:
    supplied = set(_INLINE_PARAMETER.findall(command))
    if _PARAMETER_FILE_ARG.search(command):
        supplied |= _supplied_by_every_parameter_file()
    return supplied


def test_the_scan_finds_the_known_deployments_and_required_params() -> None:
    """Guard the guard: finding nothing would make the real test vacuous."""
    assert {"administratorLoginPassword", "vaultApiToken"} <= _required_params(), (
        f"required-parameter scan found {sorted(_required_params())}"
    )
    by_workflow = [name for name, _line, _cmd in _main_bicep_invocations()]
    assert by_workflow.count("deploy-infra.yml") >= 3, by_workflow
    assert by_workflow.count("deploy-governance.yml") >= 2, by_workflow


@pytest.mark.parametrize(
    "workflow, line, command",
    _main_bicep_invocations(),
    ids=lambda value: str(value) if not isinstance(value, str) or len(value) < 40 else "cmd",
)
def test_every_main_bicep_deployment_supplies_every_required_param(
    workflow: str, line: int, command: str
) -> None:
    missing = sorted(_required_params() - _supplied(command))
    assert not missing, (
        f"{workflow}:{line} deploys infra/main.bicep without {missing}. "
        f"`az` fails with 'Missing input parameters', so the job fails and every job "
        f"after it is skipped. Pass each one with --parameters, or give the param a "
        f"default in main.bicep."
    )
