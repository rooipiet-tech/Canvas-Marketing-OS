#!/usr/bin/env python3
"""Build the versioned, signed, reproducible function-definition registry artefact.

Run standalone (from repo root):

    python services/registry/build_registry.py --out services/registry/dist
    python services/registry/build_registry.py --out dist1 --sign
    python services/registry/build_registry.py --resolve-tag v1.0.0

The artefact is a canonical-JSON manifest (`registry.json`) plus an optional
detached Ed25519 signature (`registry.json.sig`). Per C7 this is deliberately
*not* an OCI image: the goal's literal requirements are signed, versioned,
tag-addressable and byte-identical-reproducible, all of which this shape
satisfies without a container registry that does not exist yet.

Reproducibility rules, which the code holds to strictly:

* No timestamps, no mtimes, no build host, no absolute paths ever enter the
  manifest. Every path recorded is repo-relative with forward slashes.
* Packages, files within a package, and JSON keys are all sorted.
* File content is hashed with CRLF normalised to LF, so a Windows checkout
  and an ubuntu-latest CI checkout of the same commit produce the same
  hashes.
* Ed25519 signatures are deterministic (RFC 8032), so signing twice yields
  the same `.sig` bytes rather than breaking reproducibility.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import (
    FUNCTIONS_DIR,
    REGISTRY_DIR,
    REPO_ROOT,
    canonical_json,
    discover_function_packages,
    eval_task_files,
    fail,
    iter_package_files,
    load_yaml,
    normalise_text_bytes,
    rel_or_str,
    rel_posix,
    sha256_bytes,
)
from signing import resolve_private_key_path, sign_bytes

REGISTRY_SCHEMA_VERSION = 2
DEFAULT_TAG = "v1.0.0"
DEFAULT_OUT_DIR = REGISTRY_DIR / "dist"

REGISTRY_FILENAME = "registry.json"
SIGNATURE_FILENAME = "registry.json.sig"

# Tags this build knows how to resolve. `latest` is an alias for the current
# manifest tag; both resolve to the same bundled function set.
KNOWN_TAGS = (DEFAULT_TAG, "latest")


def compute_content_sha256(package_dir: Path) -> tuple[str, int]:
    """Content hash for one package, plus the number of files covered.

    The hash covers every committed file in the package: the path (so a
    rename is a change) and the LF-normalised content (so a checkout's line
    endings are not).
    """
    lines: list[str] = []
    files = iter_package_files(package_dir)
    for file_path in files:
        rel = file_path.relative_to(package_dir).as_posix()
        digest = sha256_bytes(normalise_text_bytes(file_path))
        lines.append(f"{digest}  {rel}")
    return sha256_bytes("\n".join(lines).encode("utf-8")), len(files)


def tool_names(package_dir: Path) -> list[str]:
    document = load_yaml(package_dir / "tools.yaml") or {}
    return sorted(str(tool.get("name")) for tool in document.get("tools", []) if tool.get("name"))


def file_hashes(package_dir: Path) -> dict[str, str]:
    """Per-file SHA-256 (LF-normalised, same convention as
    compute_content_sha256 above), keyed by the file's path relative to the
    package directory.

    Added alongside the package-level content_sha256 for TD-09: a consumer
    that stages only a subset of a package's files at runtime (the
    orchestrator image ships prompt.md/schema.json only, per RISK-002's
    minimal-image intent — see services/orchestrator/Dockerfile) needs a
    hash scoped to the ONE file it actually has on disk, not a bundle hash
    covering files it never staged and cannot recompute.
    """
    return {
        file_path.relative_to(package_dir).as_posix(): sha256_bytes(
            normalise_text_bytes(file_path)
        )
        for file_path in iter_package_files(package_dir)
    }


def discover_scaffold_packages(functions_dir: Path, covered_ids: set[str]) -> list[Path]:
    """Directories under functions/ that carry a prompt.md but not the full
    package shape discover_function_packages() requires -- "scaffold"
    packages, in dispatch.py's own vocabulary (e.g. compose_options_handler's
    docstring: "Fn 124 ... is still status: scaffold (no schema.json/
    tools.yaml)"). Several of dispatch.py's real DISPATCH_TABLE handlers
    (Appendix D's options/legal/incident/expertise functions among them)
    read exactly this prompt.md via _read_prompt() at runtime today.

    TD-09: orchestrator.manifest.resolve_prompt() verifies every prompt.md
    dispatch.py reads against a hash the signed manifest recorded -- so a
    scaffold function's prompt needs a recorded hash too, or the fix would
    make a function that runs today refuse to run, rather than close the
    "nothing verifies this" gap the register names.

    Deliberately kept separate from discover_function_packages() rather
    than folded into it: that function's OTHER callers (eval_harness.py,
    lint_rubrics.py, check_model_routing.py, validate_package.py) all
    assume the full shape (evals/, tools.yaml) it guarantees, and would
    break the moment a shapeless directory reached them. This is an
    additive, orchestrator-consumption-only view alongside it.
    """
    if not functions_dir.is_dir():
        return []
    found = []
    for child in sorted(functions_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name in covered_ids or child.name.startswith("_"):
            continue
        if (child / "prompt.md").is_file():
            found.append(child)
    return found


def build_manifest(tag: str, functions_dir: Path) -> dict:
    packages = discover_function_packages(functions_dir)
    scaffold_dirs = discover_scaffold_packages(functions_dir, {p.name for p in packages})
    if not packages and not scaffold_dirs:
        fail(f"no function-definition packages found under {rel_or_str(functions_dir)}")

    entries = []
    for package_dir in packages:
        content_sha256, file_count = compute_content_sha256(package_dir)
        entries.append(
            {
                "id": package_dir.name,
                "path": rel_posix(package_dir, REPO_ROOT),
                "content_sha256": content_sha256,
                "file_count": file_count,
                "eval_task_count": len(eval_task_files(package_dir)),
                "tools": tool_names(package_dir),
                "files": file_hashes(package_dir),
            }
        )

    entries.sort(key=lambda entry: entry["id"])
    bundle_sha256 = sha256_bytes(
        "\n".join(f"{entry['content_sha256']}  {entry['id']}" for entry in entries).encode("utf-8")
    )

    scaffold_entries = [
        {
            "id": package_dir.name,
            "path": rel_posix(package_dir, REPO_ROOT),
            "files": file_hashes(package_dir),
        }
        for package_dir in scaffold_dirs
    ]
    scaffold_entries.sort(key=lambda entry: entry["id"])

    return {
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "tag": tag,
        "known_tags": sorted(KNOWN_TAGS),
        "signature_algorithm": "Ed25519",
        "bundle_sha256": bundle_sha256,
        "function_count": len(entries),
        "functions": entries,
        "scaffold_function_count": len(scaffold_entries),
        "scaffold_functions": scaffold_entries,
    }


def write_artefact(manifest: dict, out_dir: Path, sign: bool) -> tuple[Path, Path | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    registry_path = out_dir / REGISTRY_FILENAME
    payload = canonical_json(manifest).encode("utf-8")
    registry_path.write_bytes(payload)

    signature_path: Path | None = None
    if sign:
        key_path = resolve_private_key_path()
        signature = sign_bytes(payload, key_path)
        signature_path = out_dir / SIGNATURE_FILENAME
        signature_path.write_text(signature + "\n", encoding="utf-8")
    return registry_path, signature_path


def resolve_tag(tag: str, functions_dir: Path) -> None:
    """Print the function IDs bundled under `tag`, or fail for an unknown tag."""
    if tag not in KNOWN_TAGS:
        fail(
            f"unknown tag {tag!r} — known tags are: {', '.join(sorted(KNOWN_TAGS))}"
        )
    manifest = build_manifest(DEFAULT_TAG, functions_dir)
    for entry in manifest["functions"]:
        print(entry["id"])
    print(f"resolved tag {tag} -> {manifest['function_count']} function(s)")
    print("PASS")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the signed, reproducible function-definition registry artefact."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"output directory (default {rel_or_str(DEFAULT_OUT_DIR)})",
    )
    parser.add_argument("--tag", default=DEFAULT_TAG, help=f"artefact tag (default {DEFAULT_TAG})")
    parser.add_argument(
        "--sign",
        action="store_true",
        help="write a detached Ed25519 signature alongside registry.json",
    )
    parser.add_argument(
        "--resolve-tag",
        metavar="TAG",
        help="print the function IDs bundled under TAG and exit; non-zero for an unknown tag",
    )
    parser.add_argument(
        "--functions-dir",
        type=Path,
        default=FUNCTIONS_DIR,
        help="directory to scan for function-definition packages",
    )
    args = parser.parse_args()

    if args.resolve_tag is not None:
        resolve_tag(args.resolve_tag, args.functions_dir)
        return

    if args.tag not in KNOWN_TAGS:
        fail(f"refusing to build unknown tag {args.tag!r} — known tags: {', '.join(KNOWN_TAGS)}")

    manifest = build_manifest(args.tag, args.functions_dir)
    registry_path, signature_path = write_artefact(manifest, args.out, args.sign)

    for entry in manifest["functions"]:
        print(f"{entry['content_sha256']}  {entry['id']}")
    print(f"bundle_sha256 {manifest['bundle_sha256']}")
    print(f"wrote {registry_path}")
    if signature_path is not None:
        print(f"wrote {signature_path}")
    else:
        print("unsigned build (pass --sign to write a detached signature)", file=sys.stderr)
    print("PASS")


if __name__ == "__main__":
    main()
