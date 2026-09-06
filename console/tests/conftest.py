"""Shared test fixtures.

TD-04 fix: `require_principal` (app/auth.py) now enforces security-group
membership in addition to authentication, mirroring `consoleAuth`'s
`allowedPrincipals.groups` requirement (console-app.bicep) — the same
CONSOLE_OPERATORS_GROUP_ID value drives both the IaC-level check and this
code-level backstop. Every test that exercises an authenticated route must
therefore run with that env var set AND present a principal whose `groups`
claim includes it — `principal_headers()` below builds exactly that shape.
"""

from __future__ import annotations

import base64
import json

import pytest

TEST_OPERATORS_GROUP_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _console_operators_group_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSOLE_OPERATORS_GROUP_ID", TEST_OPERATORS_GROUP_ID)


def principal_headers(
    principal_id: str = "operator-1",
    name: str | None = "operator@example.com",
    groups: tuple[str, ...] = (TEST_OPERATORS_GROUP_ID,),
) -> dict[str, str]:
    """Build Easy-Auth-shaped request headers for tests.

    Group membership has no dedicated Easy Auth header — it only ever
    travels inside the base64 X-MS-CLIENT-PRINCIPAL claims blob (see
    app/auth.py's `principal_from_headers` docstring) — so this always
    attaches that blob, even though the dedicated ID/NAME headers are also
    set (matching how a real Easy Auth request carries both).
    """
    claims = [{"typ": "groups", "val": group} for group in groups]
    blob = {"auth_typ": "aad", "claims": claims}
    encoded = base64.b64encode(json.dumps(blob).encode("utf-8")).decode("ascii")
    headers = {
        "X-MS-CLIENT-PRINCIPAL-ID": principal_id,
        "X-MS-CLIENT-PRINCIPAL": encoded,
    }
    if name is not None:
        headers["X-MS-CLIENT-PRINCIPAL-NAME"] = name
    return headers
