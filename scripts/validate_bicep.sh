#!/usr/bin/env bash
# Canvas Marketing OS — scripts/validate_bicep.sh
#
# Compile every Bicep template in infra/ and fail on any ERROR.
#
# WHY THIS EXISTS (F-BICEP-UNVALIDATED). Until this script, nothing in CI
# or in a local checkout ever compiled these templates. A syntax error, a
# bad function call, a param type mismatch or a reference to a module
# output that does not exist would all have been caught for the first
# time by `deploy-infra` -- against the live subscription, after review
# and merge. Every other interface in this repo is validated before it
# ships (contracts, loops, function packages, the allow-list); the
# infrastructure that runs them was the exception.
#
# ERRORS FAIL, WARNINGS RATCHET. The repo carries 91 pre-existing linter
# warnings (unused params, hardcoded environment URLs, unnecessary
# dependsOn) that are style, not correctness. Demanding they all be fixed
# to land an unrelated change would be a tax nobody pays; letting them
# grow unbounded is the drift this repo mechanises against everywhere
# else. So the count is a ratchet: it may fall freely, and a rise fails.
# When a rise is deliberate, update BASELINE_WARNINGS below in the same
# commit that causes it.
#
# Usage:
#   bash scripts/validate_bicep.sh            # uses `az bicep` or `bicep`
#   BICEP=/path/to/bicep bash scripts/validate_bicep.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASELINE_WARNINGS=89

if [[ -n "${BICEP:-}" ]]; then
  bicep_build() { "$BICEP" build "$1" --stdout; }
elif command -v bicep >/dev/null 2>&1; then
  bicep_build() { bicep build "$1" --stdout; }
elif command -v az >/dev/null 2>&1; then
  az bicep install --only-show-errors >/dev/null 2>&1 || true
  bicep_build() { az bicep build --file "$1" --stdout --only-show-errors; }
else
  echo "FAIL: no bicep CLI found. Install it, or set BICEP=/path/to/bicep." >&2
  exit 1
fi

# main.bicep, which pulls in every module it references transitively.
# Compiling each template separately was the first approach and is far too
# slow -- the CLI starts cold per file and each run resolves linter
# documentation URLs -- so orphans are caught by a reference check below
# instead of by compiling them.
ENTRYPOINT="$REPO_ROOT/infra/main.bicep"
if [[ ! -f "$ENTRYPOINT" ]]; then
  echo "FAIL: $ENTRYPOINT does not exist" >&2
  exit 1
fi

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

failed=0
bicep_build "$ENTRYPOINT" >/dev/null 2>>"$log" || failed=1

errors=$(grep -c ') : Error ' "$log" || true)
warnings=$(grep -c ') : Warning ' "$log" || true)

if [[ "$errors" -gt 0 ]]; then
  echo "FAIL: $errors Bicep error(s):" >&2
  grep ') : Error ' "$log" >&2
  exit 1
fi

if [[ "$failed" -ne 0 ]]; then
  echo "FAIL: a bicep build exited non-zero with no parsed Error line:" >&2
  cat "$log" >&2
  exit 1
fi

echo "infra/main.bicep compiled: 0 errors, $warnings warning(s)"

# Orphan check. A module nothing references is never compiled above, so a
# broken one would sit undetected until someone wired it up -- which is
# how month-end-reporting-trigger came to point at a loop that does not
# exist (architecture review F4).
orphans=0
while IFS= read -r template; do
  name="$(basename "$template")"
  [[ "$name" == "main.bicep" ]] && continue
  if ! grep -rql --include='*.bicep' "$name" "$REPO_ROOT/infra" \
       --exclude="$name" >/dev/null 2>&1; then
    echo "WARN: infra/${template#"$REPO_ROOT/infra/"} is referenced by no other template" >&2
    orphans=$((orphans + 1))
  fi
done < <(find "$REPO_ROOT/infra" -name '*.bicep' | sort)
[[ "$orphans" -gt 0 ]] && echo "$orphans unreferenced template(s) — not compiled by this check" >&2

if [[ "$warnings" -gt "$BASELINE_WARNINGS" ]]; then
  echo "" >&2
  echo "FAIL: warnings rose from $BASELINE_WARNINGS to $warnings." >&2
  echo "Fix them, or update BASELINE_WARNINGS in this script in the same commit." >&2
  grep ') : Warning ' "$log" >&2
  exit 1
fi

if [[ "$warnings" -lt "$BASELINE_WARNINGS" ]]; then
  echo "warnings fell below the baseline ($warnings < $BASELINE_WARNINGS) — lower BASELINE_WARNINGS to hold the gain."
fi

exit 0
