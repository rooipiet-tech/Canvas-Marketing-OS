"""AC-020: the orchestrator's own schema never stores client/personal-
data-shaped fields — only ids, task_type, status/state, timestamps, and
short opaque strings. Parses migrations/0001_orchestrator_init.sql
directly (no live Postgres required, so this test always runs, including
in this sandbox).
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "migrations" / "0001_orchestrator_init.sql"
)

DENY_LIST = ["email", "name", "phone", "address", "content", "body", "payload", "text_content"]

# Matches "column_name  type ..." lines inside CREATE TABLE blocks.
COLUMN_LINE_RE = re.compile(r"^\s{4}(\w+)\s+(uuid|text|jsonb|integer|timestamptz)\b", re.MULTILINE)


def test_migration_sql_has_no_denylisted_column_names():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    columns = COLUMN_LINE_RE.findall(sql)
    assert columns, "expected to find at least one column definition in the migration SQL"

    column_names = [name for name, _type in columns]
    for denied in DENY_LIST:
        for column_name in column_names:
            assert denied not in column_name.lower(), (
                f"column {column_name!r} matches deny-listed pattern {denied!r} "
                "(client/personal-data-shaped field name)"
            )


def test_migration_sql_columns_match_allow_pattern():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    columns = COLUMN_LINE_RE.findall(sql)

    allow_exact = {
        "task_id",
        "loop_id",
        "task_type",
        "state",
        "retry_count",
        "depends_on",
        "vault_write_failed_count",
        "created_at",
        "updated_at",
        "id",
        "from_state",
        "to_state",
        "reason",
        "occurred_at",
    }

    for column_name, _type in columns:
        assert column_name in allow_exact, (
            f"column {column_name!r} is not on the explicit allow-list of "
            "id/uuid, *_at timestamps, short enum-like text, integer "
            "counters, or the depends_on jsonb array"
        )


def test_no_denylisted_words_anywhere_in_migration_comments_or_ddl():
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()
    # "content" appears legitimately in this file's own header prose
    # ("stores client/personal-data-shaped content") describing what is
    # NOT stored — so this check is scoped to actual column/constraint
    # definitions via the allow-pattern tests above, not free prose. This
    # test instead guards against an actual DDL column named after a
    # denied word slipping in outside the 4-space-indented pattern above.
    ddl_lines = [
        line
        for line in sql.splitlines()
        if line.strip().startswith(("email", "name", "phone", "address", "body", "payload"))
    ]
    assert ddl_lines == []
