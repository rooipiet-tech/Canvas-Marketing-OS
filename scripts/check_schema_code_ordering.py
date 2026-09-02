#!/usr/bin/env python3
"""Refuse a change that adds a Vault column and the code writing it in one commit.

WHY THIS EXISTS
---------------
deploy-infra.yml rolls ca-vault onto its new image as part of
`az deployment group create` (line ~589), and only THEN starts
caj-vault-migrate (line ~605). main.bicep gives ca-vault
`revisionSuffix: 'r${uniqueString(deployToken)}'` with
`param vaultDeployToken string = utcNow()`, so a new revision -- new code --
is live and taking 100% of traffic (activeRevisionsMode Single) roughly
30-45 seconds before the ALTER TABLE that its columns depend on has run.

Measured on deploy-infra run 33654559138: `az deployment group create`
finished 16:31:47 and caj-vault-migrate had not reported Succeeded until
after 16:32:31.

In that window every write naming a new column fails. Reproduced against a
local Postgres by applying the pre-change schema, starting the vault from
the post-change tree and POSTing an opportunity card:

    asyncpg.exceptions.UndefinedColumnError:
        column "pillar" of relation "opportunity_cards" does not exist

...surfacing as a 500. For the orchestrator that is a dead-lettered task,
which in this system cascades to every dependent task in the loop.

WHY THE ORDERING IS NOT SIMPLY REVERSED
---------------------------------------
The migration cannot run before the deployment that rolls the app:

  * deploy-infra regenerates the Postgres admin password on every run
    ("Generate fresh Postgres admin password", openssl rand -hex 32). It
    only becomes live when ARM applies it, and caj-vault-migrate's own
    DATABASE_URL secret is set by that same deployment.
  * The job's schema arrives through the template too -- main.bicep's
    loadTextContent feeds migrationJob's `schemaSql` param.
  * Key Vault has public network access Disabled (L-0012), so CI cannot
    read a previously stored password instead of minting one.

So migration-after-rollout is a consequence of the VNet-isolation design,
not an oversight. Until that design changes, the safe way to add a column
is expand/contract across TWO deploys, which is what this check enforces:

    deploy 1 -- schema only. The column exists; nothing writes it yet.
    deploy 2 -- code. The column it writes is already there.

WHAT IT DETECTS
---------------
A column name that this change adds BOTH to contracts/vault-schema/schema.sql
(as `ALTER TABLE ... ADD COLUMN IF NOT EXISTS <name>`, the form that file's
own header requires, since CREATE TABLE IF NOT EXISTS is a no-op against a
live database) AND to services/vault/vault/models.py (as a new
`FieldSpec("<name>", ...)`). Only the intersection fails, so a schema-only
change and a code-only change both pass -- that is the whole point.

    python scripts/check_schema_code_ordering.py                # vs origin/main
    python scripts/check_schema_code_ordering.py --base HEAD~1
    python scripts/check_schema_code_ordering.py --self-test
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

SCHEMA_FILE = "contracts/vault-schema/schema.sql"
MODELS_FILE = "services/vault/vault/models.py"

# `ALTER TABLE opportunity_cards ADD COLUMN IF NOT EXISTS pillar text;`
ADD_COLUMN_RE = re.compile(
    r"^\+\s*ALTER\s+TABLE\s+(?P<table>\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(?P<column>\w+)\b",
    re.IGNORECASE,
)

# `            FieldSpec("pillar", "text", required=False, patchable=True),`
FIELDSPEC_RE = re.compile(r'^\+\s*FieldSpec\(\s*"(?P<column>\w+)"')


def added_columns(diff_text: str) -> set[str]:
    """Column names introduced by ADD COLUMN lines added in this diff."""
    return {
        m.group("column")
        for line in diff_text.splitlines()
        if (m := ADD_COLUMN_RE.match(line))
    }


def added_fieldspecs(diff_text: str) -> set[str]:
    """Column names introduced by FieldSpec entries added in this diff."""
    return {
        m.group("column")
        for line in diff_text.splitlines()
        if (m := FIELDSPEC_RE.match(line))
    }


def git_diff(base: str, path: str) -> str:
    """Added/removed lines for one path between `base` and the working tree.

    Uses the two-dot form so an out-of-date branch is compared against the
    base commit itself rather than a merge base that may already contain the
    column -- the check should fire on what this change introduces.
    """
    result = subprocess.run(
        ["git", "diff", "--unified=0", base, "--", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git diff against '{base}' failed -- is the ref fetched?\n{result.stderr.strip()}"
        )
    return result.stdout


def check(base: str) -> int:
    columns = added_columns(git_diff(base, SCHEMA_FILE))
    fields = added_fieldspecs(git_diff(base, MODELS_FILE))
    both = sorted(columns & fields)

    # Ground truth before the verdict, per L-0064: a reader must be able to
    # see WHAT was compared, not just the pass/fail it produced.
    print(f"base: {base}")
    print(f"columns added to {SCHEMA_FILE}: {sorted(columns) or '(none)'}")
    print(f"FieldSpecs added to {MODELS_FILE}: {sorted(fields) or '(none)'}")

    if not both:
        print("PASS: no column is added to the schema and written by the code in one change.")
        return 0

    named = ", ".join(both)
    print()
    print(f"FAIL: {named} added to BOTH the schema and models.py in this change.")
    print()
    print("deploy-infra rolls ca-vault onto the new image ~30-45s BEFORE")
    print("caj-vault-migrate applies the ALTER TABLE, so for that window the new")
    print("code is live against a database without these columns. Writes naming")
    print("them fail with asyncpg UndefinedColumnError -> 500 -> a dead-lettered")
    print("orchestrator task that cascades to its dependents.")
    print()
    print("Split it across two deploys (expand/contract):")
    print(f"  1. Land and deploy ONLY the {SCHEMA_FILE} change.")
    print(f"  2. Then land the {MODELS_FILE} change.")
    print()
    print("See this script's docstring for why the deploy ordering cannot")
    print("simply be reversed (rotated Postgres password + VNet-only Key Vault).")
    return 1


def self_test() -> int:
    """Prove the check can both pass and fail before it is trusted (L-0051)."""
    schema_add = (
        "@@ -95,0 +96,2 @@\n"
        "+ALTER TABLE opportunity_cards ADD COLUMN IF NOT EXISTS pillar text;\n"
        "+ALTER TABLE opportunity_cards ADD COLUMN IF NOT EXISTS so_what text;\n"
    )
    models_add = (
        "@@ -108,0 +109,1 @@\n"
        '+            FieldSpec("pillar", "text", required=False, patchable=True),\n'
    )

    assert added_columns(schema_add) == {"pillar", "so_what"}, added_columns(schema_add)
    assert added_fieldspecs(models_add) == {"pillar"}, added_fieldspecs(models_add)

    # FAILS: the same column on both sides of one change.
    assert added_columns(schema_add) & added_fieldspecs(models_add) == {"pillar"}

    # PASSES: schema-only. This is deploy 1 of the expand/contract split, and
    # it must not fail or the check forbids the very fix it recommends.
    assert not (added_columns(schema_add) & added_fieldspecs(""))

    # PASSES: code-only, the column having landed in an earlier deploy.
    assert not (added_columns("") & added_fieldspecs(models_add))

    # A REMOVED line is not an addition -- a dropped column must not trip this.
    removed = "-ALTER TABLE opportunity_cards ADD COLUMN IF NOT EXISTS pillar text;\n"
    assert not added_columns(removed), added_columns(removed)
    removed_fs = '-            FieldSpec("pillar", "text", required=False, patchable=True),\n'
    assert not added_fieldspecs(removed_fs), added_fieldspecs(removed_fs)

    # Context lines (no +/- prefix) are not additions either.
    context = " ALTER TABLE opportunity_cards ADD COLUMN IF NOT EXISTS pillar text;\n"
    assert not added_columns(context), added_columns(context)

    # The diff header naming the file starts with +++ and must not parse as a
    # FieldSpec or column addition.
    header = "+++ b/contracts/vault-schema/schema.sql\n"
    assert not added_columns(header) and not added_fieldspecs(header)

    print("self-test passed: detects the unsafe pair, and clears schema-only,")
    print("code-only, removals, context lines and diff headers.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="ref to compare against (default: origin/main)",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="verify the detector and exit"
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    return check(args.base)


if __name__ == "__main__":
    sys.exit(main())
