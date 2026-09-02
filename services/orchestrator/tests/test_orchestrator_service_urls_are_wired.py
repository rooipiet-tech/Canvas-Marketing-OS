"""Every service-URL param ca-orchestrator declares must be passed to it.

deploy-loop-e2e-smoke #118 dead-lettered the whole daily loop at
ingest-signals, cascading to all 13 descendants:

    VaultClientExt requires a resolved base_url -- never a guessed
    hostname (L-0025); call resolve_vault_base_url() first

Cause: modules/orchestrator/container-app.bicep declared
`param vaultApiUrl string = ''` and main.bicep's own
`orchestratorContainerApp` module block never passed it. It passed the
other four (cmosGatewayBaseUrl, cmosMcpWebBaseUrl, cmosGatekeeperBaseUrl,
cmosConsoleBaseUrl) and silently skipped this one, so every deploy-infra
run published ca-orchestrator with VAULT_API_URL="" -- and with
activeRevisionsMode 'Single' that revision took 100% of traffic.

"" is falsy, so resolve_vault_base_url()'s env-var branch fell through to
resolve_live_fqdn(), which shells out to the az CLI. That CLI does not
exist inside ca-orchestrator's container, so it returned None and every
handler building a Vault client raised. The empty default was written
when orchestrator/vault_client.py degraded gracefully on an unreachable
Vault (AC-016/AC-017); L-0025's VaultClientExt refuses to guess instead,
which turned "best-effort" into "hard-fails every handler".

orchestrator-image.yml's "Wire live FQDNs" step re-set the var
imperatively after each image deploy, which is why this was intermittent
rather than permanent: any later deploy-infra silently reverted it.

The param is now required (no default), so this exact omission fails at
template-compile time. This guard covers the general case -- a NEW
service-URL param added with a default and never wired would compile
cleanly and fail the same way at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_BICEP = REPO_ROOT / "infra/main.bicep"
ORCHESTRATOR_APP = REPO_ROOT / "infra/modules/orchestrator/container-app.bicep"
MODULE_REF = "modules/orchestrator/container-app.bicep"

# Params naming another service's address. These are the ones whose value
# is a live FQDN resolved at deploy time, and the ones a client refuses to
# guess at runtime.
_URL_PARAM = re.compile(
    r"^param\s+(?P<name>\w*(?:ApiUrl|BaseUrl))\b", re.MULTILINE | re.IGNORECASE
)


def _declared_url_params() -> set[str]:
    return {m.group("name") for m in _URL_PARAM.finditer(ORCHESTRATOR_APP.read_text())}


def _module_block() -> str:
    """main.bicep's `orchestratorContainerApp` module block, brace-matched."""
    source = MAIN_BICEP.read_text()
    start = source.find(MODULE_REF)
    assert start != -1, f"{MODULE_REF} is not referenced by {MAIN_BICEP.name}"
    cursor = source.index("{", start)
    depth = 0
    for index in range(cursor, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[cursor : index + 1]
    raise AssertionError("unbalanced braces in the orchestratorContainerApp module block")


def test_the_orchestrator_app_declares_service_url_params() -> None:
    """Guard the guard: an empty set would make the real test vacuous."""
    assert _declared_url_params(), (
        f"no *ApiUrl/*BaseUrl params found in {ORCHESTRATOR_APP.name} -- "
        "the naming convention this guard keys on has changed"
    )


@pytest.mark.parametrize("param", sorted(_declared_url_params()))
def test_every_service_url_param_is_passed_by_main(param: str) -> None:
    block = _module_block()
    assert re.search(rf"^\s*{re.escape(param)}\s*:", block, re.MULTILINE), (
        f"main.bicep's orchestratorContainerApp block never passes {param!r}, so "
        f"ca-orchestrator ships with it empty. An empty service URL is not a "
        f"degraded mode -- the clients raise on it (L-0025), dead-lettering every "
        f"task whose handler builds that client."
    )
