#!/usr/bin/env python3
"""Fail when a scan-profile URL's hostname could plausibly be a client's own domain.

Run standalone (from repo root):

    python scripts/check_scan_profiles_no_client_domains.py

WHY THIS EXISTS (PR 5a, v4 bootstrap). functions/_shared/scan-profiles.yaml
now carries source URLs for all twelve profiles, nine of them landed by an
agent-researched bootstrap in one pass (source-candidates.bootstrap.yaml).
A scan profile fetching a client's own site for marketing intelligence
would be the kind of thing Fn 129's allowlist rule (v3 §11.4) hard-excludes
once it exists -- this check exists so that guarantee holds *before* Fn 129
is wired in (Appendix D PR 5c), not only after.

THE LIMITATION, STATED RATHER THAN HIDDEN. docs/permission-register.yaml
records client NAMES and aliases, never a domain, URL or website field --
confirmed by grep, zero hits for domain:/website:/url: as a key anywhere in
it (the same gap policies/allowlist-deny.yaml's own header comment already
documents). So this check cannot do a domain-to-domain comparison; it does
the best mechanically achievable thing instead: a case-insensitive
substring match of each client name/alias against every scan-profile
hostname, on the theory that a client's own domain very likely contains
some form of the client's name (imperial.com, imperiallogistics.co.za,
etc.). This will miss a client domain that shares no substring with the
registered name, and could in principle false-positive on an unrelated
domain that happens to contain a short client name as a substring -- both
limitations are inherent to the input data, not this script, and are why
this stays a build-time guard rather than the only control: Fn 129's
future allowlist rule and hard exclusions (v3 §11.4, Appendix D PR 5c)
are the real, structural answer once they land.

Exits 0 and prints PASS on success (no hostname contains a registered
client name/alias). Exits 1 with `FAIL: <reason>` listing every match
otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_PROFILES_PATH = REPO_ROOT / "functions" / "_shared" / "scan-profiles.yaml"
PERMISSION_REGISTER_PATH = REPO_ROOT / "docs" / "permission-register.yaml"

# Below this length a client name/alias is too generic to substring-match
# safely (e.g. a two-letter name would match almost any domain) -- skip it
# rather than produce a check nobody can trust.
MIN_NAME_LENGTH = 4


def _client_names() -> list[str]:
    doc = yaml.safe_load(PERMISSION_REGISTER_PATH.read_text(encoding="utf-8"))
    names: list[str] = []
    for client in doc.get("clients", []):
        candidate = client.get("name")
        if candidate:
            names.append(candidate)
        for alias in client.get("aliases") or []:
            names.append(alias)
    return [n for n in names if len(n.replace(" ", "")) >= MIN_NAME_LENGTH]


def _scan_profile_hostnames() -> dict[str, list[str]]:
    doc = yaml.safe_load(SCAN_PROFILES_PATH.read_text(encoding="utf-8"))
    by_profile: dict[str, list[str]] = {}
    for profile in doc.get("profiles", []):
        hosts = []
        for url in profile.get("urls") or []:
            host = urlparse(url).netloc
            if host:
                hosts.append(host)
        by_profile[profile["profile_id"]] = hosts
    return by_profile


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def main() -> None:
    names = _client_names()
    profiles = _scan_profile_hostnames()

    findings: list[str] = []
    for profile_id, hosts in profiles.items():
        for host in hosts:
            normalised_host = _normalise(host)
            for name in names:
                normalised_name = _normalise(name)
                if normalised_name and normalised_name in normalised_host:
                    findings.append(
                        f"{profile_id}: hostname {host!r} contains registered "
                        f"client name/alias {name!r}"
                    )

    if findings:
        print("FAIL: possible client domain in a scan profile:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        sys.exit(1)

    print(
        f"PASS - {sum(len(h) for h in profiles.values())} scan-profile hostname(s) "
        f"checked against {len(names)} registered client name(s)/alias(es); "
        "name-substring match only, see this script's own docstring for the limitation"
    )


if __name__ == "__main__":
    main()
