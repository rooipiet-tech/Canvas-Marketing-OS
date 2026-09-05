#!/usr/bin/env python3
"""Verify the governance Container Apps source bundles reconstruct cleanly.

Run standalone (from repo root):

    python scripts/verify_governance_bundle_reconstruction.py
    python scripts/verify_governance_bundle_reconstruction.py --self-test

WHY THIS EXISTS
---------------
Gatekeeper and Publisher ship as base64'd JSON source bundles that are
unpacked by a shell script at container start (no Dockerfile, no ACR —
mirroring the repo's existing stock-image + inline-command pattern). That
bootstrap path is only exercised for real at deploy time, inside a VNet,
where a typo costs a full deploy cycle to discover. This script runs the
IDENTICAL path locally:

  1. read services/<svc>/BUNDLE_MANIFEST.txt  — the single source of truth
     for the file list. NOTHING is hardcoded here; if the manifest and
     infra/main.bicep's loadTextContent calls ever disagree, step 2 fails.
  2. cross-check every manifest line against exactly one matching
     loadTextContent call in infra/main.bicep (and no extras).
  3. build the bundle JSON exactly as main.bicep's `string(...)` would and
     base64 it exactly as the app module's `base64(...)` would.
  4. subprocess-execute the SAME infra/modules/governance/<svc>-bundle-
     unpack.sh — not a reimplementation of it — with APP_DIR pointed at a
     temp directory, PIP_INSTALL_CMD neutralised and LAUNCH_CMD replaced
     by an import check.
  5. confirm every app module in the reconstructed tree imports.

--self-test additionally proves the check can FAIL: it re-runs with a
manifest line removed and with the unpack script corrupted, and requires
both to be detected.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_BICEP = REPO_ROOT / "infra" / "main.bicep"
GOVERNANCE_DIR = REPO_ROOT / "infra" / "modules" / "governance"

# (service directory name, unpack script name, ASGI modules to import)
SERVICES = (
    ("gatekeeper", "gatekeeper-bundle-unpack.sh", ("main", "approval_main")),
    ("publisher", "publisher-bundle-unpack.sh", ("main",)),
)


class VerificationFailure(Exception):
    pass


# Executed by the unpack script's own exec/launch site, in place of the
# uvicorn server, with the working directory already set to APP_DIR.
_IMPORT_CHECK_SOURCE = """\
import importlib
import os
import sys

sys.path.insert(0, os.getcwd())
app_module = os.environ["APP_MODULE"]
module_name, _, attribute = app_module.partition(":")
module = importlib.import_module(module_name)
if attribute and not hasattr(module, attribute):
    raise SystemExit(f"{module_name} has no ASGI attribute {attribute!r}")
print(f"import-ok {app_module}")
"""


def log(message: str) -> None:
    print(message, flush=True)


def read_manifest(service: str) -> list[str]:
    """The ONLY place the file list comes from. Never hardcoded."""
    manifest_path = REPO_ROOT / "services" / service / "BUNDLE_MANIFEST.txt"
    if not manifest_path.exists():
        raise VerificationFailure(f"missing manifest: {manifest_path}")

    paths: list[str] = []
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        paths.append(line)
    if not paths:
        raise VerificationFailure(f"{manifest_path} lists no files")
    return paths


def check_manifest_matches_main_bicep(service: str, manifest: list[str]) -> None:
    """Every manifest line -> exactly one loadTextContent call, no extras."""
    main_source = MAIN_BICEP.read_text(encoding="utf-8")
    referenced = re.findall(
        rf"loadTextContent\('\.\./services/{re.escape(service)}/([^']+)'\)", main_source
    )

    problems: list[str] = []
    for path in manifest:
        count = referenced.count(path)
        if count != 1:
            problems.append(
                f"manifest line {path!r} has {count} loadTextContent call(s), expected 1"
            )
    for path in sorted(set(referenced) - set(manifest)):
        problems.append(f"main.bicep loads {path!r}, which is not in the manifest")

    if problems:
        raise VerificationFailure(
            f"{service}: manifest and infra/main.bicep disagree:\n  - " + "\n  - ".join(problems)
        )
    log(f"  manifest <-> main.bicep parity OK ({len(manifest)} files)")


def build_bundle(service: str, manifest: list[str]) -> dict[str, str]:
    """Reconstruct exactly what main.bicep's `string(<bundle object>)` holds."""
    service_root = REPO_ROOT / "services" / service
    bundle: dict[str, str] = {}
    for relative_path in manifest:
        source = service_root / relative_path
        if not source.exists():
            raise VerificationFailure(
                f"{service}: manifest lists {relative_path!r}, which does not exist on disk"
            )
        bundle[relative_path] = source.read_text(encoding="utf-8")
    return bundle


# Must match every <svc>-app.bicep's bundleChunkSize exactly — this is
# the same 120000-byte chunking those templates apply to stay under
# Linux's 131072-byte (128 KiB) MAX_ARG_STRLEN per single env var value,
# the exact ceiling a single-BUNDLE_B64 gatekeeper bundle crossed at 27
# files (see gatekeeper-bundle-unpack.sh's header). Chunking here too
# keeps this LOCAL check honest about what the real deploy sends, and
# stops the check itself from hitting the same OSError.
BUNDLE_CHUNK_SIZE = 120000


def _bundle_b64_chunks(bundle_b64: str) -> dict[str, str]:
    chunks = {
        f"BUNDLE_B64_{i}": bundle_b64[i * BUNDLE_CHUNK_SIZE : (i + 1) * BUNDLE_CHUNK_SIZE]
        for i in range(4)
    }
    return {name: value for name, value in chunks.items() if value}


def run_unpack_script(
    script_path: Path, bundle: dict[str, str], app_dir: Path, app_module: str
) -> str:
    """Execute the REAL unpack script — never a reimplementation of it."""
    bundle_b64 = base64.b64encode(json.dumps(bundle).encode("utf-8")).decode("ascii")
    if len(bundle_b64) > 4 * BUNDLE_CHUNK_SIZE:
        raise VerificationFailure(
            f"bundle base64 is {len(bundle_b64)} bytes, which needs more than 4 chunks of "
            f"{BUNDLE_CHUNK_SIZE} bytes — raise the chunk count here AND in every "
            f"<svc>-app.bicep's bundleChunkCandidates in the same change"
        )

    shell = shutil.which("sh") or shutil.which("bash")
    if shell is None:
        raise VerificationFailure("no POSIX shell (sh/bash) available to run the unpack script")

    # LAUNCH_CMD is word-split by the shell, so it must not contain quoted
    # arguments. The import check therefore lives in its own file and
    # reads APP_MODULE from the environment the script already sets.
    launcher = app_dir.parent / "import_check.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(_IMPORT_CHECK_SOURCE, encoding="utf-8")

    for candidate in (_python_executable(), launcher.as_posix(), str(app_dir)):
        if " " in candidate:
            raise VerificationFailure(
                f"path contains a space, which the shell's word-splitting of "
                f"LAUNCH_CMD/APP_DIR cannot survive: {candidate}"
            )

    # Inherit the parent environment (Windows resolves the per-user
    # site-packages directory from APPDATA, so a minimal env would hide
    # every installed dependency and make the import check meaningless).
    env = {
        **os.environ,
        "PATH": _path_env(),
        **_bundle_b64_chunks(bundle_b64),
        "APP_MODULE": app_module,
        "APP_DIR": str(app_dir),
        "BUNDLE_FILE": str(app_dir.parent / f"{app_dir.name}-bundle.json"),
        # Dependencies are already present in this environment; the point
        # of the local run is the unpack + import path, not a pip install.
        "PIP_INSTALL_CMD": "true",
        # Replaces the uvicorn launch with an import check, so the script's
        # single exec site is still what starts the app entry point.
        "LAUNCH_CMD": f"{_python_executable()} {launcher.as_posix()}",
    }

    completed = subprocess.run(
        [shell, str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    if completed.returncode != 0:
        raise VerificationFailure(
            f"{script_path.name} exited {completed.returncode}\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    return completed.stdout


def _python_executable() -> str:
    return Path(sys.executable).as_posix()


def _path_env() -> str:
    parts = [str(Path(sys.executable).parent), os.environ.get("PATH", "")]
    return os.pathsep.join(part for part in parts if part)


def verify_service(service: str, script_name: str, app_modules: tuple[str, ...]) -> None:
    log(f"[{service}]")
    manifest = read_manifest(service)
    check_manifest_matches_main_bicep(service, manifest)
    bundle = build_bundle(service, manifest)

    script_path = GOVERNANCE_DIR / script_name
    if not script_path.exists():
        raise VerificationFailure(f"missing unpack script: {script_path}")

    for app_module in app_modules:
        with tempfile.TemporaryDirectory(prefix=f"cmos-{service}-") as temp_root:
            app_dir = Path(temp_root) / "app-root"
            output = run_unpack_script(script_path, bundle, app_dir, f"{app_module}:app")

            if f"wrote {len(manifest)} file(s)" not in output:
                raise VerificationFailure(
                    f"{service}: unpack script did not report writing "
                    f"{len(manifest)} files:\n{output}"
                )
            if f"import-ok {app_module}:app" not in output:
                raise VerificationFailure(
                    f"{service}: reconstructed bundle failed to import {app_module}:\n{output}"
                )

            for relative_path in manifest:
                written = app_dir / relative_path
                if not written.exists():
                    raise VerificationFailure(
                        f"{service}: {relative_path!r} missing from the reconstructed tree"
                    )
                if written.read_text(encoding="utf-8") != bundle[relative_path]:
                    raise VerificationFailure(
                        f"{service}: {relative_path!r} round-tripped with different content"
                    )

            log(f"  reconstructed + imported {app_module}:app ({len(manifest)} files verified)")


def verify_all() -> None:
    for service, script_name, app_modules in SERVICES:
        verify_service(service, script_name, app_modules)


# ---------------------------------------------------------------------
# Fault injection — proves this script can actually fail.
# ---------------------------------------------------------------------


def _expect_failure(label: str, action) -> None:
    try:
        action()
    except VerificationFailure as exc:
        first_line = str(exc).splitlines()[0]
        log(f"  detected as expected: {label} -> {first_line}")
        return
    raise VerificationFailure(
        f"FAULT INJECTION DID NOT FAIL: {label} was not detected — this "
        f"verification script would pass a broken bundle"
    )


def self_test() -> None:
    log("=== fault injection ===")

    service, script_name, app_modules = SERVICES[0]
    manifest = read_manifest(service)

    # (a) a manifest line that main.bicep does not load.
    def missing_manifest_line() -> None:
        check_manifest_matches_main_bicep(service, manifest + ["app/not_in_main_bicep.py"])

    _expect_failure("manifest line with no loadTextContent call", missing_manifest_line)

    # (b) a manifest line whose file does not exist on disk.
    def missing_file_on_disk() -> None:
        build_bundle(service, manifest + ["app/does_not_exist.py"])

    _expect_failure("manifest line missing from disk", missing_file_on_disk)

    # (c) a corrupted unpack script.
    def corrupted_unpack_script() -> None:
        original = (GOVERNANCE_DIR / script_name).read_text(encoding="utf-8")
        corrupted = original.replace("base64 -d", "base64 --this-flag-does-not-exist")
        if corrupted == original:
            raise VerificationFailure("could not corrupt the unpack script (pattern not found)")
        with tempfile.TemporaryDirectory(prefix="cmos-fault-") as temp_root:
            broken_script = Path(temp_root) / script_name
            broken_script.write_text(corrupted, encoding="utf-8")
            app_dir = Path(temp_root) / "app-root"
            run_unpack_script(
                broken_script,
                build_bundle(service, manifest),
                app_dir,
                f"{app_modules[0]}:app",
            )

    _expect_failure("corrupted unpack script line", corrupted_unpack_script)

    # (d) a bundle missing a file the app imports.
    def truncated_bundle() -> None:
        reduced = [path for path in manifest if path != "app/policy_loader.py"]
        with tempfile.TemporaryDirectory(prefix="cmos-fault-") as temp_root:
            app_dir = Path(temp_root) / "app-root"
            run_unpack_script(
                GOVERNANCE_DIR / script_name,
                build_bundle(service, reduced),
                app_dir,
                f"{app_modules[0]}:app",
            )

    _expect_failure("bundle missing a module the app imports", truncated_bundle)

    log("=== fault injection: all four faults detected ===")
    log("=== re-running the clean verification to prove the revert restores a pass ===")
    verify_all()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="additionally prove the checks fail on injected faults",
    )
    args = parser.parse_args()

    try:
        log("=== reconstructing governance bundles ===")
        verify_all()
        if args.self_test:
            self_test()
    except VerificationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
