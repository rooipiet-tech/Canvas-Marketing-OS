from __future__ import annotations

from typing import Any

from .core import MAX_LINEAGE_HOPS

# ---------------------------------------------------------------------
# depends_on lineage resolution (shared by draft-brief and qa-review)
# ---------------------------------------------------------------------

def resolve_lineage_result(
    task_id: str, db: Any, *, max_hops: int = MAX_LINEAGE_HOPS
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Breadth-first walk up `task_id`'s depends_on ancestors until one
    carries a non-null result_ref, returning (that ancestor's task row,
    its result_ref). Transparently walks PAST an unhandled pass-through
    ancestor (e.g. daily-signal-loop.yaml's score-signals, which never
    gets a result_ref since it falls through to legacy_task_pass_through)
    -- draft-brief's immediate predecessor is 'score', but the real
    content it needs lives 2 hops back at 'ingest'. qa-review's own two
    loop positions each have a result_ref-bearing IMMEDIATE predecessor
    (draft-brief / draft-content), so this same walk resolves both in one
    hop for qa-review and two for draft-brief -- one mechanism, no
    per-loop-position special casing.
    """
    frontier = [task_id]
    seen = {task_id}
    for _ in range(max_hops):
        if not frontier:
            return None
        rows = {row["task_id"]: row for row in db.get_tasks(frontier)}
        next_frontier: list[str] = []
        for tid in frontier:
            row = rows.get(tid)
            if row is None:
                continue
            if tid != task_id and row.get("result_ref") is not None:
                return row, row["result_ref"]
            for dep in row.get("depends_on") or []:
                if dep not in seen:
                    seen.add(dep)
                    next_frontier.append(dep)
        frontier = next_frontier
    return None

