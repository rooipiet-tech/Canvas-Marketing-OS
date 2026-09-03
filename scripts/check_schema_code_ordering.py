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
from pathlib import Path

SCHEMA_FILE = "contracts/vault-schema/schema.sql"
MODELS_FILE = "services/vault/vault/models.py"

# `ALTER TABLE opportunity_cards ADD COLUMN IF NOT EXISTS pillar text;`
ADD_COLUMN_RE = re.compile(
    r"^\+\s*ALTER\s+TABLE\s+(?P<table>\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(?P<column>\w+)\b",
    re.IGNORECASE,
)

# `            FieldSpec("pillar", "text", required=False, patchable=True),`
# `CREATE TABLE IF NOT EXISTS profile_sources (`
#
# The ADD COLUMN guard above did not cover a whole new TABLE, and the race
# is identical: ca-vault rolls onto the new image before caj-vault-migrate
# runs, so code touching a table the migration has not created yet fails
# with asyncpg UndefinedTableError instead of UndefinedColumnError. Same
# 500, same dead-lettered task. Found while adding profile_sources, which
# would have sailed through the guard that exists to prevent exactly this.
CREATE_TABLE_RE = re.compile(
    r"^\+\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(?P<table>\w+)",
    re.IGNORECASE,
)

FIELDSPEC_INLINE_RE = re.compile(r'^\+\s*FieldSpec\(\s*"(?P<column>\w+)"')

# The wrapped form, which ruff forces once a declaration exceeds
# pyproject.toml's line-length = 100 (E501 is selected). The widest existing
# entry in models.py is already 97 characters, so the next column with a
# longer name or an extra kwarg has to wrap:
#
#     FieldSpec(
#         "new_column",
#         "text",
#     ),
#
# Matching only the inline form would return an empty set here and print
# PASS on exactly the pair this check exists to block -- a silent pass, the
# worst direction to fail in.
FIELDSPEC_OPEN_RE = re.compile(r"^\+\s*FieldSpec\(\s*(?:#.*)?$")
FIELDSPEC_NAME_RE = re.compile(r'^\+\s*"(?P<column>\w+)"')


def added_columns(diff_text: str) -> set[str]:
    """Column names introduced by ADD COLUMN lines added in this diff."""
    return {
        m.group("column")
        for line in diff_text.splitlines()
        if (m := ADD_COLUMN_RE.match(line))
    }


def added_tables(diff_text: str) -> set[str]:
    """Table names introduced by CREATE TABLE lines added in this diff."""
    return {
        m.group("table")
        for line in diff_text.splitlines()
        if (m := CREATE_TABLE_RE.match(line))
    }


def tables_referenced_in_code(diff_text: str, tables: set[str]) -> set[str]:
    """Which of `tables` this diff's ADDED code lines mention by name.

    Deliberately a plain name search rather than SQL parsing: vault code
    reaches a table through hand-written SQL strings, a FieldSpec table
    name and repo helpers, and a guard that only understood one of those
    would pass the other two. A false positive here costs one extra
    deploy; a false negative costs a crash-looping revision.
    """
    if not tables:
        return set()
    found: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("+++") or not line.startswith("+"):
            continue
        for table in tables:
            if re.search(rf"\b{re.escape(table)}\b", line):
                found.add(table)
    return found


def added_fieldspecs(diff_text: str) -> set[str]:
    """Column names introduced by FieldSpec entries added in this diff.

    Handles both the inline form and the wrapped form ruff produces past
    100 characters. Only ADDED (`+`) lines continue a wrapped declaration:
    a context or removed line ends it, so a reformat that leaves the name
    line unchanged cannot be misread as a new column.
    """
    found: set[str] = set()
    awaiting_name = False

    for line in diff_text.splitlines():
        # `+++ b/path` is a header, not an added line.
        if line.startswith("+++") or not line.startswith("+"):
            awaiting_name = False
            continue

        if m := FIELDSPEC_INLINE_RE.match(line):
            found.add(m.group("column"))
            awaiting_name = False
        elif FIELDSPEC_OPEN_RE.match(line):
            awaiting_name = True
        elif awaiting_name:
            if m := FIELDSPEC_NAME_RE.match(line):
                found.add(m.group("column"))
                awaiting_name = False
            elif ")" in line:
                # The call closed without a leading string literal -- not a
                # shape this file uses; stop rather than scan on forever.
                awaiting_name = False
            # Anything else (a comment line) keeps the declaration open.

    return found


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
    schema_diff = git_diff(base, SCHEMA_FILE)
    models_diff = git_diff(base, MODELS_FILE)

    columns = added_columns(schema_diff)
    fields = added_fieldspecs(models_diff)
    both = sorted(columns & fields)

    tables = added_tables(schema_diff)
    used_tables = sorted(tables_referenced_in_code(models_diff, tables))

    # Ground truth before the verdict, per L-0064: a reader must be able to
    # see WHAT was compared, not just the pass/fail it produced.
    print(f"base: {base}")
    print(f"columns added to {SCHEMA_FILE}: {sorted(columns) or '(none)'}")
    print(f"FieldSpecs added to {MODELS_FILE}: {sorted(fields) or '(none)'}")
    print(f"tables added to {SCHEMA_FILE}: {sorted(tables) or '(none)'}")
    print(f"...of those, referenced in {MODELS_FILE}: {used_tables or '(none)'}")

    if used_tables:
        named_tables = ", ".join(used_tables)
        print()
        print(f"FAIL: table(s) {named_tables} are CREATEd in the schema and used by")
        print(f"      {MODELS_FILE} in this same change.")
        print()
        print("Same race as a new column, one level up: ca-vault is live on the new")
        print("image ~30-45s before caj-vault-migrate creates the table, and code")
        print("touching a table that does not exist yet raises asyncpg")
        print("UndefinedTableError -> 500 -> a dead-lettered orchestrator task.")
        print()
        print("Split it across two deploys (expand/contract):")
        print(f"  1. Land and deploy ONLY the {SCHEMA_FILE} change.")
        print(f"  2. Then land the {MODELS_FILE} change.")
        return 1

    if not both:
        print("PASS: no column or table is added to the schema and used by the code in one change.")
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
    """Prove the check can both pass and fail before it is trusted (L-0051).

    Anchored to the REAL files, not only to fixtures. `git diff -- <path>`
    exits 0 with empty stdout for a pathspec matching nothing, so git_diff's
    returncode guard never fires: if either file is relocated or either path
    constant is mistyped, both sets come back empty, the intersection is
    empty, and check() prints PASS forever. Every degradation mode here
    points at PASS, so the fixtures alone would let this script report itself
    healthy while detecting nothing. Same approach as
    scripts/select_audit_lens.py, which anchors to the real lens files.
    """
    # 1. The targets still exist where the constants say they do.
    for path in (SCHEMA_FILE, MODELS_FILE):
        assert Path(path).exists(), (
            f"{path} does not exist -- the check would pass vacuously, because "
            "`git diff` against a pathspec matching nothing exits 0 with no output"
        )

    # 2. Each pattern still matches the real file's current contents. Every
    #    line is prefixed with '+' so the live file reads as an all-added
    #    diff; if the codebase reformats away from a shape a regex expects,
    #    this fails here rather than silently passing in CI.
    def as_added(text: str) -> str:
        return "".join(f"+{line}\n" for line in text.splitlines())

    live_columns = added_columns(as_added(Path(SCHEMA_FILE).read_text()))
    assert live_columns, (
        f"the ADD COLUMN pattern matched nothing in the live {SCHEMA_FILE} -- "
        "the file's shape changed and this check no longer detects anything"
    )

    live_fields = added_fieldspecs(as_added(Path(MODELS_FILE).read_text()))
    assert live_fields, (
        f"the FieldSpec pattern matched nothing in the live {MODELS_FILE} -- "
        "the file's shape changed and this check no longer detects anything"
    )

    # 3. The two really do describe the same columns, so an intersection is
    #    meaningful rather than accidentally always empty.
    assert live_columns & live_fields, (
        "no column name appears in both live files -- the two patterns are "
        "reading unrelated things and the intersection could never fire"
    )

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

    # The WRAPPED form ruff forces past 100 characters. Missing this was a
    # real defect in the first version of this script, caught in review: it
    # returned set(), so the check printed PASS on exactly the pair it
    # exists to block. A silent pass is the worst way for a guard to fail,
    # and self_test() covering only single-line fixtures let it call itself
    # healthy while detecting nothing.
    wrapped = (
        "@@ -108,0 +109,5 @@\n"
        "+            FieldSpec(\n"
        '+                "new_column",\n'
        '+                "text",\n'
        "+                required=False,\n"
        "+            ),\n"
    )
    assert added_fieldspecs(wrapped) == {"new_column"}, added_fieldspecs(wrapped)

    # ...including with a comment between the open paren and the name.
    wrapped_comment = (
        "+            FieldSpec(\n"
        "+                # added after v1 froze\n"
        '+                "commented_column",\n'
        "+            ),\n"
    )
    assert added_fieldspecs(wrapped_comment) == {"commented_column"}

    # A REMOVED wrapped declaration is not an addition.
    wrapped_removed = (
        "-            FieldSpec(\n"
        '-                "gone_column",\n'
        "-            ),\n"
    )
    assert not added_fieldspecs(wrapped_removed), added_fieldspecs(wrapped_removed)

    # A context line between the open paren and the name ends the
    # continuation, so an unchanged name cannot be read as newly added.
    wrapped_broken = (
        "+            FieldSpec(\n"
        '                 "unchanged_column",\n'
    )
    assert not added_fieldspecs(wrapped_broken), added_fieldspecs(wrapped_broken)

    # The `+++ b/path` header must not open a declaration or supply a name.
    assert not added_fieldspecs('+++ b/services/vault/vault/models.py\n')

    # The wrapped form must also fail the real check, not just parse.
    assert added_columns(
        "+ALTER TABLE opportunity_cards ADD COLUMN IF NOT EXISTS new_column text;\n"
    ) & added_fieldspecs(wrapped) == {"new_column"}

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

    # ------------------------------------------------------------------
    # CREATE TABLE, the case the ADD COLUMN guard did not cover.
    #
    # Anchored to the real schema first, for the same reason as the column
    # patterns above: every degradation mode points at PASS.
    # ------------------------------------------------------------------
    live_tables = added_tables(
        "".join(f"+{line}\n" for line in Path(SCHEMA_FILE).read_text(encoding="utf-8").splitlines())
    )
    assert len(live_tables) >= 9, (
        f"the CREATE TABLE pattern matched {len(live_tables)} table(s) in the live "
        f"{SCHEMA_FILE} -- it has drifted from the file it guards"
    )

    table_add = "+CREATE TABLE IF NOT EXISTS profile_sources (\n"
    assert added_tables(table_add) == {"profile_sources"}, added_tables(table_add)

    # The unsafe pair: table created here, and used by the code here.
    code_uses = '+    TABLE = "profile_sources"\n'
    assert tables_referenced_in_code(code_uses, {"profile_sources"}) == {"profile_sources"}

    # Also caught inside a hand-written SQL string, which is how vault code
    # reaches most tables -- a guard that only understood FieldSpec would
    # miss this entirely.
    code_sql = '+        await conn.fetch("SELECT id FROM profile_sources WHERE state = $1")\n'
    assert tables_referenced_in_code(code_sql, {"profile_sources"}) == {"profile_sources"}

    # Schema-only is the shape this check RECOMMENDS; it must not be blocked.
    assert not tables_referenced_in_code("", {"profile_sources"})

    # Code touching a table this diff did NOT create is ordinary work.
    assert not tables_referenced_in_code(code_uses, set())

    # Removals, context lines and the diff header are not additions -- the
    # same three false-positive shapes the column patterns are checked for.
    assert not added_tables("-CREATE TABLE IF NOT EXISTS gone_table (\n")
    assert not added_tables(" CREATE TABLE IF NOT EXISTS context_table (\n")
    assert not added_tables("+++ b/contracts/vault-schema/schema.sql\n")
    assert not tables_referenced_in_code(
        "+++ b/services/vault/vault/models.py profile_sources\n", {"profile_sources"}
    )

    print(
        f"self-test passed: all patterns still match the live files "
        f"({len(live_columns)} columns, {len(live_fields)} FieldSpecs, "
        f"{len(live_tables)} tables); detects the unsafe column pair inline AND "
        "wrapped, and a new table used in the same change; clears schema-only, "
        "code-only, removals, context lines and headers."
    )
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
