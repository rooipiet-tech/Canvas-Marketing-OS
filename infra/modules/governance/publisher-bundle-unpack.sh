#!/bin/sh
# Canvas Marketing OS - Publisher container bootstrap.
#
# THE single source of truth for how the Publisher source bundle is
# unpacked and the app is launched. This exact file is:
#
#   * loadTextContent()'d by infra/main.bicep and handed to
#     publisher-app.bicep as the container command - that .bicep file
#     reimplements none of it; and
#   * subprocess-executed verbatim by
#     scripts/verify_governance_bundle_reconstruction.py, so a bug here
#     fails locally instead of only at live deploy.
#
# Contract:
#   BUNDLE_B64_0..3   (required, at least one) base64 CHUNKS of the bundle
#                     JSON {path: content}, concatenated in order. Split
#                     across up to 4 env vars because a single one is a
#                     single execve() argument string, and Linux's
#                     MAX_ARG_STRLEN caps any ONE of those at 128 KiB
#                     (131072 bytes) regardless of the total argv+environ
#                     budget (getconf ARG_MAX, usually far larger) -- see
#                     gatekeeper-bundle-unpack.sh's header, where this
#                     exact ceiling broke a single-BUNDLE_B64 gatekeeper
#                     bundle at 27 files. Publisher was not yet over it
#                     but was already at 90432 of 131072 bytes, so this
#                     was fixed here too rather than waiting for the next
#                     publisher file to hit the same wall. Each chunk
#                     stays under 120000 bytes; unused trailing chunks
#                     are unset or empty, which concatenates to nothing
#                     extra.
#   APP_MODULE        (required) ASGI target, e.g. main:app
#   APP_DIR           (optional) where to unpack, default /app
#   PIP_INSTALL_CMD   (optional) dependency install command
#   LAUNCH_CMD        (optional) process to exec after install
#
# The last three exist so the verification script can reconstruct and
# import the bundle without installing packages or binding a port. The
# defaults are what actually runs in Container Apps.

set -eu

BUNDLE_B64="${BUNDLE_B64_0:-}${BUNDLE_B64_1:-}${BUNDLE_B64_2:-}${BUNDLE_B64_3:-}"
: "${BUNDLE_B64:?at least one of BUNDLE_B64_0..BUNDLE_B64_3 must be set (base64 chunks of the bundle JSON)}"
: "${APP_MODULE:?APP_MODULE must be set (e.g. main:app)}"

APP_DIR="${APP_DIR:-/app}"
BUNDLE_FILE="${BUNDLE_FILE:-/tmp/publisher-bundle.json}"
PIP_INSTALL_CMD="${PIP_INSTALL_CMD:-pip install --no-cache-dir --disable-pip-version-check -r requirements.txt}"
LAUNCH_CMD="${LAUNCH_CMD:-uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000}"

mkdir -p "$APP_DIR"
printf '%s' "$BUNDLE_B64" | base64 -d > "$BUNDLE_FILE"

APP_DIR="$APP_DIR" BUNDLE_FILE="$BUNDLE_FILE" python - <<'UNPACK_PY'
import json
import os
import pathlib
import sys

app_dir = pathlib.Path(os.environ["APP_DIR"]).resolve()
bundle_file = pathlib.Path(os.environ["BUNDLE_FILE"])
bundle = json.loads(bundle_file.read_text(encoding="utf-8"))

if not isinstance(bundle, dict) or not bundle:
    sys.exit("bundle JSON must be a non-empty object of {path: content}")

written = 0
for relative_path, content in sorted(bundle.items()):
    target = (app_dir / relative_path).resolve()
    if app_dir not in target.parents and target != app_dir:
        sys.exit("refusing to write outside APP_DIR: " + relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    written += 1

print("publisher-bundle-unpack: wrote %d file(s) to %s" % (written, app_dir))
UNPACK_PY

cd "$APP_DIR"

# --- the one dependency-install site in this script -------------------
$PIP_INSTALL_CMD

# --- the one launch site in this script -------------------------------
exec $LAUNCH_CMD
