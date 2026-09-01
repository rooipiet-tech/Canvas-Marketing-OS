#!/usr/bin/env python3
"""Fail when mcp-web's egress allow-list and the scan profiles disagree.

WHY THIS EXISTS (F-ALLOWLIST-DRIFT). The hosts a scan may fetch are
written down twice, in two places that change through different routes:

  * functions/_shared/scan-profiles.yaml -- edited by whoever adds a
    market to watch; no deploy needed.
  * MCP_WEB_ALLOWLIST in infra/main.bicep -- edited by whoever deploys;
    mcp-web raises AllowlistViolation for any host not in it.

Nothing connected them. A URL added to a profile but not to the Bicep is
rejected by mcp-web, caught by dispatch.py's per-source try/except, logged
as a warning and skipped -- so the scan quietly runs on fewer sources
instead of failing. That is precisely the silent-degradation class the
completeness floors were added to end, and a floor cannot help when the
source was never reachable in the first place.

Bicep cannot read YAML, so this is drift DETECTION rather than
derivation: CI fails when the two disagree, and the failure prints the
exact value main.bicep should carry.

Usage:
    python3 scripts/check_allowlist_sync.py          # check, exit 1 on drift
    python3 scripts/check_allowlist_sync.py --print  # print the value only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = REPO_ROOT / "functions" / "_shared" / "scan-profiles.yaml"
BICEP_PATH = REPO_ROOT / "infra" / "main.bicep"

# Matches the env-var entry's value line, tolerating the trailing `//`
# comment main.bicep keeps on it.
_ALLOWLIST_RE = re.compile(
    r"name:\s*'MCP_WEB_ALLOWLIST'\s*\n\s*value:\s*'([^']*)'",
    re.MULTILINE,
)


def profile_hosts() -> set[str]:
    """Every hostname across every profile's urls. Profiles with no urls
    (eleven of the twelve today, deliberately) contribute nothing."""
    document = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))
    hosts: set[str] = set()
    for profile in document.get("profiles", []):
        for url in profile.get("urls") or []:
            host = (urlparse(url).hostname or "").lower()
            if host:
                hosts.add(host)
    return hosts


def bicep_hosts() -> set[str]:
    match = _ALLOWLIST_RE.search(BICEP_PATH.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(
            "FAIL: could not find the MCP_WEB_ALLOWLIST env-var entry in infra/main.bicep "
            "-- this checker's regex and that template have drifted apart"
        )
    return {host.strip().lower() for host in match.group(1).split(",") if host.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="print the allow-list value the profiles imply, and exit",
    )
    args = parser.parse_args()

    wanted = profile_hosts()
    expected_value = ",".join(sorted(wanted))

    if args.print_only:
        print(expected_value)
        return 0

    actual = bicep_hosts()
    if actual == wanted:
        print(f"allow-list in sync ({len(wanted)} host(s)): {expected_value}")
        return 0

    missing = sorted(wanted - actual)
    extra = sorted(actual - wanted)
    print("FAIL: MCP_WEB_ALLOWLIST and scan-profiles.yaml disagree.", file=sys.stderr)
    if missing:
        print(
            "  in a scan profile but NOT allow-listed (mcp-web would reject these, and "
            f"dispatch.py would skip them silently): {', '.join(missing)}",
            file=sys.stderr,
        )
    if extra:
        print(
            "  allow-listed but used by no scan profile (widens egress for nothing): "
            f"{', '.join(extra)}",
            file=sys.stderr,
        )
    print(f"\n  infra/main.bicep's MCP_WEB_ALLOWLIST value should be:\n    '{expected_value}'",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
