#!/usr/bin/env python3
"""Fail when mcp-web's egress allow-list and the scan profiles disagree.

WHY THIS EXISTS (F-ALLOWLIST-DRIFT). The hosts a scan may fetch are
written down twice, in two places that change through different routes:

  * functions/_shared/scan-profiles.yaml -- edited by whoever adds a
    market to watch; no deploy needed.
  * MCP_WEB_ALLOWLIST in infra/main.bicep -- edited by whoever deploys;
    mcp-web raises AllowlistViolation for any host not in it.

The same split exists for the source-promotion sandbox:

  * functions/_shared/source-candidates.yaml -- proposed sources.
  * MCP_WEB_PROBE_ALLOWLIST in infra/main.bicep -- what probe_url may
    reach. A candidate missing from it cannot be probed, so it silently
    scores zero and looks unreachable rather than unconfigured.

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
CANDIDATES_PATH = REPO_ROOT / "functions" / "_shared" / "source-candidates.yaml"
BICEP_PATH = REPO_ROOT / "infra" / "main.bicep"

# Matches the env-var entry's value line, tolerating the trailing `//`
# comment main.bicep keeps on it.
def _allowlist_re(env_var: str) -> re.Pattern[str]:
    return re.compile(
        r"name:\s*'" + env_var + r"'\s*\n\s*value:\s*'([^']*)'",
        re.MULTILINE,
    )


def profile_hosts() -> set[str]:
    """Every hostname across every profile's urls. Profiles with no urls
    contribute nothing.

    Deliberately not stating how many of the twelve those are: that count
    moves with every promotion, and repeating it here is how it goes
    stale. scan-profiles.yaml's own header carries it, once."""
    document = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))
    hosts: set[str] = set()
    for profile in document.get("profiles", []):
        for url in profile.get("urls") or []:
            host = (urlparse(url).hostname or "").lower()
            if host:
                hosts.add(host)
    return hosts


def candidate_hosts() -> set[str]:
    """Every hostname across the source-promotion candidate register.

    These belong on the PROBE allow-list, never the scan one: a candidate
    may be probed (metadata only) and may not be fetched by a scan until a
    human approves its promotion."""
    document = yaml.safe_load(CANDIDATES_PATH.read_text(encoding="utf-8"))
    hosts: set[str] = set()
    for candidate in document.get("candidates", []):
        host = (urlparse(candidate.get("url", "")).hostname or "").lower()
        if host:
            hosts.add(host)
    return hosts


def bicep_hosts(env_var: str) -> set[str]:
    match = _allowlist_re(env_var).search(BICEP_PATH.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(
            f"FAIL: could not find the {env_var} env-var entry in infra/main.bicep "
            "-- this checker's regex and that template have drifted apart"
        )
    return {host.strip().lower() for host in match.group(1).split(",") if host.strip()}


def _report(env_var: str, wanted: set[str], actual: set[str], *, purpose: str) -> bool:
    if actual == wanted:
        print(f"{env_var} in sync ({len(wanted)} host(s))")
        return True
    missing = sorted(wanted - actual)
    extra = sorted(actual - wanted)
    print(f"FAIL: {env_var} and {purpose} disagree.", file=sys.stderr)
    if missing:
        print(f"  named in {purpose} but NOT allow-listed: {', '.join(missing)}", file=sys.stderr)
    if extra:
        print(f"  allow-listed but named nowhere in {purpose}: {', '.join(extra)}", file=sys.stderr)
    print(
        f"\n  infra/main.bicep's {env_var} value should be:\n    '{','.join(sorted(wanted))}'",
        file=sys.stderr,
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="print both allow-list values the YAML implies, and exit",
    )
    args = parser.parse_args()

    scan_hosts = profile_hosts()
    probe_hosts = candidate_hosts()

    if args.print_only:
        print(f"MCP_WEB_ALLOWLIST={','.join(sorted(scan_hosts))}")
        print(f"MCP_WEB_PROBE_ALLOWLIST={','.join(sorted(probe_hosts))}")
        return 0

    ok = _report(
        "MCP_WEB_ALLOWLIST",
        scan_hosts,
        bicep_hosts("MCP_WEB_ALLOWLIST"),
        purpose="scan-profiles.yaml",
    )
    ok = _report(
        "MCP_WEB_PROBE_ALLOWLIST",
        probe_hosts,
        bicep_hosts("MCP_WEB_PROBE_ALLOWLIST"),
        purpose="source-candidates.yaml",
    ) and ok

    # A candidate must never already hold scan-path egress: that is the
    # promotion decision, and it is a person's to make.
    unapproved = probe_hosts & scan_hosts
    if unapproved:
        print(
            "\nNOTE: also on the scan allow-list, so already promoted: "
            f"{', '.join(sorted(unapproved))}",
            file=sys.stderr,
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
