"""Every path VaultApiHttpClient requests must exist in vault-api.yaml.

INTEG-001 makes VAULT_API_MODE=real the live setting, which means these
paths stop being documentation and start being requests. The failure mode
if one is wrong is a 404 raised by `response.raise_for_status()` inside a
page render -- discovered by an operator, in production, on a read
surface, rather than here.

The mock cannot catch this: VaultApiMock implements the same Protocol by
returning lists from memory and never sees a URL at all. So the two
clients agree on the Python interface and can disagree completely on the
wire, which is exactly the gap a "pure config change" cutover walks into.

contracts/vault-api.yaml is the frozen contract, and vault's own
routers/objects.py is generated from the same OBJECT_TYPES table, so
checking against the contract checks against both.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from app.clients import vault_api_real
from app.clients.base import VaultApiClient
from app.clients.vault_api_mock import VaultApiMock
from app.clients.vault_api_real import VaultApiHttpClient

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "contracts/vault-api.yaml"


def _contract_get_paths() -> set[str]:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return {path for path, ops in document["paths"].items() if "get" in ops}


def _requested_paths() -> set[str]:
    """Every string literal handed to self._list() in vault_api_real.py.

    Read from the source rather than by calling the methods: calling them
    needs a running server, and this must hold in a unit test.
    """
    tree = ast.parse(inspect.getsource(vault_api_real))
    paths: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "_list" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                paths.add(first.value)
    return paths


def test_the_client_requests_a_path_for_every_list_method():
    """Guard the guard: an empty set would make the real test vacuous."""
    requested = _requested_paths()

    list_methods = [
        name
        for name in dir(VaultApiHttpClient)
        if name.startswith("list_") and callable(getattr(VaultApiHttpClient, name))
    ]
    assert list_methods, "no list_* methods found on VaultApiHttpClient"
    assert len(requested) == len(list_methods), (
        f"parsed {len(requested)} path literal(s) from vault_api_real.py but the client "
        f"has {len(list_methods)} list_* methods {sorted(list_methods)} -- the AST walk "
        "has stopped matching how those methods are written"
    )


@pytest.mark.parametrize("path", sorted(_requested_paths()))
def test_every_requested_path_exists_in_the_frozen_contract(path: str):
    assert path in _contract_get_paths(), (
        f"VaultApiHttpClient GETs {path!r}, which contracts/vault-api.yaml does not "
        "define. Under VAULT_API_MODE=real that is a 404 raised inside a page render; "
        "under the mock it is invisible, because the mock never sees a URL."
    )


def test_both_clients_implement_the_same_protocol_surface():
    """The premise of INTEG-001: the cutover is config, not code.

    If the two clients' method sets ever diverge, flipping the env var
    stops being safe -- some route would call a method the real client
    does not have, and the AttributeError would surface only in the mode
    nothing is tested in.
    """
    protocol_methods = {
        name for name in VaultApiClient.__dict__ if name.startswith("list_")
    }
    assert protocol_methods, "VaultApiClient declares no list_* methods"

    for name in protocol_methods:
        assert hasattr(VaultApiHttpClient, name), f"real client is missing {name}"
        assert hasattr(VaultApiMock, name), f"mock is missing {name}"
