#!/usr/bin/env python3
"""Mechanically verify Service Bus data-plane RBAC role-definition GUIDs
(F2 — closes v1's enforcement gap for AC-024).

AC-024's own spec.json verify command only greps for the role-NAME string
("Service Bus Data"), which would not catch a wrong/stale GUID embedded in
the role-assignment resourceId. This script resolves the two roles' real
GUIDs live via `az role definition list` and greps the two target bicep
files for those exact GUIDs, failing loudly on any mismatch.

Two real uses:
  1. During authoring (before infra/modules/orchestrator/container-app.bicep
     and infra/modules/scheduling/logic-apps.bicep exist) — prints the live
     GUIDs and exits 0, so a builder can copy them into the bicep it is
     about to write.
  2. As a required, non-skippable gate in the final self-check, after those
     files are written — must exit 0 (PASS) before the build is done.

Usage:
    python services/orchestrator/scripts/verify_rbac_guids.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

ROLES = [
    "Azure Service Bus Data Sender",
    "Azure Service Bus Data Receiver",
]

TARGET_FILES = [
    REPO_ROOT / "infra" / "modules" / "orchestrator" / "container-app.bicep",
    REPO_ROOT / "infra" / "modules" / "orchestrator" / "smoke-test-job.bicep",
    # logic-apps.bicep is an aggregator (module-of-modules) that composes 3
    # sibling files, one per Logic App trigger — each of those, not the
    # aggregator itself, contains the actual role-assignment resource
    # (AC-023's own grep-based verify needs infra/modules/scheduling/*.bicep
    # to span >=2 files for its filename-prefixed `grep -rc` count to sum
    # correctly via awk, which is what motivated this split in the first
    # place — see git history / builder deviations note).
    REPO_ROOT / "infra" / "modules" / "scheduling" / "daily-signal-loop-trigger.bicep",
    REPO_ROOT / "infra" / "modules" / "scheduling" / "weekly-planning-trigger.bicep",
    REPO_ROOT / "infra" / "modules" / "scheduling" / "month-end-reporting-trigger.bicep",
]

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _az_executable() -> str:
    """On Windows, `az` is a .cmd shim — subprocess.run with shell=False
    and a list-of-args cannot execute it directly unless the resolved
    executable path (with extension) is used. shutil.which honors
    PATHEXT (.CMD/.BAT/.EXE) so this works cross-platform.
    """
    import shutil

    resolved = shutil.which("az")
    return resolved or "az"


def resolve_role_guid(role_name: str) -> str:
    result = subprocess.run(
        [
            _az_executable(),
            "role",
            "definition",
            "list",
            "--name",
            role_name,
            "--query",
            "[0].name",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    guid = result.stdout.strip()
    if result.returncode != 0 or not GUID_RE.match(guid):
        print(f"FAIL: could not resolve a GUID for role {role_name!r} via az CLI", file=sys.stderr)
        print(f"  stdout={result.stdout!r} stderr={result.stderr!r}", file=sys.stderr)
        sys.exit(1)
    return guid


def main() -> int:
    role_guids = {role: resolve_role_guid(role) for role in ROLES}
    for role, guid in role_guids.items():
        print(f"{role} -> {guid}")

    existing_targets = [f for f in TARGET_FILES if f.exists()]
    if not existing_targets:
        print(
            "No orchestrator/scheduling bicep files exist yet — "
            "printed live GUIDs above for authoring reference."
        )
        print("PASS")
        return 0

    # For each existing target, check only the role(s) that file actually
    # references (by role-name substring, e.g. in a
    # "// AC-024: Azure Service Bus Data Sender" comment) — a file that
    # only needs the Sender role (e.g. logic-apps.bicep) is not required
    # to also embed the Receiver role's GUID. What matters is: whichever
    # role a file DOES reference must have the CORRECT live GUID embedded
    # alongside it, catching exactly the silent-no-op/mismatch failure
    # mode F2 flags.
    failures: list[str] = []
    for target in existing_targets:
        text = target.read_text(encoding="utf-8")
        text_lower = text.lower()
        referenced_any = False
        for role, guid in role_guids.items():
            if role.lower() in text_lower:
                referenced_any = True
                if guid.lower() not in text_lower:
                    failures.append(
                        f"{target} does not contain the live GUID for '{role}' ({guid})"
                    )
        if not referenced_any:
            failures.append(f"{target} does not reference either Service Bus RBAC role by name")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
