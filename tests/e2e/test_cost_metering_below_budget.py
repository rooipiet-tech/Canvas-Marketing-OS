"""AC-10 — total model cost for one daily loop run is metered and below
the configured DAILY_LOOP_BUDGET_USD. Fails if zero cost rows are found
(metering silently didn't fire), not just on exceeding budget.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_SERVICE_ROOT = REPO_ROOT / "services" / "orchestrator"
if str(ORCHESTRATOR_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_SERVICE_ROOT))

from conftest import run_campaign_id  # noqa: E402
from orchestrator.config import DAILY_LOOP_BUDGET_USD  # noqa: E402


def test_run_total_usd_cost_is_metered_and_below_budget(
    live_daily_loop_run: dict[str, Any], live_db_url: str
) -> None:
    # envelope.campaign_id (uuid5) is only ever used as part of the REAL
    # Vault campaign's NAME (vault_client_ext.get_or_create_campaign,
    # "run-<this value>") -- it is NOT itself campaigns.id. Resolve the
    # real campaign row by that name first, then join agent_runs/costs on
    # its actual (server-generated) primary key.
    campaign_name = f"run-{run_campaign_id(live_daily_loop_run['heartbeat'])}"

    with psycopg.connect(live_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM campaigns WHERE name = %s", (campaign_name,))
            row = cur.fetchone()
            assert row is not None, f"no Vault campaign found named {campaign_name!r}"
            (real_campaign_id,) = row

            cur.execute(
                """
                SELECT count(*), coalesce(sum(c.amount), 0)
                FROM costs c
                JOIN agent_runs a ON a.id = c.agent_run_id
                WHERE a.campaign_id = %s AND c.unit = 'usd'
                """,
                (real_campaign_id,),
            )
            row_count, total_usd = cur.fetchone()

    assert row_count > 0, (
        f"zero usd costs rows found for campaign_id={real_campaign_id} -- metering silently "
        "didn't fire for this run"
    )
    assert float(total_usd) < DAILY_LOOP_BUDGET_USD, (
        f"total metered cost ${total_usd} for this run exceeds the configured budget "
        f"${DAILY_LOOP_BUDGET_USD} (DAILY_LOOP_BUDGET_USD)"
    )
