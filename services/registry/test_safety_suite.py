#!/usr/bin/env python3
"""Paired assertions for safety_suite.py: seeded defects fire, clean copy does not.

Run standalone (from repo root):

    python services/registry/test_safety_suite.py

Each seeded fixture asserts the *exact* set of violation codes it should
produce, not merely "something failed" — a checker that fires the wrong code,
or fires three codes when one is correct, is not usable as a gate. The clean
fixtures assert zero findings, which is the half that catches false
positives.

Exits 0 with PASS, or 1 with `FAIL: <reason>`.
"""

from __future__ import annotations

from pathlib import Path

from common import REGISTRY_DIR, fail, rel_or_str
from safety_suite import (
    CODE_LINK_SHORTENER,
    CODE_MISSING_CTA,
    CODE_SA_ENGLISH,
    CODE_UNSUPPORTED_CLAIM,
    CODE_URL_UTM,
    scan_file,
)

FIXTURES = REGISTRY_DIR / "fixtures" / "safety"

EXPECTED: dict[str, set[str]] = {
    "violations/seeded-shortener-and-us-spelling.md": {CODE_LINK_SHORTENER, CODE_SA_ENGLISH},
    "violations/no-cta.md": {CODE_MISSING_CTA},
    "violations/non-utm-url.md": {CODE_URL_UTM},
    "violations/canvas-url-missing-utm.md": {CODE_URL_UTM},
    "violations/unsupported-claim.md": {CODE_UNSUPPORTED_CLAIM},
    "clean/control.md": set(),
    "clean/supported-claim.md": set(),
}


def main() -> None:
    checked = 0
    for relative, expected_codes in sorted(EXPECTED.items()):
        path = FIXTURES / Path(relative)
        if not path.is_file():
            fail(f"fixture missing: {rel_or_str(path)}")
        findings = scan_file(path)
        actual_codes = {finding.code for finding in findings}
        if actual_codes != expected_codes:
            rendered = "; ".join(finding.render() for finding in findings) or "<none>"
            fail(
                f"{rel_or_str(path)} produced codes {sorted(actual_codes)}, "
                f"expected exactly {sorted(expected_codes)} — findings: {rendered}"
            )
        checked += 1
        status = "clean" if not expected_codes else ", ".join(sorted(expected_codes))
        print(f"  {relative:<48} {status}")

    # The two explicitly seeded cases from the goal text, asserted by name.
    seeded = scan_file(FIXTURES / "violations" / "seeded-shortener-and-us-spelling.md")
    details = " ".join(finding.detail for finding in seeded)
    for token in ("bit.ly", "productized", "behavior"):
        if token not in details:
            fail(f"the seeded fixture's findings must name {token!r}; details were: {details}")

    print(f"\n{checked} fixture(s) matched their expected violation codes exactly")
    print("PASS")


if __name__ == "__main__":
    main()
