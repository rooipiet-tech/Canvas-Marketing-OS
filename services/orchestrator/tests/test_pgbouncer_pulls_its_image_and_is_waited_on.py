"""ca-pgbouncer must be able to pull its image on every deploy, and the deploy
must prove its new revision is Ready before anything uses it.

deploy-pipeline run 90: pgbouncer-app.bicep declared no `registries[]`, so each
full deploy (an ARM PUT) stripped ca-pgbouncer's registry config. The revision
it created could not pull the private ACR image, sat in Activating with an empty
log, and never became Ready. An older revision (ca-pgbouncer--0000003, created
by a manual deploy-pgbouncer run) stayed Ready and active, holding a PREVIOUS
deploy's Postgres password, and the bare app name routed every login to it:
asyncpg "SASL authentication failed" on all 8 probe attempts, while direct
Postgres logins succeeded 8/8 and the old replica's own log showed the matching
"password authentication failed" lines.

Three properties are pinned, each as a pure function over text so it can be
shown to fail on the old shape as well as pass on the new one:

1. the module declares `registries[]` for a CI-built image, but NOT alongside
   the public placeholder (L-0049: a freshly attached identity cannot also be
   referenced by registries[] in the same create);
2. main.bicep feeds it the registry login server from the registry module's
   output (L-0017: a real reference, not a re-typed string);
3. both deploy jobs wait for the latest revision to be Ready right after the
   Bicep deploy and before the first step that needs ca-pgbouncer, fail with
   ground truth if it never is, retire stale active revisions, and skip a
   bootstrap deploy that is still on the placeholder.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
MODULE = REPO / "infra" / "modules" / "pgbouncer-app.bicep"
MAIN = REPO / "infra" / "main.bicep"
WORKFLOW = REPO / ".github" / "workflows" / "deploy-infra.yml"

WAIT_STEP_PREFIX = "Wait for ca-pgbouncer's latest revision to be Ready"
DEPLOY_STEP = "az deployment group create (real deploy)"
FIRST_DEPENDENT_STEP = "Start Vault secret-writer job (loads vault-db-connection-string in-VNet)"


def module_violations(module: str) -> list[str]:
    problems: list[str] = []
    if not re.search(r"^param\s+registryServer\s+string\b", module, re.M):
        problems.append("module has no `param registryServer string`")
    if not re.search(
        r"^var\s+useRegistry\s*=\s*startsWith\(\s*image\s*,\s*'\$\{registryServer\}/'\s*\)",
        module,
        re.M,
    ):
        problems.append("module does not derive useRegistry from the image and registryServer")
    block = re.search(r"^\s*registries:\s*(.+?)\n\s*\]\s*:\s*\[\]", module, re.M | re.S)
    if not re.search(r"^\s*registries:\s*useRegistry\s*\?\s*\[", module, re.M):
        problems.append(
            "registries[] is not conditional on useRegistry — unconditional would "
            "break the maiden deploy (L-0049); absent strips the config (run 90)"
        )
    elif not block or "identity: userAssignedIdentityId" not in block.group(1):
        problems.append("registries[] does not reference userAssignedIdentityId")
    elif "server: registryServer" not in block.group(1):
        problems.append("registries[] does not use registryServer")
    return problems


def main_violations(main: str) -> list[str]:
    m = re.search(r"^module pgbouncer 'modules/pgbouncer-app\.bicep'.*?^\}", main, re.M | re.S)
    if not m:
        return ["main.bicep has no pgbouncer module"]
    if not re.search(
        r"^\s*registryServer:\s*containerRegistry\.outputs\.loginServer\s*$", m.group(0), re.M
    ):
        return [
            "pgbouncer module is not passed "
            "registryServer: containerRegistry.outputs.loginServer"
        ]
    return []


def _deploy_job_steps(workflow_text: str) -> dict[str, list[dict]]:
    d = yaml.safe_load(workflow_text)
    return {
        name: job["steps"]
        for name, job in d["jobs"].items()
        if any(s.get("name") == DEPLOY_STEP for s in job.get("steps", []))
    }


def workflow_violations(workflow_text: str) -> list[str]:
    problems: list[str] = []
    jobs = _deploy_job_steps(workflow_text)
    if len(jobs) < 2:
        problems.append(f"expected the gated and auto-pass deploy jobs, found {sorted(jobs)}")
    for job, steps in jobs.items():
        names = [s.get("name", "") for s in steps]
        waits = [i for i, n in enumerate(names) if n.startswith(WAIT_STEP_PREFIX)]
        if len(waits) != 1:
            problems.append(
                f"{job}: expected exactly one ca-pgbouncer wait step, found {len(waits)}"
            )
            continue
        wait = waits[0]
        if wait < names.index(DEPLOY_STEP):
            problems.append(f"{job}: the wait runs before the Bicep deploy")
        if FIRST_DEPENDENT_STEP in names and wait > names.index(FIRST_DEPENDENT_STEP):
            problems.append(f"{job}: the wait runs after the first step that needs ca-pgbouncer")
        script = steps[wait].get("run", "")
        for needle, why in (
            ("containerapps-helloworld", "does not skip the bootstrap placeholder image"),
            ("latestReadyRevisionName", "never compares latestReadyRevisionName"),
            ("latestRevisionName", "never reads latestRevisionName"),
            ("exit 1", "never fails when the revision does not become Ready"),
            ("--type system", "prints no system log on failure"),
            ("revision deactivate", "does not retire stale active revisions"),
            ("properties.active", "does not look for stale active revisions"),
        ):
            if needle not in script:
                problems.append(f"{job}: wait step {why}")
    return problems


# ---------------------------------------------------------------- real files


def test_module_declares_registries_for_a_ci_built_image_only():
    assert module_violations(MODULE.read_text(encoding="utf-8")) == []


def test_main_passes_the_registry_login_server_by_output_reference():
    assert main_violations(MAIN.read_text(encoding="utf-8")) == []


def test_both_deploy_jobs_wait_for_pgbouncer_before_anything_uses_it():
    assert workflow_violations(WORKFLOW.read_text(encoding="utf-8")) == []


# ------------------------------------------------- non-vacuity: can it fail


OLD_MODULE = """\
param image string
param userAssignedIdentityId string
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  properties: {
    configuration: {
      // Deliberately NO registries[] block
      ingress: {}
    }
  }
}
"""

GOOD_MODULE = """\
param image string
param userAssignedIdentityId string
param registryServer string
var useRegistry = startsWith(image, '${registryServer}/')
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  properties: {
    configuration: {
      registries: useRegistry ? [
        {
          server: registryServer
          identity: userAssignedIdentityId
        }
      ] : []
    }
  }
}
"""


def test_module_check_flags_the_old_module_with_no_registries():
    problems = module_violations(OLD_MODULE)
    assert any("registryServer" in p for p in problems)
    assert any("not conditional" in p for p in problems)


def test_module_check_flags_an_unconditional_registries_block():
    unconditional = GOOD_MODULE.replace(
        "registries: useRegistry ? [", "registries: ["
    ).replace("] : []", "]")
    assert any("not conditional" in p for p in module_violations(unconditional))


def test_module_check_flags_registries_without_the_identity():
    no_identity = GOOD_MODULE.replace("identity: userAssignedIdentityId", "identity: ''")
    assert any("userAssignedIdentityId" in p for p in module_violations(no_identity))


def test_module_check_accepts_the_conditional_shape():
    assert module_violations(GOOD_MODULE) == []


def test_main_check_flags_a_missing_or_retyped_registry_server():
    base = "module pgbouncer 'modules/pgbouncer-app.bicep' = {\n  params: {\n%s  }\n}\n"
    assert main_violations(base % "    image: x\n")
    assert main_violations(base % "    registryServer: 'acr.azurecr.io'\n")
    assert (
        main_violations(base % "    registryServer: containerRegistry.outputs.loginServer\n") == []
    )


def _workflow(steps: list[dict]) -> str:
    return yaml.safe_dump({"jobs": {"a": {"steps": steps}, "b": {"steps": steps}}})


_GOOD_RUN = (
    "case $I in mcr.microsoft.com/azuredocs/containerapps-helloworld*) exit 0;; esac\n"
    "az x --query latestRevisionName\naz x --query latestReadyRevisionName\n"
    "exit 1\naz containerapp logs show --type system\n"
    "az containerapp revision list --query '[?properties.active]'\n"
    "az containerapp revision deactivate\n"
)


def test_workflow_check_flags_a_pipeline_without_the_wait():
    wf = _workflow([{"name": DEPLOY_STEP}, {"name": FIRST_DEPENDENT_STEP}])
    assert any("exactly one" in p for p in workflow_violations(wf))


def test_workflow_check_flags_a_wait_after_its_first_dependent_and_a_hollow_one():
    late = _workflow(
        [
            {"name": DEPLOY_STEP},
            {"name": FIRST_DEPENDENT_STEP},
            {"name": WAIT_STEP_PREFIX, "run": _GOOD_RUN},
        ]
    )
    assert any("after the first step" in p for p in workflow_violations(late))
    hollow = _workflow(
        [
            {"name": DEPLOY_STEP},
            {"name": WAIT_STEP_PREFIX, "run": "echo nothing"},
            {"name": FIRST_DEPENDENT_STEP},
        ]
    )
    problems = workflow_violations(hollow)
    assert any("never fails" in p for p in problems)
    assert any("placeholder" in p for p in problems)
    assert any("retire stale" in p for p in problems)


def test_workflow_check_accepts_a_correct_wait():
    ok = _workflow(
        [
            {"name": DEPLOY_STEP},
            {"name": WAIT_STEP_PREFIX, "run": _GOOD_RUN},
            {"name": FIRST_DEPENDENT_STEP},
        ]
    )
    assert workflow_violations(ok) == []
