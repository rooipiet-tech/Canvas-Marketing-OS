#!/bin/sh
# Canvas Marketing OS - Gatekeeper container bootstrap.
#
# THE single source of truth for how the Gatekeeper source bundle is
# unpacked and the app is launched. This exact file is:
#
#   * loadTextContent()'d by infra/main.bicep and handed to
#     gatekeeper-app.bicep / gatekeeper-approval-app.bicep as the
#     container command - neither .bicep file reimplements any of it; and
#   * subprocess-executed verbatim by
#     scripts/verify_governance_bundle_reconstruction.py, so a bug here
#     fails locally instead of only at live deploy.
#
# Both Gatekeeper apps share this script. The ONLY difference between
# them is APP_MODULE (main:app for the internal API,
# approval_main:app for the Entra-ID-protected approval surface).
#
# Contract:
#   BUNDLE_B64_PART0..3   (required, at least one) up to 4 INDEPENDENT
#                     base64-encoded JSON objects, each its own disjoint
#                     {path: content} subset of the bundle -- never
#                     byte-offset slices of one combined blob. Split into
#                     separate parts for two independent reasons:
#                       (a) Linux's MAX_ARG_STRLEN caps any single
#                           execve() argument string at 128 KiB (131072
#                           bytes) regardless of the total argv+environ
#                           budget (getconf ARG_MAX, usually far larger) --
#                           gatekeeper's own bundle crossed that per-string
#                           ceiling at 27 files (142140 base64 bytes) with
#                           a single BUNDLE_B64.
#                       (b) ARM's own deployment engine separately caps
#                           any single "template language expression
#                           literal" at the same 131072-character ceiling,
#                           evaluated BEFORE any chunking of an
#                           already-assembled value runs -- gatekeeper's
#                           bundle crossed THIS ceiling too at 28 files
#                           (144344 base64 bytes), breaking every
#                           `az deployment group create` from PR #167
#                           onward even though (a)'s chunking was already
#                           in place, because that chunking sliced an
#                           ALREADY-too-large combined value rather than
#                           ever avoiding computing one.
#                     Each part is decoded and JSON-parsed independently,
#                     then the resulting dicts are merged (disjoint keys
#                     by construction -- see main.bicep's own comment
#                     above gatekeeperBundlePart0). Unused trailing parts
#                     are unset or empty, contributing nothing.
#   APP_MODULE        (required) ASGI target, e.g. main:app
#   APP_DIR           (optional) where to unpack, default /app
#   PIP_INSTALL_CMD   (optional) dependency install command
#   LAUNCH_CMD        (optional) process to exec after install
#
# The last three exist so the verification script can reconstruct and
# import the bundle without installing packages or binding a port. The
# defaults are what actually runs in Container Apps.

set -eu

ALL_PARTS="${BUNDLE_B64_PART0:-}${BUNDLE_B64_PART1:-}${BUNDLE_B64_PART2:-}${BUNDLE_B64_PART3:-}"
: "${ALL_PARTS:?at least one of BUNDLE_B64_PART0..BUNDLE_B64_PART3 must be set (base64-encoded bundle parts)}"
: "${APP_MODULE:?APP_MODULE must be set (e.g. main:app)}"

APP_DIR="${APP_DIR:-/app}"
BUNDLE_FILE="${BUNDLE_FILE:-/tmp/gatekeeper-bundle.json}"
PIP_INSTALL_CMD="${PIP_INSTALL_CMD:-pip install --no-cache-dir --disable-pip-version-check -r requirements.txt}"
LAUNCH_CMD="${LAUNCH_CMD:-uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000}"

mkdir -p "$APP_DIR"
i=0
for part in "${BUNDLE_B64_PART0:-}" "${BUNDLE_B64_PART1:-}" "${BUNDLE_B64_PART2:-}" "${BUNDLE_B64_PART3:-}"; do
  if [ -n "$part" ]; then
    printf '%s' "$part" | base64 -d > "${BUNDLE_FILE}.${i}"
  fi
  i=$((i + 1))
done

APP_DIR="$APP_DIR" BUNDLE_FILE="$BUNDLE_FILE" python - <<'UNPACK_PY'
import glob
import json
import os
import pathlib
import sys

app_dir = pathlib.Path(os.environ["APP_DIR"]).resolve()
bundle_file = pathlib.Path(os.environ["BUNDLE_FILE"])
part_paths = sorted(glob.glob(f"{bundle_file}.*"), key=lambda p: int(p.rsplit(".", 1)[1]))
if not part_paths:
    sys.exit("no BUNDLE_B64_PARTn decoded to a part file -- at least one part must be set")

bundle: dict = {}
for part_path in part_paths:
    part = json.loads(pathlib.Path(part_path).read_text(encoding="utf-8"))
    if not isinstance(part, dict):
        sys.exit(f"{part_path} must decode to a JSON object of {{path: content}}")
    overlap = set(bundle) & set(part)
    if overlap:
        sys.exit(f"bundle parts are not disjoint -- duplicate path(s): {sorted(overlap)}")
    bundle.update(part)

if not bundle:
    sys.exit("bundle JSON must be a non-empty object of {path: content}")

written = 0
for relative_path, content in sorted(bundle.items()):
    target = (app_dir / relative_path).resolve()
    if app_dir not in target.parents and target != app_dir:
        sys.exit("refusing to write outside APP_DIR: " + relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    written += 1

print("gatekeeper-bundle-unpack: wrote %d file(s) to %s" % (written, app_dir))
UNPACK_PY

cd "$APP_DIR"

# --- the one dependency-install site in this script -------------------
$PIP_INSTALL_CMD

# --- the one launch site in this script -------------------------------
exec $LAUNCH_CMD
