-- Proposal A prerequisite query (claude/qa-feedback-loop-proposal-2026-08-05.md):
-- pull Brand Steward QA (function 02) failures and their violation codes
-- over a period, for building docs/content-learnings.md entries.
--
-- Schema reference: services/vault/vault/models.py (agent_runs ObjectTypeConfig)
-- and services/vault/vault/routers/objects.py (fetch_object/list_objects show
-- the real join shape: agent_runs itself has no function_id column -- that
-- lives on vault_internal.object_taxonomy, joined on
-- object_table='agent_runs' AND object_id = agent_runs.id).
--
-- Params: $1 = period start (timestamptz), $2 = period end (timestamptz)

-- 1) Raw failed runs in the period, with their violations array intact.
SELECT
    o.id            AS agent_run_id,
    o.agent_name,
    o.status,
    o.output,
    o.created_at,
    t.campaign_id
FROM agent_runs o
LEFT JOIN vault_internal.object_taxonomy t
    ON t.object_table = 'agent_runs' AND t.object_id = o.id
WHERE t.function_id = '02-brand-steward-qa'
  AND o.status = 'failed'
  AND o.created_at >= $1
  AND o.created_at < $2
ORDER BY o.created_at DESC;

-- 2) Violation-code occurrence counts in the period (what content-learnings.md
--    entries are built from -- promote a code to "Accepted patterns" once it
--    crosses the 3-occurrence threshold with a common root cause).
SELECT
    violation,
    COUNT(*) AS occurrences
FROM agent_runs o
JOIN vault_internal.object_taxonomy t
    ON t.object_table = 'agent_runs' AND t.object_id = o.id
CROSS JOIN LATERAL jsonb_array_elements_text(o.output -> 'violations') AS violation
WHERE t.function_id = '02-brand-steward-qa'
  AND o.status = 'failed'
  AND o.created_at >= $1
  AND o.created_at < $2
GROUP BY violation
ORDER BY occurrences DESC;
