#!/usr/bin/env python3
"""Deterministic brand-safety checks over marketing copy.

Run standalone (from repo root):

    python services/registry/safety_suite.py services/registry/fixtures/safety/compliant.md
    python services/registry/safety_suite.py --dir services/registry/fixtures/safety/clean

Deterministic by design (regex + lexicon, no model call): the two explicitly
seeded cases — a bit.ly link and a US spelling — must be caught the same way
every single run, and a model-judged rule cannot promise that.

Violation codes:

  link-shortener      a shortened link, which hides its destination and
                      breaks UTM attribution
  sa-english-spelling a US variant of a word positioning.md attests in South
                      African English (productise/productised, behaviour)
  missing-cta         no call to action anywhere in the asset
  url-utm             a URL that is not a full https://www.canvasintelligence.com/...
                      link carrying utm_source, utm_medium and utm_campaign
  unsupported-claim   a superlative with no client, number or artefact
                      attached, breaking positioning.md's "proof over
                      platitude" tone rule (line 92)

Findings are printed one per line as `<file>:<line>: <code>: <detail>`.
Exits 0 and prints PASS when clean; exits 1 with `FAIL: <reason>` otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import fail, rel_or_str

CODE_LINK_SHORTENER = "link-shortener"
CODE_SA_ENGLISH = "sa-english-spelling"
CODE_MISSING_CTA = "missing-cta"
CODE_URL_UTM = "url-utm"
CODE_UNSUPPORTED_CLAIM = "unsupported-claim"

LINK_SHORTENERS = (
    "bit.ly",
    "lnkd.in",
    "tinyurl.com",
    "t.co/",
    "ow.ly",
    "buff.ly",
    "goo.gl",
    "rebrand.ly",
    "cutt.ly",
)

# US spelling -> required South African English form. Grounded in
# positioning.md's own attested spellings (productised, behaviour) and
# extended to the same -ise/-our/-re word families.
US_TO_SA_SPELLING = {
    "productize": "productise",
    "productizes": "productises",
    "productized": "productised",
    "productizing": "productising",
    "productization": "productisation",
    "behavior": "behaviour",
    "behaviors": "behaviours",
    "behavioral": "behavioural",
    "organization": "organisation",
    "organizations": "organisations",
    "organize": "organise",
    "organized": "organised",
    "optimize": "optimise",
    "optimized": "optimised",
    "optimization": "optimisation",
    "analyze": "analyse",
    "analyzed": "analysed",
    "analyzing": "analysing",
    "recognize": "recognise",
    "recognized": "recognised",
    "center": "centre",
    "centers": "centres",
    "centered": "centred",
    "catalog": "catalogue",
    "defense": "defence",
    "licenses": "licences",
}

CANVAS_URL_PREFIX = "https://www.canvasintelligence.com/"
REQUIRED_UTM_PARAMS = ("utm_source=", "utm_medium=", "utm_campaign=")

URL_RE = re.compile(r"https?://[^\s\"'<>)\]}]+")

CTA_MARKERS = (
    "book a",
    "book your",
    "get in touch",
    "talk to us",
    "speak to",
    "request a",
    "contact us",
    "see how",
    "read the",
    "read our",
    "download the",
    "sign up",
    "canvasintelligence.com",
)

# Superlatives that assert rather than prove.
CLAIM_TERMS = (
    "leading",
    "the leader",
    "world-class",
    "best-in-class",
    "best in class",
    "the best",
    "the only",
    "unmatched",
    "unrivalled",
    "unrivaled",
    "industry-leading",
    "market-leading",
    "revolutionary",
    "game-changing",
    "cutting-edge",
    "state-of-the-art",
)

# Evidence that discharges a claim: a number, or a named artefact.
NUMBER_RE = re.compile(r"\d")
ARTEFACT_TERMS = (
    "case study",
    "reference",
    "report pack",
    "audit",
    "audited",
    "architecture",
    "deck",
    "benchmark",
    "certification",
    "partner status",
    "sla",
)

# Client names that count as attached proof. Naming one is separately gated by
# docs/permission-register.yaml (function 02's job) — this suite only asks
# whether a claim carries evidence, not whether the name may be published.
CLIENT_TERMS = ("imperial", "rotork", "weir", "arcelormittal", "sgb cape", "delta")

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

TEXT_SUFFIXES = {".md", ".txt"}


class Finding:
    def __init__(self, path: Path, line: int, code: str, detail: str) -> None:
        self.path = path
        self.line = line
        self.code = code
        self.detail = detail

    def render(self) -> str:
        return f"{rel_or_str(self.path)}:{self.line}: {self.code}: {self.detail}"


def check_link_shorteners(path: Path, lines: list[str]) -> list[Finding]:
    findings = []
    for number, line in enumerate(lines, start=1):
        lowered = line.lower()
        for shortener in LINK_SHORTENERS:
            if shortener in lowered:
                findings.append(
                    Finding(
                        path,
                        number,
                        CODE_LINK_SHORTENER,
                        f"shortened link {shortener!r} hides its destination and breaks "
                        "UTM attribution",
                    )
                )
    return findings


def check_sa_english(path: Path, lines: list[str]) -> list[Finding]:
    findings = []
    for number, line in enumerate(lines, start=1):
        lowered = line.lower()
        for us_word, sa_word in US_TO_SA_SPELLING.items():
            if re.search(rf"\b{re.escape(us_word)}\b", lowered):
                findings.append(
                    Finding(
                        path,
                        number,
                        CODE_SA_ENGLISH,
                        f"US spelling {us_word!r} - South African English requires {sa_word!r}",
                    )
                )
    return findings


def check_cta(path: Path, lines: list[str]) -> list[Finding]:
    lowered = "\n".join(lines).lower()
    if any(marker in lowered for marker in CTA_MARKERS):
        return []
    return [
        Finding(
            path,
            1,
            CODE_MISSING_CTA,
            "no call to action found - every publishable asset carries exactly one",
        )
    ]


def check_urls(path: Path, lines: list[str]) -> list[Finding]:
    findings = []
    for number, line in enumerate(lines, start=1):
        for url in URL_RE.findall(line):
            if any(shortener in url.lower() for shortener in LINK_SHORTENERS):
                # Already reported as link-shortener; do not double-count.
                continue
            if not url.startswith(CANVAS_URL_PREFIX):
                findings.append(
                    Finding(
                        path,
                        number,
                        CODE_URL_UTM,
                        f"{url!r} is not a full {CANVAS_URL_PREFIX}... link",
                    )
                )
                continue
            missing = [param.rstrip("=") for param in REQUIRED_UTM_PARAMS if param not in url]
            if missing:
                findings.append(
                    Finding(
                        path,
                        number,
                        CODE_URL_UTM,
                        f"{url!r} is missing UTM parameter(s): {', '.join(missing)}",
                    )
                )
    return findings


def check_unsupported_claims(path: Path, lines: list[str]) -> list[Finding]:
    findings = []
    text = "\n".join(lines)
    offset = 0
    for sentence in SENTENCE_SPLIT_RE.split(text):
        stripped = sentence.strip()
        if not stripped:
            continue
        index = text.find(stripped, offset)
        if index >= 0:
            offset = index + len(stripped)
            line_number = text.count("\n", 0, index) + 1
        else:
            line_number = 1

        lowered = stripped.lower()
        matched = [term for term in CLAIM_TERMS if term in lowered]
        if not matched:
            continue
        if NUMBER_RE.search(stripped):
            continue
        if any(term in lowered for term in ARTEFACT_TERMS):
            continue
        if any(term in lowered for term in CLIENT_TERMS):
            continue
        findings.append(
            Finding(
                path,
                line_number,
                CODE_UNSUPPORTED_CLAIM,
                f"claim {matched[0]!r} carries no client, number or artefact "
                f"(positioning.md line 92): {stripped[:90]!r}",
            )
        )
    return findings


def scan_file(path: Path) -> list[Finding]:
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[Finding] = []
    findings.extend(check_link_shorteners(path, lines))
    findings.extend(check_sa_english(path, lines))
    findings.extend(check_cta(path, lines))
    findings.extend(check_urls(path, lines))
    findings.extend(check_unsupported_claims(path, lines))
    return findings


def collect_targets(paths: list[Path], directories: list[Path]) -> list[Path]:
    targets: list[Path] = []
    for path in paths:
        if not path.is_file():
            fail(f"{rel_or_str(path)} is not a file")
        targets.append(path)
    for directory in directories:
        if not directory.is_dir():
            fail(f"{rel_or_str(directory)} is not a directory")
        targets.extend(
            sorted(
                candidate
                for candidate in directory.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in TEXT_SUFFIXES
            )
        )
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic brand-safety checks.")
    parser.add_argument("files", nargs="*", type=Path, help="files to scan")
    parser.add_argument(
        "--dir",
        dest="directories",
        action="append",
        type=Path,
        default=[],
        help="scan every .md/.txt file under this directory (repeatable)",
    )
    args = parser.parse_args()

    targets = collect_targets(args.files, args.directories)
    if not targets:
        fail("no files to scan - pass file paths and/or --dir")

    findings: list[Finding] = []
    for target in targets:
        findings.extend(scan_file(target))

    for finding in sorted(findings, key=lambda f: (str(f.path), f.line, f.code)):
        print(finding.render(), file=sys.stderr)

    if findings:
        codes = sorted({finding.code for finding in findings})
        fail(
            f"{len(findings)} brand-safety violation(s) across {len(targets)} file(s); "
            f"codes: {', '.join(codes)}"
        )

    print(f"scanned {len(targets)} file(s), 0 violations")
    print("PASS")


if __name__ == "__main__":
    main()
